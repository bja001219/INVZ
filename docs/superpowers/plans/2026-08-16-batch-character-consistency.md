# Batch Generation and Character Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn one prompt into six cuts that share the same characters and a single cute animated style, generate their images and videos in bounded-concurrency batches, and let an operator switch Mock/Live at runtime while both Polling and Webhook drive one job state machine.

**Architecture:** Scene creation now yields a character sheet plus per-cut shot descriptions; a pure `prompting` module deterministically composes the final image and video prompts stored on each Cut. Scene-level batch endpoints enqueue six jobs at once and one `GenerationWorker` claims up to `GENERATION_CONCURRENCY` due jobs serially, then calls providers concurrently. Cut 1's selected image becomes the scene anchor referenced by cuts 2-6.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, SQLite/aiosqlite, HTTPX, OpenAI SDK, pytest, respx, React 19, TypeScript, Vite, TanStack Query, Vitest, Playwright

**Spec:** `docs/superpowers/specs/2026-08-16-batch-character-consistency-design.md`

## Status — completed 2026-08-16, review fixes landed 2026-08-17

Tasks 1-8 are implemented and committed, `4ee89e3` through `295997a`. A senior review after the
last task produced further findings; the ones fixed since are in `f49276c` and `c97b7e7`.

**Fixed in `295997a`** — Live provider built twice and leaking its HTTP client; a webhook losing
the write race with a poll returned 500 and made the provider retry forever; the Mock callback
task was never discarded.

**Fixed in `f49276c`** — the anchor gate opened when Cut 1 had simply never been requested, so a
single cut generated outside a batch produced an unreferenced image and broke character
consistency silently; the capped candidate fetch could fill with anchor-gated jobs and hide
runnable jobs behind them; the Mock webhook was scheduled inside `submit()` and could overtake
the worker's own `PROCESSING` commit.

**Fixed in `c97b7e7`** — `docker-compose.yml` carried a literal `WEBHOOK_SECRET`.

**Still open, deliberately.** These are recorded rather than fixed because each is a scope
decision, not a defect to patch quietly:

- The anchor reference's *effect* is unverified. The `image_urls` request shape is fixed by a
  contract test, but whether the model honours it is a documented assumption.
- Live mode has no cost guard: two clicks are twelve real provider calls, with no confirmation
  and no usage counter.
- Concurrent `run_once()` double-claiming is prevented structurally by serial claiming, but no
  test would fail if someone later parallelised the claim.
- `GenerationBatch.requested_count` is always the scene's cut count, so the name promises more
  than it delivers.
- Runtime mode lives in process memory and silently reverts on restart; the UI does not say so.
- `_recover_one_submitting` recovers one row per tick, so a crash leaving three takes three ticks.
- The Mock scenario select exists both per-cut and per-batch and the two can disagree.

**Verification at completion:** 192 backend tests (92% coverage), 55 frontend tests, 3 Playwright
E2E, ruff and mypy --strict clean, `alembic upgrade/downgrade/upgrade` clean. The E2E run used
`MOCK_WEBHOOK_DELAY_SEC=0` so the callback-ordering fix is exercised rather than masked by latency.

One Global Constraint below is **superseded**: "The anchor gate never deadlocks: cuts 2-6 wait
only while an anchor job is active." Waiting only for *active* anchor jobs is precisely the hole
that `f49276c` closed. The current rule is that cuts 2-6 wait whenever an anchor could still
arrive, and the only release is Cut 1 exhausting its retries.

## Global Constraints

- Scene output stays exactly six Cuts with `durationSec=5`.
- Prompt composition is a pure function: same inputs always produce the same string.
- Jobs follow their own `generation_mode` snapshot, never the current global mode.
- Claiming jobs is serial in one transaction; only provider calls run concurrently.
- The anchor gate never deadlocks: cuts 2-6 wait only while an anchor job is active.
- Webhook and polling share one normalization function and one transition function.
- Every state transition re-checks `job.status` inside its transaction.
- API keys stay in backend environment only; `/api/config` never exposes key values.
- Existing 131 backend and 41 frontend tests must still pass at the end.
- TDD: focused failing test, observed failure, minimum code, focused pass, affected suite.

## Requirement-to-task mapping

| Requirement | Task |
|---|---|
| `REQ-14` style guide | Task 1 |
| `REQ-13` character consistency | Tasks 1, 2, 4 |
| `REQ-17` traceability | Tasks 3, 7 |
| `REQ-12` batch | Tasks 3, 4, 7 |
| `REQ-15` runtime mode | Task 5 |
| `REQ-16` webhook | Task 6 |
| all | Task 8 |

## File structure

```text
backend/app/
├─ prompting.py                 NEW  pure style/character/prompt composition
├─ batches.py                   NEW  batch creation + batch view assembly
├─ webhooks.py                  NEW  webhook payload verification + routing helper
├─ runtime.py                   NEW  RuntimeMode + ProviderRegistry
├─ models.py                    MOD  Scene.character_profiles, Cut.shot_description/video_motion,
│                                    GenerationJob.generation_mode/reference_image_id/batch_id,
│                                    GenerationBatch
├─ schemas.py                   MOD  CharacterProfile, CutDraft rework, batch + config schemas
├─ scenes.py                    MOD  compose prompts at creation, expose new fields
├─ generations.py               MOD  record mode snapshot, anchor resolution helper
├─ worker.py                    MOD  bounded concurrency, anchor gating, public apply_external_result
├─ providers/contracts.py       MOD  GenerationRequest.reference_image_url
├─ providers/kie.py             MOD  image_urls, callBackUrl, extracted task_result_from_data
├─ providers/mock.py            MOD  webhook scenario + deterministic media
├─ main.py                      MOD  new routes, registry wiring
└─ alembic/versions/0002_*.py   NEW  migration

backend/tests/
├─ test_prompting.py            NEW
├─ test_batches.py              NEW
├─ test_webhooks.py             NEW
└─ (existing files)             MOD

frontend/src/
├─ features/scene/CharacterPanel.tsx   NEW
├─ features/scene/BatchControls.tsx    NEW
├─ app/ModeSwitch.tsx                  NEW
└─ (existing)                          MOD
```

---

### Task 1: Deterministic prompt composition

**Files:**
- Create: `backend/app/prompting.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_prompting.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `VISUAL_STYLE_GUIDE`, `NEGATIVE_STYLE_GUIDE`, `CharacterProfile`,
  `character_sheet(profiles) -> str`, `compose_image_prompt(profiles, shot_description) -> str`,
  `compose_video_prompt(profiles, shot_description, video_motion) -> str`,
  `SCENE_SYSTEM_INSTRUCTION`.

- [x] **Step 1: Write the failing tests**

```python
def test_character_sheet_is_stable_and_ordered(profiles):
    assert character_sheet(profiles) == character_sheet(profiles)
    assert character_sheet(profiles).index("Mina") < character_sheet(profiles).index("Jun")

def test_image_prompt_contains_style_characters_shot_and_negative(profiles):
    prompt = compose_image_prompt(profiles, "two students meet at the school gate")
    assert VISUAL_STYLE_GUIDE in prompt
    assert "Mina" in prompt and "Jun" in prompt
    assert "two students meet at the school gate" in prompt
    assert NEGATIVE_STYLE_GUIDE in prompt

def test_prompts_never_ask_for_photoreal(profiles):
    for prompt in (compose_image_prompt(profiles, "s"), compose_video_prompt(profiles, "s", "m")):
        assert "photorealistic" not in prompt.replace("not photorealistic", "")
        assert "live action" not in prompt.replace("no live action", "")

def test_video_prompt_adds_motion_and_duration(profiles):
    prompt = compose_video_prompt(profiles, "walking together", "slow dolly in")
    assert "slow dolly in" in prompt
    assert "5-second" in prompt

def test_every_cut_shares_one_character_sheet(profiles):
    sheets = {compose_image_prompt(profiles, f"shot {n}").split("Shot:")[0] for n in range(1, 7)}
    assert len(sheets) == 1
```

- [x] **Step 2: Run the tests and observe failure**

Run: `cd backend && python -m pytest tests/test_prompting.py -v`
Expected: collection error, `app.prompting` does not exist.

- [x] **Step 3: Implement the module**

```python
VISUAL_STYLE_GUIDE = (
    "stylized 2D animation still, cute cinematic anime look, soft cel shading, "
    "clean appealing character design, expressive faces, warm slightly dreamy "
    "high-school mood, hand-painted background, gentle rim light"
)
NEGATIVE_STYLE_GUIDE = (
    "not photorealistic, no live action footage, no 3D render, "
    "no photographic skin texture, no hyperrealistic detail"
)

def character_sheet(profiles):
    return "; ".join(
        f"{p.name} ({p.role}, {p.age_range}): {p.hair_color} {p.hair_style} hair, "
        f"wearing {p.outfit}, {p.build} build, {p.face_impression}, carries {p.signature_prop}"
        for p in profiles
    )

def compose_image_prompt(profiles, shot_description):
    return (
        f"{VISUAL_STYLE_GUIDE}. "
        f"Characters (keep identical in every shot): {character_sheet(profiles)}. "
        f"Shot: {shot_description}. "
        f"Avoid: {NEGATIVE_STYLE_GUIDE}."
    )

def compose_video_prompt(profiles, shot_description, video_motion):
    return (
        f"{VISUAL_STYLE_GUIDE}. "
        f"Characters (keep identical in every shot): {character_sheet(profiles)}. "
        f"Shot: {shot_description}. Motion: {video_motion}. "
        f"5-second continuous animated shot, keep character appearance consistent. "
        f"Avoid: {NEGATIVE_STYLE_GUIDE}."
    )
```

`SCENE_SYSTEM_INSTRUCTION` tells the model to invent two to four recurring characters, fill every
character field concretely, and write `shotDescription`/`videoMotion` that never restate hair or
outfit because the character sheet is injected separately.

- [x] **Step 4: Run the tests and verify they pass**

Run: `cd backend && python -m pytest tests/test_prompting.py -v && ruff check app tests && mypy app`

- [x] **Step 5: Commit**

```bash
git add backend/app/prompting.py backend/app/schemas.py backend/tests/test_prompting.py
git commit -m "feat: compose cut prompts from a shared character sheet and style guide"
```

---

### Task 2: Scene creates a character sheet and composed prompts

**Files:**
- Modify: `backend/app/models.py`, `backend/app/schemas.py`, `backend/app/scenes.py`,
  `backend/app/providers/mock.py`, `backend/app/providers/openai_scene.py`
- Test: `backend/tests/test_scenes.py`, `backend/tests/providers/test_openai_scene.py`

**Interfaces:**
- Consumes: Task 1 composition functions.
- Produces: `Scene.character_profiles` (JSON), `Cut.shot_description`, `Cut.video_motion`,
  `SceneDraft.character_profiles`, `CutDraft(order, shot_description, video_motion, duration_sec)`.

- [x] **Step 1: Write the failing tests**

```python
def test_scene_draft_requires_at_least_two_characters(valid_scene_payload):
    valid_scene_payload["characterProfiles"] = valid_scene_payload["characterProfiles"][:1]
    with pytest.raises(ValidationError):
        SceneDraft.model_validate(valid_scene_payload)

async def test_scene_api_stores_character_profiles_and_composed_prompts(client):
    body = (await client.post("/api/scenes", json={"prompt": "moon voyage"})).json()
    assert len(body["characterProfiles"]) >= 2
    lead = body["characterProfiles"][0]["name"]
    for cut in body["cuts"]:
        assert lead in cut["imagePrompt"]
        assert VISUAL_STYLE_GUIDE in cut["imagePrompt"]
        assert cut["shotDescription"]
```

- [x] **Step 2: Run and observe failure**

Run: `cd backend && python -m pytest tests/test_scenes.py -v`

- [x] **Step 3: Implement**

`Scene.character_profiles` is `JSON` holding the serialized profile list. `create_scene` composes
`image_prompt` and `video_prompt` once with Task 1 functions and stores them alongside
`shot_description` and `video_motion`. `MockSceneProvider` returns two fixed characters so Mock output
is deterministic. `OpenAISceneProvider` passes `SCENE_SYSTEM_INSTRUCTION` as the system message.

- [x] **Step 4: Run the affected suites**

Run: `cd backend && python -m pytest tests/test_scenes.py tests/providers/test_openai_scene.py tests/test_prompting.py -v`

- [x] **Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: generate a scene character sheet and inject it into every cut prompt"
```

---

### Task 3: Job traceability fields and the batch record

**Files:**
- Modify: `backend/app/models.py`, `backend/app/schemas.py`, `backend/app/generations.py`,
  `backend/app/scenes.py`
- Create: `backend/alembic/versions/0002_batch_and_character.py`
- Test: `backend/tests/test_generations.py`

**Interfaces:**
- Consumes: Task 2 models.
- Produces: `GenerationJob.generation_mode`, `.reference_image_id`, `.batch_id`,
  `GenerationBatch(id, scene_id, kind, requested_count, created_at)`,
  `create_image_job(..., generation_mode, batch_id=None)`.

- [x] **Step 1: Write the failing tests**

```python
async def test_job_records_mode_snapshot(session):
    job = await create_image_job(session, cut.id, request, generation_mode="MOCK")
    assert job.generation_mode == "MOCK"

async def test_scene_detail_exposes_mode_and_reference(client, cut):
    body = (await client.get(f"/api/scenes/{scene_id}")).json()
    job = body["cuts"][0]["imageJobs"][0]
    assert job["generationMode"] in {"MOCK", "LIVE"}
    assert "referenceImageId" in job
```

- [x] **Step 2: Run and observe failure**

Run: `cd backend && python -m pytest tests/test_generations.py -v`

- [x] **Step 3: Implement model, schema, and migration**

Migration `0002` adds the three job columns, `scenes.character_profiles`, `cuts.shot_description`,
`cuts.video_motion`, and creates `generation_batches`. Existing rows get `generation_mode='MOCK'`
via `server_default` then the default is dropped.

- [x] **Step 4: Run migration and suites**

Run: `cd backend && alembic upgrade head && python -m pytest tests -v`

- [x] **Step 5: Commit**

```bash
git add backend/app backend/alembic backend/tests
git commit -m "feat: record generation mode, reference image, and batch on each job"
```

---

### Task 4: Batch endpoints, bounded concurrency, and the anchor gate

**Files:**
- Create: `backend/app/batches.py`, `backend/tests/test_batches.py`
- Modify: `backend/app/worker.py`, `backend/app/main.py`,
  `backend/app/providers/contracts.py`, `backend/app/providers/kie.py`
- Test: `backend/tests/test_worker.py`, `backend/tests/providers/test_kie.py`

**Interfaces:**
- Consumes: Task 3 batch model.
- Produces: `create_image_batch(session, scene_id, request, mode) -> BatchResult`,
  `create_video_batch(...)`, `POST /api/scenes/{id}/images`, `POST /api/scenes/{id}/videos`,
  `GenerationRequest.reference_image_url`, `GenerationWorker(concurrency=...)`.

- [x] **Step 1: Write the failing batch tests**

```python
async def test_image_batch_creates_one_job_per_cut(client, scene):
    response = await client.post(f"/api/scenes/{scene.id}/images", json={})
    assert response.status_code == 202
    assert len(response.json()["createdJobIds"]) == 6

async def test_batch_skips_cuts_with_an_active_job(client, scene):
    await client.post(f"/api/cuts/{scene.cuts[0].id}/images", json={})
    body = (await client.post(f"/api/scenes/{scene.id}/images", json={})).json()
    assert len(body["createdJobIds"]) == 5
    assert body["skipped"] == [{"cutId": str(scene.cuts[0].id), "reason": "GENERATION_ALREADY_ACTIVE"}]
```

- [x] **Step 2: Write the failing concurrency and anchor tests**

```python
async def test_worker_handles_up_to_concurrency_jobs_per_run(worker_factory, six_queued_jobs):
    worker = worker_factory(concurrency=3)
    await worker.run_once()
    assert await nonqueued_count() == 3

async def test_worker_still_handles_one_job_when_concurrency_is_one(worker_factory, six_queued_jobs):
    await worker_factory(concurrency=1).run_once()
    assert await nonqueued_count() == 1

async def test_non_anchor_image_waits_while_anchor_job_is_active(worker, scene_with_active_cut1_image):
    await worker.run_once()
    assert (await job_for_cut(2)).status == JobStatus.QUEUED

async def test_non_anchor_image_proceeds_without_reference_after_anchor_fails(worker, scene_with_failed_cut1):
    await worker.run_once()
    job = await job_for_cut(2)
    assert job.status != JobStatus.QUEUED and job.reference_image_id is None

async def test_non_anchor_image_uses_anchor_reference(worker, scene_with_selected_cut1_image, provider):
    await worker.run_once()
    assert provider.last_request.reference_image_url == anchor_url

async def test_kie_image_submit_includes_reference_image_urls(kie, respx_mock):
    await kie.submit(image_request(prompt="forest", reference_image_url="https://cdn.example/a.png"))
    body = json.loads(route.calls.last.request.content)
    assert body["input"]["image_urls"] == ["https://cdn.example/a.png"]
```

- [x] **Step 3: Run and observe failures**

Run: `cd backend && python -m pytest tests/test_batches.py tests/test_worker.py tests/providers/test_kie.py -v`

- [x] **Step 4: Implement claim-serial / call-concurrent worker**

`run_once()` becomes:

```python
async def run_once(self) -> bool:
    if await self._recover_one_submitting():
        return True
    requests = await self._claim_due_submissions(self._concurrency)   # one transaction
    if requests:
        await asyncio.gather(*(self._submit(r) for r in requests))
        return True
    if await self._expire_one_attempt():
        return True
    polls = await self._claim_due_polls(self._concurrency)
    if not polls:
        return False
    await asyncio.gather(*(self._poll(job_id, task_id) for job_id, task_id in polls))
    return True
```

`_claim_due_submissions` resolves the anchor for each IMAGE job of a cut whose `order != 1`:
returns the anchor URL when the scene's cut 1 has a selected image, skips the job while an anchor
job is active, and proceeds with `reference_image_url=None` once no anchor job remains active.

`_submission_body` in `kie.py` adds `"image_urls": [reference_image_url]` for IMAGE requests that
carry a reference.

- [x] **Step 5: Run the suites**

Run: `cd backend && python -m pytest tests -v && ruff check app tests && mypy app`

- [x] **Step 6: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: generate cut images and videos in bounded-concurrency batches"
```

---

### Task 5: Runtime Mock/Live switching

**Files:**
- Create: `backend/app/runtime.py`
- Modify: `backend/app/main.py`, `backend/app/worker.py`, `backend/app/core/config.py`
- Test: `backend/tests/test_core.py`

**Interfaces:**
- Consumes: `Settings`, both provider implementations.
- Produces: `RuntimeMode(initial, live_available)`, `ProviderRegistry.scene_provider(mode)`,
  `.generation_provider(mode)`, `GET/PUT /api/config`.

- [x] **Step 1: Write the failing tests**

```python
async def test_config_reports_live_availability(client):
    assert (await client.get("/api/config")).json() == {"generationMode": "MOCK", "liveAvailable": False}

async def test_switching_to_live_without_keys_is_rejected(client):
    response = await client.put("/api/config", json={"generationMode": "LIVE"})
    assert response.status_code == 409
    assert response.json() == {"code": "LIVE_MODE_UNAVAILABLE", "message": "Live mode is not configured"}

async def test_switch_takes_effect_for_new_jobs_only(live_capable_client, cut):
    first = await live_capable_client.post(f"/api/cuts/{cut.id}/images", json={})
    await live_capable_client.put("/api/config", json={"generationMode": "LIVE"})
    assert first.json()["generationMode"] == "MOCK"
```

- [x] **Step 2: Run and observe failure**

Run: `cd backend && python -m pytest tests/test_core.py -k config -v`

- [x] **Step 3: Implement**

`RuntimeMode` holds the current mode in memory. `ProviderRegistry` builds the Mock providers always
and the Live providers only when both keys are present. The worker looks up the provider with
`registry.generation_provider(job.generation_mode)` so a mode flip never redirects an in-flight task.

- [x] **Step 4: Run the suites**

Run: `cd backend && python -m pytest tests -v`

- [x] **Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: switch generation mode at runtime without moving in-flight jobs"
```

---

### Task 6: Webhook sharing the polling state machine

**Files:**
- Create: `backend/app/webhooks.py`, `backend/tests/test_webhooks.py`
- Modify: `backend/app/providers/kie.py`, `backend/app/providers/mock.py`,
  `backend/app/worker.py`, `backend/app/main.py`, `backend/app/core/config.py`
- Test: `backend/tests/providers/test_kie.py`

**Interfaces:**
- Consumes: Task 4 worker.
- Produces: `task_result_from_data(data) -> TaskResult`, `GenerationWorker.apply_external_result`,
  `POST /api/webhooks/kie`, `MockScenario.SUCCEED_VIA_WEBHOOK`.

- [x] **Step 1: Write the failing tests**

```python
async def test_webhook_requires_the_configured_secret(webhook_client):
    assert (await webhook_client.post("/api/webhooks/kie", json=payload)).status_code == 401

async def test_webhook_completes_a_processing_job(webhook_client, processing_job):
    response = await webhook_client.post("/api/webhooks/kie", json=success_payload("task-1"),
                                         headers={"X-Webhook-Secret": "s3cret"})
    assert response.json() == {"status": "applied"}
    assert (await reload(processing_job)).status == JobStatus.SUCCEEDED

async def test_duplicate_webhook_is_ignored(webhook_client, processing_job):
    await webhook_client.post(...)   # first delivery succeeds
    second = await webhook_client.post(...)
    assert second.status_code == 200
    assert second.json() == {"status": "ignored"}
    assert await artifact_count(processing_job.cut_id) == 1

async def test_unknown_task_is_ignored_not_errored(webhook_client):
    response = await webhook_client.post("/api/webhooks/kie", json=success_payload("nope"),
                                         headers={"X-Webhook-Secret": "s3cret"})
    assert (response.status_code, response.json()) == (200, {"status": "ignored"})

async def test_polling_after_webhook_does_not_double_apply(worker, webhook_client, processing_job):
    await webhook_client.post(...)
    await worker.run_once()
    assert await artifact_count(processing_job.cut_id) == 1

async def test_kie_submit_sends_callback_url_when_configured(kie_with_callback, respx_mock):
    await kie_with_callback.submit(image_request())
    assert json.loads(route.calls.last.request.content)["callBackUrl"] == "https://example.test/api/webhooks/kie"
```

- [x] **Step 2: Run and observe failure**

Run: `cd backend && python -m pytest tests/test_webhooks.py -v`

- [x] **Step 3: Implement**

Extract `task_result_from_data` from `KieGenerationProvider.poll` and reuse it in both paths.
Promote `_apply_poll_result` to `apply_external_result`. The route verifies the secret with
`secrets.compare_digest`, finds the job by `external_task_id` with status `PROCESSING`, and returns
`{"status": "ignored"}` for anything else. Duplicate suppression comes from the existing
`job.status is not JobStatus.PROCESSING` guard inside each transaction.

`MockGenerationProvider` takes an optional `webhook_sender`; under `SUCCEED_VIA_WEBHOOK` it schedules
a delayed self-call and its `poll()` keeps returning `PENDING`.

- [x] **Step 4: Run the suites**

Run: `cd backend && python -m pytest tests -v && ruff check app tests && mypy app`

- [x] **Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: accept provider webhooks through the polling state machine"
```

---

### Task 7: Frontend character panel, batch controls, mode switch, and lineage

**Files:**
- Create: `frontend/src/features/scene/CharacterPanel.tsx`,
  `frontend/src/features/scene/BatchControls.tsx`, `frontend/src/app/ModeSwitch.tsx`
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`,
  `frontend/src/app/App.tsx`, `frontend/src/features/scene/SceneWorkspace.tsx`,
  `frontend/src/features/generations/CutCard.tsx`, `frontend/src/app/styles.css`
- Test: `frontend/src/features/scene/CharacterPanel.test.tsx`,
  `frontend/src/features/scene/BatchControls.test.tsx`,
  `frontend/src/features/generations/CutCard.test.tsx`

**Interfaces:**
- Consumes: scene detail with `characterProfiles`, jobs with `generationMode`/`referenceImageId`.
- Produces: `CharacterPanel`, `BatchControls`, `ModeSwitch`.

- [x] **Step 1: Write the failing tests**

```tsx
it("shows every scene character with its defining traits", () => {
  render(<CharacterPanel profiles={twoProfiles()} />);
  expect(screen.getByText("Mina")).toBeInTheDocument();
  expect(screen.getByText(/dark brown/i)).toBeInTheDocument();
});

it("reports batch progress across the six cuts", () => {
  render(<BatchControls cuts={cutsWithMixedImageStates()} sceneId="s1" />);
  expect(screen.getByText("Images 4/6 done · 1 failed")).toBeInTheDocument();
});

it("disables batch generation while the batch mutation is pending", async () => {
  renderBatch({ imageBatchRequest: deferredPromise() });
  const button = screen.getByRole("button", { name: "Generate all images" });
  await user.click(button);
  expect(button).toBeDisabled();
  await user.click(button);
  expect(receivedBatchRequests()).toHaveLength(1);
});

it("shows the generation mode and reference image of each job", () => {
  renderCut(cutWithReferencedImage());
  expect(screen.getByText("MOCK")).toBeInTheDocument();
  expect(screen.getByText(/Reference: Image v1/)).toBeInTheDocument();
});

it("offers Live only when the backend reports it is available", async () => {
  renderApp({ config: { generationMode: "MOCK", liveAvailable: false } });
  expect(await screen.findByRole("button", { name: "Live" })).toBeDisabled();
});
```

- [x] **Step 2: Run and observe failure**

Run: `cd frontend && npm run test:run`

- [x] **Step 3: Implement the components**

`CharacterPanel` renders one card per profile. `BatchControls` derives counts from the cuts' jobs and
posts to the scene batch endpoints. `ModeSwitch` calls `PUT /api/config` and invalidates `['config']`.
`CutCard` gains a mode badge, the shot description, and a reference-image line.

- [x] **Step 4: Run frontend verification**

Run: `cd frontend && npm run test:run && npm run lint && npm run build`

- [x] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat: surface characters, batch progress, mode switching, and job lineage"
```

---

### Task 8: Documentation, E2E, and full verification

**Files:**
- Modify: `README.md`, `.env.example`, `docker-compose.yml`,
  `frontend/e2e/prompt-to-animation.spec.ts`
- Test: whole suite

- [x] **Step 1: Extend the E2E scenario**

Add to the existing Mock scenario, after the six cuts appear: click `Generate all images`, wait for
six `Succeeded` image jobs, assert every cut's image job prompt region shows the same lead character
name, click `Generate all videos`, and assert the player reaches `6 of 6 videos ready`.

- [x] **Step 2: Document the new environment variables**

`GENERATION_CONCURRENCY=3`, `WEBHOOK_SECRET=`, `WEBHOOK_PUBLIC_URL=`, `SELF_BASE_URL=http://127.0.0.1:8000`,
`MOCK_WEBHOOK_DELAY_SEC=2` go into `.env.example`, the README variable table, and `docker-compose.yml`.

- [x] **Step 3: Document batch, character consistency, runtime switching, and webhook**

README gains: how batching works and why claiming is serial, how the character sheet and anchor image
produce consistency, how to switch modes at runtime, how to test the webhook in Mock, and the updated
requirement traceability table including `REQ-12` through `REQ-17`.

- [x] **Step 4: Run complete verification**

```bash
cd backend && ruff check app tests && mypy app && python -m pytest tests -v --cov=app
cd ../frontend && npm run lint && npm run test:run && npm run build
# with the Mock stack running
npm run test:e2e
```

- [x] **Step 5: Commit**

```bash
git add README.md .env.example docker-compose.yml frontend/e2e
git commit -m "docs: document batch, character consistency, runtime mode, and webhooks"
```

---

## Final acceptance gate

- Every `REQ-12` through `REQ-17` row has a passing automated test.
- Six cut prompts share one character sheet and one style guide, proven by test, not by eye.
- Batch runs process at most `GENERATION_CONCURRENCY` jobs per tick and never double-claim.
- The anchor gate cannot deadlock when cut 1 fails permanently.
- A duplicate webhook delivery creates no second artifact.
- Switching modes never redirects an in-flight job.
- The previously passing 131 backend and 41 frontend tests still pass.
