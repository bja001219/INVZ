> Local evaluation only. This app has no authentication and must not be exposed to the public internet or an untrusted LAN. Docker Compose publishes ports on `127.0.0.1` only.

# InvzAssign Prompt-to-Animation MVP

One natural-language prompt becomes a Scene with exactly six five-second Cuts. Each Cut
generates versioned images and videos, retries bounded failures, keeps every previous
version, and the six selected videos play back in Cut order.

- Design spec: [`docs/superpowers/specs/2026-08-14-prompt-to-animation-design.md`](docs/superpowers/specs/2026-08-14-prompt-to-animation-design.md)
- Batch, character consistency, runtime mode, and webhook design: [`docs/superpowers/specs/2026-08-16-batch-character-consistency-design.md`](docs/superpowers/specs/2026-08-16-batch-character-consistency-design.md)
- Implementation plan: [`docs/superpowers/plans/2026-08-14-prompt-to-animation.md`](docs/superpowers/plans/2026-08-14-prompt-to-animation.md)

## Scope and safety

This is a trusted-local evaluation app. There is no login, no permission model, and no
per-user data isolation. Compose binds both published ports to the loopback interface, so
neither service is reachable from another machine. Do not deploy it to a public URL, a
shared LAN, or production.

`OPENAI_API_KEY` and `KIE_API_KEY` are read only from the backend environment. They are
never returned by an API, never sent to the frontend, never written to a tracked file, and
are redacted from logs together with any `Authorization: Bearer …` header.

## Quick start — Mock mode (no API keys)

```bash
docker compose up --build -d
# open http://localhost:5173
docker compose down
```

Compose defaults to `GENERATION_MODE=mock`, requires no API key, stores SQLite in the named
volume `backend-data`, and starts the frontend only after `GET /health` reports the backend
healthy. Mock generation results are served from the backend at `/media/mock/*`.

Mock and Live must not share a database file. Compose uses the Mock URL below; use a
different file name when you run Live.

```dotenv
# Mock
DATABASE_URL=sqlite+aiosqlite:///./data/app-mock.db
```

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
Startup fails if either key is missing, so a misconfigured Live process never runs against
Mock data.

```dotenv
# Live — set the two keys in your own environment, never in a tracked file
GENERATION_MODE=live
OPENAI_API_KEY=
KIE_API_KEY=
DATABASE_URL=sqlite+aiosqlite:///./data/app-live.db
```

`GENERATION_MODE` is read once at startup. There is no runtime mode switch and no endpoint
that mutates it; `GET /api/config` returns only `{"generationMode": "MOCK" | "LIVE"}`.

Models are fixed: `gpt-5.4-mini` (Scene), `google/nano-banana` (image), and
`kling-2.6/image-to-video` (video).

## Environment variables

Copy [`.env.example`](.env.example). It contains names and non-secret defaults only.

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
| `WEBHOOK_SECRET` | *(empty)* | Shared secret for `POST /api/webhooks/kie`; empty disables the route |
| `WEBHOOK_PUBLIC_URL` | *(empty)* | Public callback URL sent to Kie as `callBackUrl` |
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
   ├─ providers/      Mock or Live adapter, chosen once at startup
   └─ GenerationWorker (1 coroutine, at most one due job per run_once)
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

### Batch generation

`POST /api/scenes/{id}/images` and `.../videos` enqueue one job per Cut in a single request and
stamp them with a shared batch id. A Cut that cannot start — an active job, or a video with no
selected image — is reported under `skipped` while the rest proceed; one unusable Cut must not
cancel the other five.

The worker, not the batch, owns concurrency. Each `run_once()` claims up to
`GENERATION_CONCURRENCY` due jobs **inside one transaction**, then fans out only the provider
calls with `asyncio.gather`. Serial claiming makes double-claiming structurally impossible and
keeps SQLite write locks short; parallelism is applied where the latency actually is.

Batch progress in the UI is derived from the jobs themselves rather than stored on the batch
row, so the displayed counts can never disagree with the state machine.

### Character consistency

Cuts drift apart when each prompt is written independently, so the model does not write the
final prompts at all. Scene creation produces:

1. a **character sheet** — two to four recurring characters, each described on fixed axes
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
therefore run in two phases — Cut 1 alone, then the rest in parallel. Cuts 2-6 wait only while
an anchor job is *active*, so a permanently failed Cut 1 opens the gate instead of stalling the
scene forever.

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

`POST /api/webhooks/kie` is enabled only when `WEBHOOK_SECRET` is set, and the `X-Webhook-Secret`
header is compared in constant time. The payload is normalized by the same function polling uses
and applied by the same worker transition, so a callback and a poll of the same task produce
identical results.

Idempotency comes from the transition itself: every branch re-reads the job inside its
transaction and does nothing unless it is still `PROCESSING`. A duplicate delivery, or a poll
racing a callback, therefore creates no second artifact. Unknown or already-finished tasks get
`200 {"status":"ignored"}` rather than an error, because a 4xx would make the provider retry a
delivery that can never change anything.

In Mock mode the `SUCCEED_VIA_WEBHOOK` scenario makes the provider post a real callback to
`SELF_BASE_URL`, while its polling deliberately never succeeds — so the job can only finish if
the webhook route works.

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
