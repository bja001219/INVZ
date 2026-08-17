# Prompt-to-Animation Generator MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only full-stack MVP that turns one prompt into six five-second Cuts, generates versioned images and videos, demonstrates bounded retry and regeneration, and plays the selected videos sequentially.

**Architecture:** A React SPA calls a single-process FastAPI application. SQLite stores Scene, Cut, artifact, and generation job state; one in-process `GenerationWorker` submits or polls one due job at a time through a startup-selected Mock or Live provider. External async completion uses Polling only.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, SQLite/aiosqlite, HTTPX, OpenAI Python SDK, pytest, Node.js 22+, React, TypeScript, Vite, TanStack Query, Vitest, React Testing Library, Playwright, Docker Compose

**Spec:** `docs/superpowers/specs/2026-08-14-prompt-to-animation-design.md`

## Status — completed 2026-08-16

All tasks are implemented and committed, `4ce25e5` through `c9646fe`.

Two of the Global Constraints below were **deliberately superseded** on 2026-08-16 once the
assignment text confirmed the excluded features were required. Read them as history, not as
current rules; `docs/superpowers/plans/2026-08-16-batch-character-consistency.md` is the plan
that replaced them.

- "External generation completion uses Polling only; do not add callback endpoints" — a webhook
  now shares the polling state machine.
- "`GENERATION_MODE` is read once at startup; do not add a runtime mode API that mutates state"
  — `PUT /api/config` switches the mode at runtime, and each job follows its own snapshot.
- "The generation worker handles at most one due job per `run_once()`" — the worker now claims
  up to `GENERATION_CONCURRENCY` jobs per tick.
- "Do not add Batch" — scene-level batch endpoints exist.

## Global Constraints

- Scene output is exactly six Cuts and every Cut has `durationSec=5` (`REQ-01`, `REQ-02`).
- OpenAI uses `gpt-5.4-mini`; Kie uses `google/nano-banana` and `kling-2.6/image-to-video` (`REQ-09`).
- External generation completion uses Polling only; do not add callback endpoints.
- `GENERATION_MODE=mock|live` is read once at startup; do not add a runtime mode API that mutates state.
- Live startup requires both `OPENAI_API_KEY` and `KIE_API_KEY`; Mock startup requires neither.
- Backend runs as one process, one Uvicorn worker, and one generation worker coroutine.
- The generation worker handles at most one due job per `run_once()`.
- Do not add Batch, Redis, Celery, RabbitMQ, PostgreSQL, MinIO, FFmpeg, authentication, or object storage.
- Regeneration is allowed only when no job of the same Cut/kind is active; it creates the next version and preserves history (`REQ-06`).
- Retry only errors explicitly classified as retryable and stop after `GENERATION_MAX_ATTEMPTS`, default 3 (`REQ-05`).
- POST submission read timeout after request transmission is `SUBMISSION_UNCERTAIN` and is not retried automatically.
- Do not persist provider/model snapshots. Persist the actual prompt and video source image only.
- `.gitignore`, `.env.example`, secret-safe settings, and redaction exist before provider code or the first application commit (`REQ-10`).
- CORS allows only `FRONTEND_ORIGIN`; Mock media is served from `/media/mock` (`REQ-11`).
- Docker ports publish to `127.0.0.1` only. README must say this is an unauthenticated trusted-local evaluation app.
- All automated tests deny external network access and use Mock or HTTP stubs.
- Implement with TDD: focused failing test, observed failure, minimum behavior, focused pass, affected suite.

## Requirement-to-task mapping

| Requirement | Implementation task | Acceptance evidence |
|---|---|---|
| `REQ-01`, `REQ-02` | Task 2 | Scene schema/API tests and E2E |
| `REQ-03`, `REQ-04`, `REQ-06` | Task 3 | generation/version/selection tests |
| `REQ-05` | Task 4 | worker retry and failure tests |
| `REQ-07` | Task 6 | player tests and E2E |
| `REQ-08` | Tasks 2, 4, 7 | Mock provider and Compose E2E |
| `REQ-09` | Tasks 2, 4 | OpenAI and Kie HTTP contract tests |
| `REQ-10` | Tasks 1, 7 | redaction tests and secret scan |
| `REQ-11` | Tasks 1, 7 | CORS/media tests, Compose smoke |

## Planned file structure

```text
.
├─ .env.example
├─ .gitignore
├─ README.md
├─ docker-compose.yml
├─ .github/workflows/ci.yml
├─ backend/
│  ├─ Dockerfile
│  ├─ pyproject.toml
│  ├─ alembic.ini
│  ├─ alembic/{env.py,versions/0001_initial.py}
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ models.py
│  │  ├─ schemas.py
│  │  ├─ scenes.py
│  │  ├─ generations.py
│  │  ├─ worker.py
│  │  ├─ core/{config.py,db.py,errors.py,logging.py,clock.py}
│  │  ├─ providers/{contracts.py,mock.py,openai_scene.py,kie.py}
│  │  └─ static/mock/{cut-image.png,cut-video.mp4}
│  └─ tests/
│     ├─ conftest.py
│     ├─ test_core.py
│     ├─ test_scenes.py
│     ├─ test_generations.py
│     ├─ test_worker.py
│     └─ providers/{test_openai_scene.py,test_kie.py}
└─ frontend/
   ├─ Dockerfile
   ├─ package.json
   ├─ vite.config.ts
   ├─ playwright.config.ts
   ├─ src/
   │  ├─ main.tsx
   │  ├─ api/{client.ts,types.ts}
   │  ├─ app/{App.tsx,styles.css}
   │  └─ features/{scene/SceneWorkspace.tsx,generations/CutCard.tsx,player/SequencePlayer.tsx}
   └─ e2e/prompt-to-animation.spec.ts
```

The backend intentionally keeps one ORM module and one API schema module. `scenes.py` and `generations.py` each own their route and domain operations; a separate repository/service pair is not required. Provider-specific JSON remains under `providers`.

---

### Task 1: Secret-Safe Foundation, Schema, CORS, and Mock Media

**Requirements:** `REQ-10`, `REQ-11`

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `backend/pyproject.toml`
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/db.py`
- Create: `backend/app/core/errors.py`
- Create: `backend/app/core/logging.py`
- Create: `backend/app/core/clock.py`
- Create: `backend/app/models.py`
- Create: `backend/app/schemas.py`
- Create: `backend/app/main.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/0001_initial.py`
- Create: `backend/app/static/mock/cut-image.png`
- Create: `backend/app/static/mock/cut-video.mp4`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_core.py`

**Interfaces:**
- Consumes: no application interfaces.
- Produces: `Settings`, `Base`, `async_session_factory`, ORM models, `AppError`, `Clock`, stable exception handlers, FastAPI `app`, `/health`, `/api/config`, and `/media/mock`.

- [x] **Step 1: Create repository safety files before Git initialization**

Create `.gitignore` with these entries:

```gitignore
.env
.env.*
!.env.example
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
backend/data/
node_modules/
frontend/dist/
frontend/test-results/
frontend/playwright-report/
```

Create `.env.example` with names and non-secret example values only:

```dotenv
GENERATION_MODE=mock
OPENAI_API_KEY=
KIE_API_KEY=
DATABASE_URL=sqlite+aiosqlite:///./data/app-mock.db
FRONTEND_ORIGIN=http://localhost:5173
GENERATION_MAX_ATTEMPTS=3
RETRY_BASE_DELAY_SEC=1
PROVIDER_POLL_INTERVAL_SEC=1
GENERATION_ATTEMPT_TIMEOUT_SEC=120
VITE_API_BASE_URL=http://localhost:8000
```

Create README with this first paragraph:

```markdown
> Local evaluation only. This app has no authentication and must not be exposed to the public internet or an untrusted LAN. Docker Compose publishes ports on `127.0.0.1` only.
```

- [x] **Step 2: Create backend package and initialize Git**

Create `backend/pyproject.toml` with FastAPI, Uvicorn, Pydantic Settings, SQLAlchemy async, aiosqlite, Alembic, HTTPX, OpenAI, pytest, pytest-asyncio, pytest-cov, pytest-socket, respx, Ruff, and mypy. Configure pytest `asyncio_mode = "auto"`; pytest-socket blocks every external host. On Windows, allow only `127.0.0.1` because asyncio uses it for its internal socketpair, and add a regression test proving a documentation-only external address is rejected. Application HTTP tests still use ASGI transport or routes registered through respx.

Only after Step 1 files exist, run if `git rev-parse --show-toplevel` fails:

```bash
git init
git branch -M main
```

- [x] **Step 3: Write failing Settings, CORS, media, and error contract tests**

```python
async def test_config_exposes_mode_without_secrets(client):
    response = await client.get("/api/config")
    assert response.json() == {"generationMode": "MOCK"}
    assert "api" not in response.text.lower()

def test_live_mode_requires_both_keys(settings_factory):
    with pytest.raises(ValidationError):
        settings_factory(generation_mode="live", openai_api_key="", kie_api_key="")

def test_settings_repr_hides_secrets(settings_factory):
    settings = settings_factory(openai_api_key="openai-secret", kie_api_key="kie-secret")
    assert "openai-secret" not in repr(settings)
    assert "kie-secret" not in repr(settings)

def test_log_filter_masks_secrets_and_authorization(caplog, secret_filter):
    logger = logging.getLogger("app.test")
    logger.addFilter(secret_filter(openai="openai-secret", kie="kie-secret"))
    logger.error("Authorization: Bearer openai-secret kie-secret")
    assert "openai-secret" not in caplog.text
    assert "kie-secret" not in caplog.text
    assert "Bearer " not in caplog.text

async def test_cors_allows_only_frontend_origin(client):
    allowed = await client.options(
        "/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    denied = await client.options(
        "/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers

async def test_mock_media_is_served(client):
    assert (await client.get("/media/mock/cut-image.png")).headers["content-type"] == "image/png"
    assert (await client.get("/media/mock/cut-video.mp4")).headers["content-type"] == "video/mp4"

async def test_missing_resource_uses_stable_error(client):
    response = await client.get("/missing-route")
    assert response.status_code == 404
    assert response.json() == {"code": "ROUTE_NOT_FOUND", "message": "Route not found"}
```

- [x] **Step 4: Run the focused tests and observe failure**

Run: `cd backend && python -m pytest tests/test_core.py -v`

Expected: collection fails because `app.main`, settings, models, and fixtures do not exist.

- [x] **Step 5: Implement settings, schema, app wiring, CORS, errors, and media mount**

Use `SecretStr` for both API keys. Validate Live mode in Settings:

```python
@model_validator(mode="after")
def live_requires_keys(self) -> "Settings":
    if self.generation_mode == "live":
        if not self.openai_api_key.get_secret_value() or not self.kie_api_key.get_secret_value():
            raise ValueError("live mode requires OPENAI_API_KEY and KIE_API_KEY")
    return self
```

Mount static media and exact-origin CORS:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.mount("/media/mock", StaticFiles(directory=mock_media_dir), name="mock-media")
```

Define `Scene`, `Cut`, `GenerationJob`, `CutImage`, and `CutVideo`. The migration must include unique `(scene_id, order)`, unique `(cut_id, kind, version)`, unique artifact `generation_job_id`, check constraints for Cut order/duration, and the active-job partial unique index:

```sql
CREATE UNIQUE INDEX uq_active_generation_per_cut_kind
ON generation_jobs (cut_id, kind)
WHERE status IN ('QUEUED', 'SUBMITTING', 'PROCESSING', 'RETRY_WAIT');
```

Register handlers that serialize `AppError`, request validation, and missing resources as `{code,message}` without exception details.

- [x] **Step 6: Run migration and foundation verification**

Run:

```bash
cd backend
alembic upgrade head
python -m pytest tests/test_core.py -v
ruff check app tests
mypy app
```

Expected: migration succeeds; core tests, lint, and types pass.

- [x] **Step 7: Commit the safe foundation**

```bash
git add .gitignore .env.example README.md backend
git commit -m "chore: establish secure local MVP foundation"
```

---

### Task 2: Scene Creation and OpenAI Contract

**Requirements:** `REQ-01`, `REQ-02`, `REQ-08`, `REQ-09`

**Files:**
- Create: `backend/app/providers/contracts.py`
- Create: `backend/app/providers/mock.py`
- Create: `backend/app/providers/openai_scene.py`
- Create: `backend/app/scenes.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_scenes.py`
- Test: `backend/tests/providers/test_openai_scene.py`

**Interfaces:**
- Consumes: `Settings`, `Scene`, `Cut`, async DB session, `AppError`.
- Produces: `SceneDraft`, `CutDraft`, `SceneProvider.generate(prompt)`, `create_scene`, `get_scene`, `POST /api/scenes`, and `GET /api/scenes/{scene_id}`.

- [x] **Step 1: Write failing Scene schema and transaction tests**

```python
def test_scene_draft_requires_ordered_six_five_second_cuts(valid_scene_payload):
    valid_scene_payload["cuts"][5]["durationSec"] = 6
    with pytest.raises(ValidationError):
        SceneDraft.model_validate(valid_scene_payload)

async def test_scene_creation_rolls_back_invalid_provider_output(invalid_provider, session):
    with pytest.raises(AppError, match="SCENE_SCHEMA_INVALID"):
        await create_scene(session, invalid_provider, "moon voyage")
    assert await session.scalar(select(func.count(Scene.id))) == 0

async def test_scene_api_trims_prompt_and_returns_six_cuts(client):
    response = await client.post("/api/scenes", json={"prompt": "  moon voyage  "})
    assert response.status_code == 201
    body = response.json()
    assert len(body["cuts"]) == 6
    assert {cut["durationSec"] for cut in body["cuts"]} == {5}

async def test_missing_scene_uses_stable_error(client):
    response = await client.get("/api/scenes/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json() == {"code": "SCENE_NOT_FOUND", "message": "Scene not found"}
```

- [x] **Step 2: Run Scene tests and observe missing schema/service failures**

Run: `cd backend && python -m pytest tests/test_scenes.py -v`

Expected: FAIL because Scene schemas and routes are absent.

- [x] **Step 3: Implement strict schemas, Mock provider, and transactional Scene creation**

```python
class CutDraft(BaseModel):
    order: int = Field(ge=1, le=6)
    image_prompt: str = Field(alias="imagePrompt", min_length=1)
    video_prompt: str = Field(alias="videoPrompt", min_length=1)
    duration_sec: Literal[5] = Field(alias="durationSec")

class SceneDraft(BaseModel):
    title: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    cuts: Annotated[list[CutDraft], Field(min_length=6, max_length=6)]

    @model_validator(mode="after")
    def ordered_once(self) -> "SceneDraft":
        if [cut.order for cut in self.cuts] != [1, 2, 3, 4, 5, 6]:
            raise ValueError("cuts must be ordered 1 through 6")
        return self
```

Implement `MockSceneProvider` with deterministic six-Cut output. Persist Scene and Cuts inside one `session.begin()` transaction. Retry Scene generation only for typed `RetryableProviderError`; use injected `Clock.sleep()` and `base * 2**retry_index`.

- [x] **Step 4: Write failing OpenAI HTTP request/response contract tests**

Register only `POST https://api.openai.com/v1/responses` through respx and assert the captured JSON:

```python
async def test_openai_scene_request_uses_model_prompt_and_strict_schema(provider, respx_mock):
    route = respx_mock.post("https://api.openai.com/v1/responses").mock(
        return_value=Response(200, json=openai_scene_response(valid_scene_payload()))
    )
    draft = await provider.generate("moon voyage")
    request = json.loads(route.calls.last.request.content)
    assert request["model"] == "gpt-5.4-mini"
    assert "moon voyage" in json.dumps(request["input"])
    assert request["text"]["format"]["strict"] is True
    assert len(draft.cuts) == 6

@pytest.mark.parametrize("status", [429, 500, 503])
async def test_openai_retryable_status_is_normalized(provider, respx_mock, status):
    respx_mock.post("https://api.openai.com/v1/responses").mock(return_value=Response(status))
    with pytest.raises(RetryableProviderError):
        await provider.generate("moon voyage")

async def test_openai_malformed_structured_output_is_permanent(provider, respx_mock):
    respx_mock.post("https://api.openai.com/v1/responses").mock(
        return_value=Response(200, json=openai_scene_response({"title": "bad"}))
    )
    with pytest.raises(PermanentProviderError, match="OPENAI_RESPONSE_INVALID"):
        await provider.generate("moon voyage")
```

- [x] **Step 5: Implement OpenAI provider with explicit timeout and SDK retries disabled**

Construct the injected client with `max_retries=0` and explicit HTTPX connect/read timeouts. Use the Responses API structured output schema derived from `SceneDraft`. Normalize 429/5xx/connect errors as retryable, 400/401/403/422 and schema errors as permanent. Never include response bodies or authorization headers in `AppError`.

- [x] **Step 6: Run Scene and OpenAI suites**

Run:

```bash
cd backend
python -m pytest tests/test_scenes.py tests/providers/test_openai_scene.py -v
ruff check app/scenes.py app/providers tests/test_scenes.py tests/providers/test_openai_scene.py
```

Expected: all tests pass and respx reports no unregistered network calls.

- [x] **Step 7: Commit Scene creation**

```bash
git add backend/app backend/tests
git commit -m "feat: create validated scenes from mock or OpenAI"
```

---

### Task 3: Versioned Generation, Regeneration, and Selection

**Requirements:** `REQ-03`, `REQ-04`, `REQ-06`

**Files:**
- Create: `backend/app/generations.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/scenes.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_generations.py`

**Interfaces:**
- Consumes: Cut/Job/artifact ORM models, startup mode, async DB session.
- Produces: `create_image_job`, `create_video_job`, `select_image`, `select_video`, generation routes, and enriched Scene detail.

- [x] **Step 1: Write failing active-job, versioning, and lineage tests**

```python
async def test_active_job_blocks_regeneration(session, cut):
    first = await create_image_job(session, cut.id, mock_request())
    with pytest.raises(AppError, match="GENERATION_ALREADY_ACTIVE"):
        await create_image_job(session, cut.id, mock_request())
    assert first.version == 1

async def test_terminal_job_allows_next_version(session, cut, failed_image_job):
    second = await create_image_job(session, cut.id, mock_request())
    assert second.version == failed_image_job.version + 1
    assert second.id != failed_image_job.id

async def test_video_captures_selected_source_image(session, cut, selected_image):
    job = await create_video_job(session, cut.id, mock_request())
    assert job.source_image_id == selected_image.id
    assert job.prompt == cut.video_prompt
```

- [x] **Step 2: Write failing concurrent-create and selection-consistency tests**

```python
async def test_concurrent_image_requests_create_one_active_job(client, cut):
    responses = await asyncio.gather(
        client.post(f"/api/cuts/{cut.id}/images", json={"mockScenario": "SUCCESS"}),
        client.post(f"/api/cuts/{cut.id}/images", json={"mockScenario": "SUCCESS"}),
    )
    assert sorted(response.status_code for response in responses) == [202, 409]
    assert await active_job_count(cut.id, "IMAGE") == 1

async def test_changing_image_clears_incompatible_video(session, cut, image_v2):
    assert cut.selected_video_id is not None
    await select_image(session, cut.id, image_v2.id)
    await session.refresh(cut)
    assert cut.selected_image_id == image_v2.id
    assert cut.selected_video_id is None

async def test_rejects_video_from_nonselected_image(session, cut, old_video):
    with pytest.raises(AppError, match="VIDEO_SOURCE_MISMATCH"):
        await select_video(session, cut.id, old_video.id)
```

- [x] **Step 3: Run focused tests and observe failures**

Run: `cd backend && python -m pytest tests/test_generations.py -v`

Expected: FAIL because generation operations and routes are absent.

- [x] **Step 4: Implement minimal request contracts and job creation**

Define one request body:

```python
class CreateGenerationRequest(BaseModel):
    mock_scenario: MockScenario | None = Field(default=None, alias="mockScenario")
```

At the route boundary, reject non-null `mockScenario` with `422 GENERATION_REQUEST_INVALID` when startup mode is Live.

Inside one short transaction, load the Cut, calculate `coalesce(max(version), 0) + 1`, and insert `QUEUED`. Translate the partial-index `IntegrityError` into `409 GENERATION_ALREADY_ACTIVE`. Do not retry the insert into a later version.

Video creation requires a selected successful image and copies only its ID and the Cut video prompt. Do not store provider, model, completion method, runtime setting, or opaque JSON snapshots.

- [x] **Step 5: Implement selection rules and Scene detail enrichment**

Selecting an image and clearing selected video occur in one transaction. Selecting a video verifies:

```python
video.cut_id == cut.id
video.cut_image_id == cut.selected_image_id
video.job.status == JobStatus.SUCCEEDED
```

Return Scene detail with ordered Cuts, all jobs/artifacts newest-first, selected IDs, attempts, retry time, stable final error, and source image lineage.

- [x] **Step 6: Run generation and full backend suites**

Run:

```bash
cd backend
python -m pytest tests/test_generations.py -v
python -m pytest tests/test_core.py tests/test_scenes.py tests/test_generations.py -v
ruff check app tests
```

Expected: concurrent requests return one 202 and one 409; versioning and selection tests pass.

- [x] **Step 7: Commit generation APIs**

```bash
git add backend/app backend/tests/test_generations.py
git commit -m "feat: add versioned cut generation and selection"
```

---

### Task 4: Single Polling Worker, Retry Policy, and Kie Contract

**Requirements:** `REQ-05`, `REQ-08`, `REQ-09`

**Files:**
- Create: `backend/app/worker.py`
- Create: `backend/app/providers/kie.py`
- Modify: `backend/app/providers/contracts.py`
- Modify: `backend/app/providers/mock.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_worker.py`
- Test: `backend/tests/providers/test_kie.py`

**Interfaces:**
- Consumes: `GenerationJob`, artifacts, `Settings`, injected provider and clock.
- Produces: `GenerationRequest`, `Submission`, `TaskResult`, `GenerationProvider`, `GenerationWorker.run_once/start/stop`.

- [x] **Step 1: Define provider-neutral contracts and write failing Kie submit tests**

```python
@dataclass(frozen=True)
class GenerationRequest:
    job_id: UUID
    kind: GenerationKind
    prompt: str
    source_image_url: str | None
    duration_sec: Literal[5]
    mock_scenario: MockScenario | None

@dataclass(frozen=True)
class Submission:
    external_task_id: str

@dataclass(frozen=True)
class TaskResult:
    state: Literal["PENDING", "SUCCEEDED", "FAILED"]
    result_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
```

```python
async def test_kie_image_submit_contract(kie, respx_mock):
    route = respx_mock.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        return_value=Response(200, json={"code": 200, "msg": "success", "data": {"taskId": "img-1"}})
    )
    result = await kie.submit(image_request(prompt="forest"))
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "model": "google/nano-banana",
        "input": {"prompt": "forest", "aspect_ratio": "16:9", "output_format": "png"},
    }
    assert result.external_task_id == "img-1"

async def test_kie_video_submit_contract(kie, respx_mock):
    route = respx_mock.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        return_value=Response(200, json={"code": 200, "msg": "success", "data": {"taskId": "vid-1"}})
    )
    await kie.submit(video_request("move slowly", "https://cdn.example/image.png"))
    assert json.loads(route.calls.last.request.content) == {
        "model": "kling-2.6/image-to-video",
        "input": {
            "prompt": "move slowly",
            "image_urls": ["https://cdn.example/image.png"],
            "sound": False,
            "duration": "5",
        },
    }
```

- [x] **Step 2: Write failing Kie response and error contract tests**

```python
async def test_kie_poll_success_requires_nonempty_http_url(kie, respx_mock):
    respx_mock.get("https://api.kie.ai/api/v1/jobs/recordInfo").mock(
        return_value=Response(200, json=kie_success_payload(["https://cdn.example/video.mp4"]))
    )
    result = await kie.poll("vid-1")
    assert result.state == "SUCCEEDED"
    assert result.result_url == "https://cdn.example/video.mp4"

@pytest.mark.parametrize("payload", [
    {"code": 200, "data": {"state": "success", "resultJson": "{}"}},
    {"code": 200, "data": {"state": "mystery"}},
    {"code": 500, "msg": "business failure"},
])
async def test_kie_malformed_or_unknown_payload_is_contract_error(kie, respx_mock, payload):
    respx_mock.get("https://api.kie.ai/api/v1/jobs/recordInfo").mock(
        return_value=Response(200, json=payload)
    )
    with pytest.raises(PermanentProviderError, match="KIE_RESPONSE_INVALID"):
        await kie.poll("task-1")
```

- [x] **Step 3: Implement Kie adapter with explicit error classification**

Use only:

```text
POST https://api.kie.ai/api/v1/jobs/createTask
GET  https://api.kie.ai/api/v1/jobs/recordInfo?taskId={external_task_id}
Authorization: Bearer {KIE_API_KEY}
```

Validate HTTP status, provider `code`, task ID, known state, JSON-encoded `resultJson`, nonempty `resultUrls`, and HTTP(S) result URL. Map connect-before-send, explicit 429/5xx response, and provider retryable failure to typed retryable errors. Map POST read timeout to `SubmissionUncertainError`. Redact response bodies from public errors.

- [x] **Step 4: Write failing worker transition and persistence tests**

```python
async def test_worker_handles_one_due_job_per_run(worker, queued_jobs):
    await worker.run_once()
    assert await nonqueued_count() == 1

async def test_retryable_failure_uses_persisted_attempt_count(worker, fake_clock, job):
    job.mock_scenario = MockScenario.FAIL_TWICE_THEN_SUCCEED
    await worker.run_once()
    assert (job.status, job.attempt_count) == (JobStatus.RETRY_WAIT, 1)
    fake_clock.advance(seconds=1)
    await worker.run_once()
    assert job.attempt_count == 2
    fake_clock.advance(seconds=2)
    await worker.run_once()
    assert (job.status, job.attempt_count) == (JobStatus.SUCCEEDED, 3)

async def test_submission_uncertain_is_not_retried(worker, uncertain_provider, job):
    await worker.run_once()
    assert job.status == JobStatus.FAILED
    assert job.last_error_code == "SUBMISSION_UNCERTAIN"
    fake_clock.advance(seconds=999)
    await worker.run_once()
    assert uncertain_provider.submit_calls == 1

async def test_transient_poll_error_does_not_consume_attempt(worker, poll_timeout_provider, processing_job):
    before = processing_job.attempt_count
    await worker.run_once()
    assert processing_job.status == JobStatus.PROCESSING
    assert processing_job.attempt_count == before
```

- [x] **Step 5: Implement the single-job worker and startup recovery**

`run_once()` performs exactly one of these operations:

1. recover one stale row;
2. submit one due `QUEUED`/`RETRY_WAIT` job;
3. poll one due `PROCESSING` job;
4. return without work.

Increment `attempt_count` and commit `SUBMITTING` before calling submit. After acceptance, save task ID, set `PROCESSING`, `next_run_at`, and `attempt_deadline_at`. Artifact creation and `SUCCEEDED` transition share one transaction. Retry scheduling sets `RETRY_WAIT`, stable error fields, and deterministic next run time.

On first successful image, set `selected_image_id` only when it is null. On first successful video, set `selected_video_id` only when it is null and the video's source still equals the current selected image. Never overwrite an explicit user selection.

On startup:

- stale `SUBMITTING` → `FAILED/SUBMISSION_UNCERTAIN`
- `QUEUED` and due `RETRY_WAIT` remain runnable
- `PROCESSING` with task ID resumes polling

Mock failure behavior uses the Job's persisted `attempt_count`, never an in-memory counter.

- [x] **Step 6: Run worker, provider, and backend suites**

Run:

```bash
cd backend
python -m pytest tests/providers/test_kie.py tests/test_worker.py -v
python -m pytest tests -v --cov=app --cov-report=term-missing
ruff check app tests
mypy app
```

Expected: all state transitions and HTTP contracts pass with no real network calls.

- [x] **Step 7: Commit Polling generation processing**

```bash
git add backend/app backend/tests
git commit -m "feat: process generation jobs with one polling worker"
```

---

### Task 5: Frontend Workspace and Refresh-Safe State

**Requirements:** `REQ-01`, `REQ-03`, `REQ-04`, `REQ-05`, `REQ-06`

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/styles.css`
- Create: `frontend/src/features/scene/SceneWorkspace.tsx`
- Create: `frontend/src/features/generations/CutCard.tsx`
- Test: `frontend/src/app/App.test.tsx`
- Test: `frontend/src/features/scene/SceneWorkspace.test.tsx`
- Test: `frontend/src/features/generations/CutCard.test.tsx`

**Interfaces:**
- Consumes: `/api/config`, Scene endpoints, generation endpoints, selection endpoints.
- Produces: typed API client, prompt/Scene workspace, Cut cards, query polling, and URL Scene restoration.

- [x] **Step 1: Create frontend test harness**

Add React, TanStack Query, TypeScript, Vite, ESLint, Vitest, jsdom, Testing Library, MSW, and Playwright. Define scripts `dev`, `build`, `lint`, `test`, `test:run`, and `test:e2e`.

- [x] **Step 2: Write failing startup mode and Scene restoration tests**

```tsx
it("shows startup mode without a switch", async () => {
  renderApp({ config: { generationMode: "MOCK" } });
  expect(await screen.findByText("Mock mode")).toBeInTheDocument();
  expect(screen.queryByRole("switch")).not.toBeInTheDocument();
});

it("restores the scene from the URL after reload", async () => {
  window.history.replaceState({}, "", "/?scene=scene-1");
  renderApp({ scene: sceneDetail({ id: "scene-1" }) });
  expect(await screen.findByText("Moon Voyage")).toBeInTheDocument();
  expect(receivedSceneDetailIds()).toEqual(["scene-1"]);
});

it("writes a newly created scene id to the URL", async () => {
  renderApp();
  await user.type(screen.getByLabelText("Animation prompt"), "moon voyage");
  await user.click(screen.getByRole("button", { name: "Create scene" }));
  await screen.findByText("Moon Voyage");
  expect(new URL(window.location.href).searchParams.get("scene")).toBe("scene-1");
});
```

- [x] **Step 3: Write failing pending-mutation, retry, regenerate, and selection tests**

```tsx
it("disables generation immediately while the mutation is pending", async () => {
  renderCut(cutWithoutImage(), { imageRequest: deferredPromise() });
  const button = screen.getByRole("button", { name: "Generate image" });
  await user.click(button);
  expect(button).toBeDisabled();
  await user.click(button);
  expect(receivedImageRequests()).toHaveLength(1);
});

it("shows retry and final failure then regenerates a new version", async () => {
  renderCut(cutWithFailedVideo({ attemptCount: 3, maxAttempts: 3 }));
  expect(screen.getByText("Failed after 3/3 attempts")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Regenerate video" }));
  expect(receivedVideoRequests()).toHaveLength(1);
});

it("clears the displayed video selection after selecting another image", async () => {
  renderCut(cutWithImageAndVideoVersions());
  await user.click(screen.getByRole("button", { name: "Select Image v2" }));
  expect(await screen.findByText("Select a compatible video")).toBeInTheDocument();
});
```

- [x] **Step 4: Run frontend tests and observe missing component failures**

Run: `cd frontend && npm test -- --run`

Expected: FAIL because the app, API client, and components are absent.

- [x] **Step 5: Implement typed API client and query ownership**

Use query keys `['config']` and `['scene', sceneId]`. Poll Scene every second only while any job is nonterminal. Generation mutations disable their own button via `mutation.isPending`, then invalidate only the current Scene query. Parse `{code,message}` into `ApiError` and show the safe message.

Keep the active Scene ID in `URLSearchParams`, not component-only state. When Scene ID changes, mount a new `SceneWorkspace key={sceneId}` so local Cut/player state resets.

- [x] **Step 6: Implement accessible workspace and Cut cards**

Render six ordered Cut regions. Show newest generation versions first without hiding failures. Show status, `attemptCount/maxAttempts`, next retry time, stable final error, source image, and prompt. Button labels are `Generate image`, `Regenerate image`, `Generate video`, or `Regenerate video` based only on artifact history and active status.

Mock scenario controls appear only when `/api/config` returns `MOCK`. Live has no runtime switch and no key status UI.

- [x] **Step 7: Run frontend verification**

Run:

```bash
cd frontend
npm run test:run
npm run lint
npm run build
```

Expected: tests, lint, and production build pass.

- [x] **Step 8: Commit the frontend workspace**

```bash
git add frontend
git commit -m "feat: add refresh-safe generation workspace"
```

---

### Task 6: Nominal Thirty-Second Sequence Player

**Requirements:** `REQ-07`

**Files:**
- Create: `frontend/src/features/player/SequencePlayer.tsx`
- Create: `frontend/src/features/player/SequencePlayer.test.tsx`
- Modify: `frontend/src/features/scene/SceneWorkspace.tsx`
- Modify: `frontend/src/app/styles.css`

**Interfaces:**
- Consumes: ordered Cuts with selected successful video and current selected image lineage.
- Produces: `SequencePlayer` with readiness, play, pause, restart, Cut transition, progress, and media error UI.

- [x] **Step 1: Write failing readiness and transition tests**

```tsx
it("requires six compatible selected videos", () => {
  render(<SequencePlayer cuts={cutsWithOnlyFiveVideos()} />);
  expect(screen.getByRole("button", { name: "Play sequence" })).toBeDisabled();
  expect(screen.getByText("5 of 6 videos ready")).toBeInTheDocument();
});

it("advances on ended and stops after cut six", async () => {
  render(<SequencePlayer cuts={sixReadyCuts()} />);
  await user.click(screen.getByRole("button", { name: "Play sequence" }));
  for (let cut = 1; cut < 6; cut += 1) {
    fireEvent.ended(screen.getByTestId("sequence-video"));
    expect(screen.getByText(`Cut ${cut + 1} of 6`)).toBeInTheDocument();
  }
  fireEvent.ended(screen.getByTestId("sequence-video"));
  expect(screen.getByRole("button", { name: "Restart sequence" })).toBeEnabled();
});

it("shows media and autoplay failures", async () => {
  render(<SequencePlayer cuts={sixReadyCuts()} playRejects />);
  await user.click(screen.getByRole("button", { name: "Play sequence" }));
  expect(await screen.findByText("Playback could not start")).toBeInTheDocument();
  fireEvent.error(screen.getByTestId("sequence-video"));
  expect(screen.getByText("Cut video could not be loaded")).toBeInTheDocument();
});
```

- [x] **Step 2: Run player tests and observe missing component failure**

Run: `cd frontend && npm test -- --run src/features/player/SequencePlayer.test.tsx`

Expected: collection fails because `SequencePlayer` is absent.

- [x] **Step 3: Implement one-video-at-a-time sequence playback**

Sort Cuts by `order`, derive readiness, and render one `<video>` at a time. Advance only on `ended`. Reset to Cut 1 when selected video IDs change. Catch `video.play()` rejection and handle `error`. Label the UI `Nominal 30-second sequence`; do not claim exact media duration.

- [x] **Step 4: Run player and full frontend suites**

Run:

```bash
cd frontend
npm test -- --run src/features/player/SequencePlayer.test.tsx
npm run test:run
npm run build
```

Expected: readiness, transition, reset, and error tests pass.

- [x] **Step 5: Commit the sequence player**

```bash
git add frontend/src
git commit -m "feat: play selected cut videos sequentially"
```

---

### Task 7: Local Delivery, Traceable E2E, CI, and Documentation

**Requirements:** `REQ-01` through `REQ-11`

**Files:**
- Create: `backend/Dockerfile`
- Create: `frontend/Dockerfile`
- Create: `docker-compose.yml`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/prompt-to-animation.spec.ts`
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`
- Test: `backend/tests/test_core.py`

**Interfaces:**
- Consumes: complete backend and frontend.
- Produces: localhost-only Compose demo, end-to-end requirement evidence, CI, and submission documentation.

- [x] **Step 1: Add a full secret redaction test before delivery code**

```python
@pytest.mark.parametrize("secret_name", ["openai_api_key", "kie_api_key"])
async def test_provider_error_never_serializes_configured_secrets(
    client, caplog, settings, secret_name
):
    secret = getattr(settings, secret_name).get_secret_value()
    response = await trigger_stubbed_provider_failure(client)
    serialized = response.text + "\n" + caplog.text + "\n" + repr(settings)
    assert secret not in serialized
    assert "Authorization" not in serialized
    assert "Bearer " not in serialized
```

Run: `cd backend && python -m pytest tests/test_core.py -k secret -v`

Expected: PASS using the logging filter created in Task 1.

- [x] **Step 2: Create localhost-only Dockerfiles and Compose**

Backend command:

```text
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Compose port bindings must be:

```yaml
services:
  backend:
    ports:
      - "127.0.0.1:8000:8000"
  frontend:
    ports:
      - "127.0.0.1:5173:5173"
```

Compose contains only frontend and backend, defaults to `GENERATION_MODE=mock`, mounts one named backend data volume, and waits for `/health` before frontend startup.

- [x] **Step 3: Write the requirement-traceable Playwright scenario**

```ts
test("REQ-01..08: creates, retries, regenerates, restores, and plays", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Animation prompt").fill("A paper astronaut explores a watercolor moon");
  await page.getByRole("button", { name: "Create scene" }).click();
  await expect(page.getByRole("region", { name: /^Cut / })).toHaveCount(6);

  for (let order = 1; order <= 6; order += 1) {
    const cut = page.getByRole("region", { name: `Cut ${order}` });
    await cut.getByRole("button", { name: "Generate image" }).click();
    await expect(cut.getByText(/Image v1.*Succeeded/)).toBeVisible();
  }

  const first = page.getByRole("region", { name: "Cut 1" });
  await page.getByLabel("Mock scenario").selectOption("ALWAYS_FAIL");
  await first.getByRole("button", { name: "Generate video" }).click();
  await expect(first.getByText("Failed after 3/3 attempts")).toBeVisible();
  await page.getByLabel("Mock scenario").selectOption("SUCCESS");
  await first.getByRole("button", { name: "Regenerate video" }).click();
  await expect(first.getByText(/Video v2.*Succeeded/)).toBeVisible();

  for (let order = 2; order <= 6; order += 1) {
    const cut = page.getByRole("region", { name: `Cut ${order}` });
    await cut.getByRole("button", { name: "Generate video" }).click();
    await expect(cut.getByText(/Video v1.*Succeeded/)).toBeVisible();
  }

  await page.reload();
  await expect(page.getByRole("region", { name: /^Cut / })).toHaveCount(6);
  await expect(page.getByRole("button", { name: "Play sequence" })).toBeEnabled();
});
```

- [x] **Step 4: Complete README with scope, traceability, and trade-offs**

README must contain:

- requirement table `REQ-01` through `REQ-11` with test commands
- local-only/no-auth warning before quick start
- Mock quick start and separate Mock DB URL
- Live setup and separate Live DB URL
- all environment variables without secret examples
- architecture and Polling state machine
- retryable, permanent, and uncertain-submission behavior
- regenerate semantics and active-job conflict
- CORS and `/media/mock` behavior
- nominal 30-second sequence limitation
- single-process, expiring provider URL, and no-MP4 limitations
- backend, frontend, E2E, Compose, and secret-scan commands

- [x] **Step 5: Add CI without API secrets**

CI executes:

```text
backend: ruff check app tests; mypy app; pytest tests
frontend: npm ci; npm run lint; npm run test:run; npm run build
```

Do not define `OPENAI_API_KEY` or `KIE_API_KEY`. Run all tests in Mock mode and fail on any unregistered outbound request.

- [x] **Step 6: Run complete automated verification**

Run:

```bash
cd backend && ruff check app tests && mypy app && python -m pytest tests -v --cov=app
cd ../frontend && npm run lint && npm run test:run && npm run build
cd .. && docker compose config
docker compose up --build -d
cd frontend && npx playwright test
cd .. && docker compose logs backend --no-color
```

Expected: all checks pass, both services are healthy, E2E passes in Mock mode, and logs contain no authorization headers or configured secrets.

- [x] **Step 7: Run repository secret and removed-scope scans**

Run:

```bash
git grep -n -E "(sk-[A-Za-z0-9_-]{16,}|Bearer [A-Za-z0-9_-]{16,}|OPENAI_API_KEY=.+|KIE_API_KEY=.+)" -- . ":(exclude).env.example" ":(exclude)docs/superpowers/plans/2026-08-14-prompt-to-animation.md"
rg -n "runtime-mode|RuntimeSetting|completion_method|WEBHOOK_SIGNING_SECRET|/api/webhooks|generations/batch|BatchService" backend frontend
```

Expected: both commands return no matches.

- [x] **Step 8: Perform the manual local acceptance walkthrough**

1. Open `http://localhost:5173` and confirm read-only `Mock mode`.
2. Create one Scene and verify exactly six five-second Cuts.
3. Generate all six images.
4. Run `FAIL_TWICE_THEN_SUCCEED` and observe attempts 1/3, 2/3, success 3/3.
5. Run one `ALWAYS_FAIL` video and observe final failure 3/3.
6. Regenerate that video with `SUCCESS`; verify v1 failure remains and v2 succeeds.
7. Generate/select remaining videos and play the sequence.
8. Reload and confirm the same Scene restores from the URL.
9. Inspect browser network and built assets; confirm no API Key or mode mutation endpoint exists.

- [x] **Step 9: Stop containers and commit delivery artifacts**

Run:

```bash
docker compose down
git add README.md docker-compose.yml .github backend frontend
git commit -m "chore: add local MVP delivery and verification"
```

Keep the named SQLite volume for persistence evidence.

---

## Final acceptance gate

Implementation is complete only when:

- every `REQ-*` row has a passing automated test or explicit manual evidence;
- no removed feature appears in backend/frontend code;
- provider contract tests pass without real network calls;
- the Mock E2E walkthrough passes from a clean build;
- secrets are absent from API responses, logs, frontend assets, and tracked environment files;
- README states all accepted trade-offs without presenting them as production guarantees.
