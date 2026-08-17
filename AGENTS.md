# Agent instructions

Working rules for AI coding agents in this repository. They are not style preferences: each one
exists because breaking it produced a real defect here, and the defect is named so the rule can
be argued with rather than obeyed blindly.

Claude Code reads `CLAUDE.md`, which points here. Other tools read this file directly.

## What this project is

One natural-language prompt becomes a Scene with exactly six five-second Cuts. Each Cut
generates versioned images, then videos from a selected image, and the six selected videos play
back in order. A single FastAPI process owns one SQLite database and one generation worker
coroutine. There is no broker, no object storage, no authentication — the app is local-only by
design, and that constraint is what makes the simple designs below correct.

Read `README.md` for the architecture and `docs/superpowers/specs/` for the design decisions
behind it before proposing a change to either.

## Non-negotiables

**API keys live in the backend environment and nowhere else.** Never return one from an
endpoint, never pass one to the frontend, never write one to a tracked file, never put one in a
log line. `frontend/` may read `VITE_API_BASE_URL` and nothing else. Before committing, the
secret scan in the Verification section of `README.md` must print nothing.

**Tests come before implementation.** Write the focused failing test, run it, read the failure
and confirm it is the failure you meant to cause, then write the smallest code that passes, then
run the affected suite. A test written after the fact proves the code runs, not that it works.

**Verify with output, not with reasoning.** Paste the command and its result. This rule was
added after a redaction change was reported as verified on the strength of a log line that
turned out to be a logging *error* dump — the string looked right and the mechanism was broken.

**A superseded decision is edited, never left standing.** When a change contradicts something in
`README.md` or a design doc, update that text in the same commit and say what replaced it. The
repository already carries the cost of the alternative: for a while the README stated there was
no runtime mode switch while the endpoint that switches modes was listed forty lines below.

## Design rules that hold here

**Derive state; do not store it twice.** Batch progress comes from the jobs, not from a status
column on the batch. Anything denormalized has to be re-synchronised on every transition and
becomes the source of the next inconsistency bug.

**One rule, one function, two callers.** The scene-anchor gate lives in `app/anchor.py` as a
pure function because the worker enforces it and the scene response explains it. Two
implementations of one rule drift; that is how a cut generated outside a batch silently skipped
the anchor.

**Claiming is serial, provider calls are concurrent.** `GenerationWorker.run_once()` claims up
to `GENERATION_CONCURRENCY` jobs inside one transaction, then fans out only the network calls.
Parallelising the claim would make double-claiming possible again, and no current test would
notice — if you change it, add that test first.

**Every state transition re-reads the job inside its transaction and bails unless it is still
`PROCESSING`.** This is the whole of the webhook/polling idempotency argument. A branch that
skips the re-read creates duplicate artifacts under a race that happens in practice.

**Prompt composition is a pure function of stored data.** The model supplies a character sheet
and per-cut shot descriptions; `app/prompting.py` splices them into a fixed template. Character
consistency is enforced by structure, not by asking the model to be consistent. Never move
prompt assembly into the provider or the route.

**An artifact belongs to the mode that produced it.** A Mock image is a path this app serves; a
Live image is a URL on the provider's CDN. Crossing them is refused at job creation with
`409 ARTIFACT_MODE_MISMATCH`, not left to fail anonymously inside the request builder.

**Errors use one envelope.** `{"code": "STABLE_CODE", "message": "user-safe text"}` with no
stack trace, provider body, or request header. New codes go in the README table.

## Conventions

- Python 3.13, FastAPI, SQLAlchemy 2.x async, Alembic, pytest. `ruff` and `mypy --strict` must
  both pass.
- React 19 + TypeScript + Vite + TanStack Query + Vitest + Playwright. `eslint`, `tsc`, and the
  build must pass.
- Schema changes need an Alembic migration, and the migration must survive
  `upgrade → downgrade → upgrade`.
- Comments explain **why**, never what the line already says. If a comment restates the code,
  delete it. If a decision took a paragraph to reach, that paragraph belongs next to the code.
- Commit messages explain the defect and the reasoning, not the diff. One coherent change per
  commit.

## Before claiming a task is done

```bash
cd backend && ruff check app tests && mypy app && python -m pytest tests -q
cd ../frontend && npm run lint && npm run test:run && npm run build
# with the Mock stack running:
npm run test:e2e
```

Zero failures, and the secret scan in `README.md` prints nothing. If any step was skipped, say
which one and why rather than reporting success.
