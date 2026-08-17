> Local evaluation only. This app has no authentication and must not be exposed to the public internet or an untrusted LAN. Docker Compose publishes ports on `127.0.0.1` only.

# InvzAssign Prompt-to-Animation MVP

One natural-language prompt becomes a Scene with exactly six five-second Cuts. Each Cut
generates versioned images and videos, retries bounded failures, keeps every previous
version, and the six selected videos play back in Cut order.

- Design spec: [`docs/superpowers/specs/2026-08-14-prompt-to-animation-design.md`](docs/superpowers/specs/2026-08-14-prompt-to-animation-design.md)
- Batch, character consistency, runtime mode, and webhook design: [`docs/superpowers/specs/2026-08-16-batch-character-consistency-design.md`](docs/superpowers/specs/2026-08-16-batch-character-consistency-design.md)
- Implementation plan: [`docs/superpowers/plans/2026-08-14-prompt-to-animation.md`](docs/superpowers/plans/2026-08-14-prompt-to-animation.md)
- AI coding agent instructions: [`AGENTS.md`](AGENTS.md) (and [`CLAUDE.md`](CLAUDE.md), which points to it)

Jump to [Quick start](#quick-start--mock-mode-no-api-keys), the
[demo walkthrough](#demo-walkthrough), or [Verification](#verification).

## Scope and safety

This is a trusted-local evaluation app. There is no login, no permission model, and no
per-user data isolation. Compose binds both published ports to the loopback interface, so
neither service is reachable from another machine. Do not deploy it to a public URL, a
shared LAN, or production.

`OPENAI_API_KEY` and `KIE_API_KEY` are read only from the backend environment. They are
never returned by an API, never sent to the frontend, never written to a tracked file, and
are redacted from logs together with `WEBHOOK_SECRET` and any `Authorization: Bearer …` header.

Redaction replaces the process log record factory rather than adding a filter to a logger. A
filter on the root logger is consulted only for records logged directly to root; records from a
child logger reach ancestor *handlers*, never ancestor filters. Uvicorn gives its own loggers
private handlers with `propagate = False` and leaves root without any, so a root-level filter
would never see the access line that carries the webhook token.

## Quick start — Mock mode (no API keys)

```bash
cp .env.example .env      # compose reads this; WEBHOOK_SECRET enables the callback route
docker compose up --build -d
# open http://localhost:5173
docker compose down
```

Without a `.env` the stack still runs; only `POST /api/webhooks/kie` stays disabled, so the
`SUCCEED_VIA_WEBHOOK` scenario is the one thing that cannot complete.

Compose defaults to `GENERATION_MODE=mock`, requires no API key, stores SQLite in the named
volume `backend-data`, and starts the frontend only after `GET /health` reports the backend
healthy. Mock generation results are served from the backend at `/media/mock/*`.

Give each mode its own database file when you run them as separate processes, so a Live run
never inherits Mock rows:

```dotenv
# Mock
DATABASE_URL=sqlite+aiosqlite:///./data/app-mock.db
```

A single process that switches mode at runtime necessarily keeps both modes in one file. That
is fine — every job records the mode that produced it, and mixing artifacts across modes is
refused with `409 ARTIFACT_MODE_MISMATCH`.

### Running without Docker

```bash
# backend
cd backend
python -m pip install -e ".[dev]"
alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

# frontend (second shell)
cd frontend
npm ci
npm run dev
```

## Live mode

Live calls OpenAI for Scene drafting and Kie for image and image-to-video generation.

```dotenv
# Live — set the two keys in your own environment, never in a tracked file
GENERATION_MODE=live
OPENAI_API_KEY=
KIE_API_KEY=
DATABASE_URL=sqlite+aiosqlite:///./data/app-live.db
```

**Live spends money and the app has no cost guard.** "Generate all images" followed by
"Generate all videos" is twelve real provider calls from two clicks, with no confirmation step
and no usage counter. Use single-cut buttons when checking Live.

`GENERATION_MODE` sets the mode the process **starts** in. `PUT /api/config` changes it at
runtime, and each job follows the mode snapshot taken when it was created — see
[Runtime Mock/Live switching](#runtime-mocklive-switching).

The most useful configuration for a review is **`GENERATION_MODE=mock` with both keys present**:
the app starts safe, nothing bills, and the Live button in the header is enabled so the runtime
switch can be demonstrated on purpose rather than by accident.

```dotenv
GENERATION_MODE=mock        # start in Mock
OPENAI_API_KEY=…            # present, so `liveAvailable` is true and Live is selectable
KIE_API_KEY=…
```

Startup refuses to run only when `GENERATION_MODE=live` and a key is missing, so a process that
is meant to be Live can never quietly serve Mock data.

Because one process owns one database, artifacts from both modes land in the same file once you
switch at runtime. That is intended, and mixing them is refused explicitly — see
[Cross-mode artifacts](#cross-mode-artifacts).

Models are fixed: `gpt-5.4-mini` (Scene), `google/nano-banana` (image), and
`kling-2.6/image-to-video` (video).

## Environment variables

Copy [`.env.example`](.env.example). It carries variable names, non-secret defaults, and one
local-demo `WEBHOOK_SECRET` that authenticates callbacks to this app only — never a credential
for any external service. The two API keys are left empty there on purpose.

| Variable | Default | Meaning |
|---|---|---|
| `GENERATION_MODE` | `mock` | `mock` or `live`, fixed at startup |
| `OPENAI_API_KEY` | *(empty)* | Backend-only secret; required in Live |
| `KIE_API_KEY` | *(empty)* | Backend-only secret; required in Live |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/app-mock.db` | SQLite file; use a separate file per mode |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | The single allowed CORS origin |
| `GENERATION_MAX_ATTEMPTS` | `3` | Attempts per generation job before final failure |
| `GENERATION_CONCURRENCY` | `3` | Jobs the worker may hand to providers at once |
| `RETRY_BASE_DELAY_SEC` | `1` | Base of the `base * 2^(attempt-1)` backoff |
| `PROVIDER_POLL_INTERVAL_SEC` | `1` | Worker idle interval and next-poll delay |
| `GENERATION_ATTEMPT_TIMEOUT_SEC` | `120` | Deadline for one accepted provider task |
| `WEBHOOK_SECRET` | *(empty)* | Shared secret for `POST /api/webhooks/kie`; empty disables the route. Compose reads it from the root `.env` rather than carrying a literal |
| `WEBHOOK_PUBLIC_URL` | *(empty)* | Public callback URL sent to Kie as `callBackUrl`; must end in `?token=<WEBHOOK_SECRET>` or every Live callback is rejected |
| `SELF_BASE_URL` | `http://127.0.0.1:8000` | Where Mock mode posts its own simulated callbacks |
| `MOCK_WEBHOOK_DELAY_SEC` | `1` | Simulated provider latency before a Mock callback |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Frontend build-time backend URL (public, not a secret) |

## Architecture

```text
React SPA
   │ REST + 1s polling while any job is nonterminal
   ▼
FastAPI (1 process, 1 Uvicorn worker)
   ├─ scenes.py       routes + short transactions
   ├─ generations.py  routes + short transactions
   ├─ anchor.py       the scene-anchor gate, as one pure function
   ├─ prompting.py    deterministic prompt composition, pure
   ├─ providers/      Mock and Live adapters, resolved per job from its mode snapshot
   └─ GenerationWorker (1 coroutine, up to GENERATION_CONCURRENCY jobs per run_once)
             │
             ▼
           SQLite
```

External async completion is driven by Polling, with an optional provider webhook that feeds
the same state machine. There is no Redis/Celery/RabbitMQ, no object storage, and no FFmpeg.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/config` | Current generation mode and whether Live is available |
| `PUT` | `/api/config` | Switch the generation mode at runtime |
| `POST` | `/api/scenes` | Create a Scene and six Cuts (`201`) |
| `GET` | `/api/scenes/{id}` | Cuts, jobs, artifacts, selections |
| `POST` | `/api/cuts/{id}/images` | Generate or regenerate an image (`202`) |
| `POST` | `/api/cuts/{id}/videos` | Generate or regenerate a video (`202`) |
| `POST` | `/api/scenes/{id}/images` | Batch: one image job per Cut (`202`) |
| `POST` | `/api/scenes/{id}/videos` | Batch: one video job per Cut with a selected image (`202`) |
| `POST` | `/api/webhooks/kie` | Provider callback, shares the polling state machine |
| `PUT` | `/api/cuts/{id}/selected-image` | Select a successful image |
| `PUT` | `/api/cuts/{id}/selected-video` | Select a video built from the selected image |
| `GET` | `/health` | Container health check |

Every application error uses one shape, without stack traces, provider bodies, or request
headers:

```json
{ "code": "STABLE_APPLICATION_CODE", "message": "User-safe explanation" }
```

### Polling state machine

```text
QUEUED
  └─ SUBMITTING
       ├─ accepted ──→ PROCESSING ──→ SUCCEEDED
       │                   ├─ still pending ──→ PROCESSING
       │                   └─ retryable task failure / deadline ──→ RETRY_WAIT
       ├─ clearly retryable rejection ──→ RETRY_WAIT
       ├─ uncertain submission ──→ FAILED
       └─ permanent error ──→ FAILED

RETRY_WAIT ── due and attempts remain ──→ SUBMITTING
```

### Failure classification

- **Retryable** — connect failure before the request body is sent, an explicit HTTP 429 or
  5xx, a provider task failure flagged retryable, or an accepted task that passes
  `GENERATION_ATTEMPT_TIMEOUT_SEC`. Retries stop after `GENERATION_MAX_ATTEMPTS` and use
  `RETRY_BASE_DELAY_SEC * 2^(attempt_count - 1)`; a numeric `Retry-After` wins when larger.
- **Permanent** — HTTP 400/401/403/404/422, schema violations, and unknown provider states
  fail immediately.
- **Uncertain submission** — a read timeout *after* the POST body was transmitted becomes
  `SUBMISSION_UNCERTAIN` and is never resubmitted, because the provider offers no
  idempotency key and a blind retry could bill twice. A `SUBMITTING` row left behind by a
  restart is recovered the same way.

A transient failure while polling an already-accepted task reschedules the poll and does
not consume an attempt. Mock retry behaviour is decided by the persisted `attempt_count`,
never by an in-memory counter.

Scene drafting adds a fourth class. When the model answers in a shape the schema rejects, that
is a **retryable** `SchemaProviderError`, not a permanent failure: nothing was created upstream,
so re-asking duplicates nothing, and a model that got the shape wrong once usually gets it right
next time. Retrying only transport errors would have spent the budget on the rarer problem.
Exhausting the attempts reports `SCENE_SCHEMA_INVALID` rather than an outage code, so the two
causes stay distinguishable.

### Cross-mode artifacts

An artifact is only usable by the provider that produced it: a Mock image is a path this app
serves, a Live image is a URL on the provider's CDN. Requesting a Live video over a Mock image
therefore returns `409 ARTIFACT_MODE_MISMATCH` at job creation, instead of letting the request
builder fail anonymously inside the provider three steps later. The guard matters because
switching modes mid-scene is the natural way to demonstrate the runtime switch.

### Batch generation

`POST /api/scenes/{id}/images` and `.../videos` enqueue one job per Cut in a single request and
stamp them with a shared batch id. A Cut that cannot start — an active job, or a video with no
selected image — is reported under `skipped` while the rest proceed; one unusable Cut must not
cancel the other five.

The worker, not the batch, owns concurrency. Each `run_once()` claims up to
`GENERATION_CONCURRENCY` due jobs **inside one transaction**, then fans out only the provider
calls with `asyncio.gather`. Serial claiming makes double-claiming structurally impossible and
keeps SQLite write locks short; parallelism is applied where the latency actually is.

Claiming walks the queue by keyset pages rather than one capped fetch, because anchor-gated
jobs are skipped without being written: a single bounded read could fill up entirely with gated
jobs and hide every runnable job behind them.

Batch progress in the UI is derived from the jobs themselves rather than stored on the batch
row, so the displayed counts can never disagree with the state machine.

### Character consistency

Cuts drift apart when each prompt is written independently, so the model does not write the
final prompts at all. Scene creation produces:

1. a **character sheet** — the model is asked for two to four recurring characters, each
   described on fixed axes; the schema accepts one to four so a single-protagonist prompt
   produces a scene instead of a 502
   (hair colour, hair style, outfit, build, face impression, signature prop);
2. per-Cut **shot descriptions** that state framing and action and are explicitly forbidden
   from restating appearance.

`app/prompting.py` then composes every Cut prompt from one template:

```text
<style guide>. Characters, keep identical in every shot: <character sheet>. Shot: <shot>. Avoid: <negative guide>
```

Composition is a pure function, so all six Cuts carry a byte-identical character section, and a
regenerate reuses the exact prompt that produced the earlier version. The composed prompts are
stored on the Cut, which is also what the UI shows as the generation input.

On top of that, Cut 1's **selected image becomes the scene anchor**: Cuts 2-6 send it to the
image model as `image_urls` so the same faces are redrawn rather than reinvented. Image batches
therefore run in two phases — Cut 1 alone, then the rest in parallel.

The gate holds Cuts 2-6 for as long as an anchor could still arrive, including when Cut 1 was
never requested at all — pressing a single cut's button outside a batch must not quietly produce
an unanchored image. A held job reports `waitingForAnchor`, and the UI says which cut it is
waiting on, so nothing stalls without an explanation. The one release is Cut 1 exhausting its
retries: a permanently failed anchor opens the gate rather than stalling the scene forever.

### Visual style

One style guide and one negative guide are injected into every image and video prompt, and the
model is instructed never to mention medium or realism itself. The target is a stylized, cute,
softly shaded animation look; `photorealistic`, `live action`, and `3D render` appear only
inside the negative guide, which is asserted by test.

### Runtime Mock/Live switching

`PUT /api/config {"generationMode":"LIVE"}` flips the mode without a restart. Both provider
pairs are built at startup and a registry resolves them, so nothing above the registry branches
on mode. `GET /api/config` reports `liveAvailable`, and switching to Live without keys returns
`409 LIVE_MODE_UNAVAILABLE`.

Every job stores the mode in force when it was created, and the worker resolves its provider
from **that snapshot**. Flipping the switch therefore never hands an in-flight Live task to the
Mock provider. The mode lives in process memory, so a restart returns to `GENERATION_MODE`.

### Webhook

`POST /api/webhooks/kie` is enabled only when `WEBHOOK_SECRET` is set. The payload is normalized
by the same function polling uses and applied by the same worker transition, so a callback and a
poll of the same task produce identical results.

The secret may arrive either as the `X-Webhook-Secret` header or as a `?token=` query
parameter, both compared in constant time. Two channels rather than one because **a provider
cannot be told to send a custom header** — Kie receives a callback URL and nothing else. So Live
callbacks carry the secret in the URL:

```dotenv
WEBHOOK_PUBLIC_URL=https://<your-tunnel>/api/webhooks/kie?token=<WEBHOOK_SECRET>
```

That token then appears in access logs, which is why `WEBHOOK_SECRET` is registered for log
redaction alongside the two API keys.

Idempotency comes from the transition itself: every branch re-reads the job inside its
transaction and does nothing unless it is still `PROCESSING`. A duplicate delivery, or a poll
racing a callback, therefore creates no second artifact. Unknown or already-finished tasks get
`200 {"status":"ignored"}` rather than an error, because a 4xx would make the provider retry a
delivery that can never change anything.

In Mock mode the `SUCCEED_VIA_WEBHOOK` scenario makes the provider post a real callback to
`SELF_BASE_URL`, while its polling deliberately never succeeds — so the job can only finish if
the webhook route works.

A provider that pushes its own result hands the delivery back on `Submission.on_processing`
instead of sending it during `submit()`; the worker fires it only after the job is committed as
`PROCESSING`. Otherwise a fast callback races that very commit, finds no job to apply itself to,
and the job idles until its attempt deadline expires.

### Regeneration and active-job conflict

A Cut allows at most one active job per kind. The rule is enforced by a partial unique
index, so two concurrent requests produce exactly one `202` and one
`409 GENERATION_ALREADY_ACTIVE`:

```sql
CREATE UNIQUE INDEX uq_active_generation_per_cut_kind
ON generation_jobs (cut_id, kind)
WHERE status IN ('QUEUED', 'SUBMITTING', 'PROCESSING', 'RETRY_WAIT');
```

Regenerating means calling the same endpoint again after the previous job reached a
terminal state. It creates the next version and never deletes or overwrites earlier jobs or
artifacts. A video job pins the image selected at request time as its `source_image_id`.
Selecting a different image clears `selected_video_id`, and only a video produced from the
currently selected image can be selected. The first successful artifact is auto-selected
only while no selection exists; an explicit user selection is never overwritten.

There is no HTTP replay idempotency key; duplicate work is prevented by the database
constraint above only.

### CORS and Mock media

CORS allows exactly one origin, `FRONTEND_ORIGIN`, with credentials disabled and only
`GET`, `POST`, `PUT`, and `OPTIONS`. Any other origin gets no
`access-control-allow-origin` header. Mock fixtures are mounted with FastAPI `StaticFiles`
at `/media/mock` and served as real `image/png` and `video/mp4` responses.

## Accepted trade-offs

These are deliberate MVP limits, not production guarantees.

- **No authentication.** Mitigated only by loopback-bound ports and the trusted-local scope.
- **Nominal 30 seconds.** Each Cut requests five seconds, but generated media is never
  inspected or trimmed. The sequence is six requested five-second Cuts, not a guaranteed
  30-second runtime.
- **No combined MP4.** Cuts play one after another in the browser. There is no
  concatenation, no download, and no FFmpeg.
- **Provider URLs may expire.** Live results are stored as provider URLs; the app runs no
  object storage of its own.
- **Single process only.** One backend process, one Uvicorn worker, one worker coroutine.
  Horizontal scaling and distributed locking are out of scope.
- **Ambiguous submissions fail closed.** See `SUBMISSION_UNCERTAIN` above: the app prefers an
  explicit failure over a possible double charge.
- **No Scene/Cut editing** and no orchestration of several prompts at once.
- **The anchor reference depends on the image model honouring `image_urls`.** The request
  shape is pinned by an HTTP contract test, but its effect on the output was never verified
  against the live model. If the model ignores the reference, consistency degrades to what
  the character sheet alone achieves.
- **The runtime mode is in-memory.** Correct for one process; it would need shared state the
  moment a second process exists.

## Demo walkthrough

Six clicks that exercise every requirement, in Mock mode, spending nothing. Start the stack,
open http://localhost:5173, and go in order.

**1 — Prompt to six cuts.** Enter any prompt and press **Create scene**. Six Cut cards appear,
each `5 sec`, and a **Characters in every cut** panel shows the recurring cast the model
invented. The scene id is in the URL, so a refresh restores the same workspace.

**2 — Batch image generation.** Press **Generate all images**. One request enqueues six jobs;
`Images 0/6 done` climbs as the worker drains them at most `GENERATION_CONCURRENCY` at a time.

**3 — Character consistency.** Open any Cut's image history. Every cut's prompt carries the same
character section, and Cuts 2-6 additionally show `Reference  Image v1` — Cut 1's selected image
sent to the model as the scene anchor. Cut 1 has no reference, because it *is* the anchor.

To see the gate refuse to guess: create a **new** scene, leave Cut 1 alone and press
**Generate image** on Cut 3 only. The job holds at `Queued` and says *"Waiting for the Cut 1
image so this cut keeps the same characters."* Generate Cut 1 and it releases by itself.

**4 — Retry, final failure, and regenerate history.** Set a Cut's **Mock scenario** dropdown to
`Always fail` and generate: the job retries with backoff and settles at
`Failed after 3/3 attempts`. Switch the dropdown to `Success` and press **Regenerate** — v2
succeeds and **v1 stays in the history** with its failure reason. `Fail twice, then succeed`
shows the middle case.

**5 — Webhook instead of polling.** Set the dropdown to `Succeed via webhook` and generate.
Mock's polling for this scenario never returns success on purpose, so the job can only finish if
the callback route works. The backend log shows exactly one `POST /api/webhooks/kie`.

**6 — Runtime mode switch and playback.** The header switch flips Mock/Live without a restart;
Live is selectable only when both keys are configured. Requesting a Live video over an image
generated in Mock returns `409 ARTIFACT_MODE_MISMATCH` rather than failing inside the provider.
Finally press **Generate all videos**, wait for `6 of 6 videos ready`, and **Play sequence**
plays the six selected videos in Cut order.

Every job card shows what produced it: the composed prompt, `MOCK`/`LIVE`, attempt count, source
image for a video, reference image for an image, and the failure reason when there is one.

## Verification

```bash
# backend
cd backend
ruff check app tests
mypy app
python -m pytest tests -v --cov=app

# frontend
cd frontend
npm run lint
npm run test:run
npm run build

# Compose
cd ..
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose logs backend --no-color

# end-to-end (Mock stack must already be running)
cd frontend
npx playwright install chromium
npm run test:e2e
# point somewhere else when 5173 is taken:
#   E2E_BASE_URL=http://localhost:5174 npm run test:e2e

# secret and removed-scope scans; both must print nothing.
# README.md and the plan are excluded because they quote the search pattern itself.
cd ..
git grep -n -E "(sk-[A-Za-z0-9_-]{16,}|Bearer [A-Za-z0-9_-]{16,}|OPENAI_API_KEY=.+|KIE_API_KEY=.+)" \
  -- . ":(exclude).env.example" ":(exclude)README.md" \
  ":(exclude)docs/superpowers/plans/2026-08-14-prompt-to-animation.md"
git grep -n -E "runtime-mode|RuntimeSetting|completion_method|WEBHOOK_SIGNING_SECRET|/api/webhooks|generations/batch|BatchService" \
  -- backend frontend
```

No automated test reaches the real OpenAI or Kie API. `pytest-socket` blocks every host
except loopback, and provider contracts are pinned with `respx` HTTP stubs.

## Requirement traceability

| ID | Requirement | Evidence |
|---|---|---|
| `REQ-01` | One prompt creates a Scene | `cd backend && pytest tests/test_scenes.py`; `cd frontend && npm run test:run src/app/App.test.tsx`; E2E |
| `REQ-02` | Exactly six Cuts, each five seconds | `cd backend && pytest tests/test_scenes.py tests/test_core.py`; E2E |
| `REQ-03` | Per-Cut image generation and selection | `cd backend && pytest tests/test_generations.py`; `cd frontend && npm run test:run src/features/generations/CutCard.test.tsx`; E2E |
| `REQ-04` | Video generated from the selected image | `cd backend && pytest tests/test_generations.py tests/test_worker.py`; E2E |
| `REQ-05` | Retry, progress, and final failure | `cd backend && pytest tests/test_worker.py`; `cd frontend && npm run test:run src/features/generations/CutCard.test.tsx`; E2E |
| `REQ-06` | Regeneration preserves earlier results | `cd backend && pytest tests/test_generations.py`; E2E |
| `REQ-07` | Six selected videos play in order | `cd frontend && npm run test:run src/features/player/SequencePlayer.test.tsx`; E2E |
| `REQ-08` | Demo without external APIs | `cd backend && pytest tests`; `docker compose up --build -d` then E2E |
| `REQ-09` | Real OpenAI and Kie integration | `cd backend && pytest tests/providers` |
| `REQ-10` | API keys are never exposed | `cd backend && pytest tests/test_core.py -k secret`; the secret scan above |
| `REQ-11` | Reproducible local run | `docker compose config`; `cd backend && pytest tests/test_core.py -k "cors or media"` |
| `REQ-12` | Batch generation with bounded concurrency | `cd backend && pytest tests/test_batches.py tests/test_worker.py`; `cd frontend && npm run test:run src/features/scene/BatchControls.test.tsx`; E2E |
| `REQ-13` | Same characters across all six cuts | `cd backend && pytest tests/test_prompting.py tests/test_scenes.py`; `pytest tests/test_worker.py -k anchor`; E2E |
| `REQ-14` | Animated, non-photoreal style | `cd backend && pytest tests/test_prompting.py`; E2E |
| `REQ-15` | Runtime Mock/Live switching | `cd backend && pytest tests/test_core.py -k "config or mode or live"`; `cd frontend && npm run test:run src/app/App.test.tsx` |
| `REQ-16` | Polling and webhook on one state machine | `cd backend && pytest tests/test_webhooks.py`; E2E |
| `REQ-17` | Full input/output traceability | `cd backend && pytest tests/test_generations.py`; `cd frontend && npm run test:run src/features/generations/CutCard.test.tsx` |
