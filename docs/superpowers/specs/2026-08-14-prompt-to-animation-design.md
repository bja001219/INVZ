# Prompt-to-Animation Generator 설계 명세 — 면접 MVP 축소판

## 1. 목적과 설계 원칙

하나의 자연어 prompt를 Scene과 정확히 6개의 5초 Cut으로 변환하고, 각 Cut의 이미지와 비디오를 생성한 뒤 선택된 6개 비디오를 순서대로 재생하는 full-stack 면접 MVP를 만든다.

이 문서는 제한된 구현 시간과 안정적인 로컬 데모를 우선한다. 핵심 사용자 흐름을 직접 뒷받침하지 않는 Webhook, Batch, 런타임 mode 변경, 다중 worker, 범용 durable queue 추상화는 포함하지 않는다.

설계 원칙은 다음과 같다.

- 한 번에 설명할 수 있는 단일 데이터 흐름을 사용한다.
- 외부 비동기 API 완료 방식은 Polling 하나로 통일한다.
- Mock/Live mode는 프로세스 시작 시 환경변수로 결정하며 실행 중 바꾸지 않는다.
- 비동기 생성 작업은 SQLite에 저장하지만 worker는 단일 coroutine으로 제한한다.
- regenerate는 기존 결과를 보존하면서 새 generation version을 만드는 동작만 의미한다.
- 보안은 secret 비노출과 로컬 바인딩에 집중하고 인증은 추가하지 않는다.
- 원 과제 요구사항으로 추적되지 않는 기능은 추가하지 않는다.

## 2. 요구사항 근거와 traceability

원 과제 원문이나 평가표는 현재 저장소에 포함되어 있지 않다. 따라서 아래 표는 기존 설계 문서에 이미 반영된 핵심 사용자 요구와 이번 설계 축소 지시를 근거로 한 저장소 내부 source of truth다. 원문을 확보하기 전에는 새로운 기능을 필수 요구사항으로 간주하지 않는다.

| ID | 확인된 요구 | 현재 근거 | 설계 반영 | 검증 |
|---|---|---|---|---|
| `REQ-01` | prompt 하나로 Scene 생성 | 기존 명세 목적 및 포함 범위 | `POST /api/scenes` | Scene API/E2E |
| `REQ-02` | Scene은 정확히 6개 Cut, 각 5초 | 기존 명세 포함 범위 | Pydantic 및 DB constraint | schema/service test |
| `REQ-03` | Cut별 이미지 생성과 결과 선택 | 기존 명세 포함 범위 | versioned image job/artifact | generation API/E2E |
| `REQ-04` | 선택 이미지로 Cut 비디오 생성 | 기존 명세 포함 범위 | video job의 `source_image_id` | lineage test/E2E |
| `REQ-05` | retry, 진행 상태, 최종 실패 표시 | 기존 명세 목적 | 단일 Polling state machine | worker/UI test |
| `REQ-06` | regenerate 시 기존 결과 보존 | 기존 명세 포함 범위 | 다음 version 생성 | service/UI test |
| `REQ-07` | 선택된 6개 비디오 순차 재생 | 기존 명세 목적 | frontend sequence player | player/E2E |
| `REQ-08` | 외부 API 없이 데모 가능한 Mock | 기존 명세 포함 범위 | `GENERATION_MODE=mock` | 전체 자동 테스트 |
| `REQ-09` | 실제 OpenAI/Kie 연동 가능 | 기존 명세 포함 범위 | `GENERATION_MODE=live` provider | HTTP contract test |
| `REQ-10` | API Key 비노출 | 기존 명세 보안 범위 | backend env only/redaction | secret test/scan |
| `REQ-11` | 재현 가능한 로컬 실행 | 기존 명세 완료 조건 | Docker Compose/README | Compose smoke/E2E |

Webhook과 Batch는 원 과제 원문에서 요구된다는 근거가 저장소에 없으므로 제거한다. 원문이 나중에 제공되어 필수임이 확인되면 별도 scope 변경으로만 다시 추가한다.

## 3. 범위

### 3.1 포함

- React + TypeScript 단일 페이지 작업 공간
- FastAPI backend와 SQLite persistence
- OpenAI Responses API를 이용한 Scene/Cut 생성
- Kie image 및 image-to-video 생성
- Cut Image와 Cut Video의 version별 생성 및 선택
- 실패하거나 완료된 Cut의 regenerate
- 최대 3회의 명시적 retry와 최종 실패 상태
- 외부 generation task 상태 Polling
- 환경변수 기반 Mock/Live mode
- 성공 결과의 prompt 및 source image lineage
- 선택된 6개 Cut Video의 순차 재생
- Docker Compose 기반 로컬 실행
- Mock 기반 unit/integration/E2E 테스트

### 3.2 제외

- Webhook 및 callback endpoint
- Batch 생성
- runtime Mock/Live switch와 DB 설정 테이블
- Redis, Celery, RabbitMQ 또는 범용 queue abstraction
- 다중 backend process, 다중 Uvicorn worker, 분산 locking
- 별도 provider/model snapshot과 provider history migration
- Cut Video를 하나의 MP4로 합성하거나 다운로드하는 기능
- FFmpeg 및 자체 object storage
- 로그인, 권한, 사용자별 데이터 격리
- 공개 인터넷 배포
- 여러 prompt의 동시 생성 orchestration
- Scene/Cut 직접 편집

## 4. 기술 스택과 실행 제약

- Frontend: Node.js 22+, React, TypeScript, Vite, TanStack Query, Vitest, React Testing Library, Playwright
- Backend: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, HTTPX, OpenAI Python SDK, pytest
- Database: SQLite + `aiosqlite`
- Runtime: backend process 1개, Uvicorn worker 1개, generation worker coroutine 1개
- Infrastructure: frontend와 backend만 포함하는 Docker Compose
- Persistence: SQLite 파일을 backend Docker volume에 저장
- Media: Live 결과는 provider URL, Mock 결과는 backend의 `/media/mock/*` 정적 URL
- 모든 자동 테스트는 실제 OpenAI/Kie API를 호출하지 않는다.

## 5. 최소 아키텍처

```text
React SPA
   │ REST + active Scene 1초 polling
   ▼
FastAPI
   ├─ scene routes + operations
   ├─ generation routes + operations
   ├─ Provider adapters (Mock or Live, startup-fixed)
   └─ single GenerationWorker
             │
             ▼
           SQLite
```

`scenes.py`와 `generations.py`는 각 feature의 route와 짧은 transaction 함수를 함께 가진다. Provider adapter는 외부 payload 변환만 담당한다. GenerationWorker는 SQLite에서 due job 하나를 읽어 submit 또는 poll하고 상태를 갱신한다.

별도 repository 계층은 모든 feature에 의무적으로 만들지 않는다. transaction이나 복잡한 query를 재사용해야 하는 경우에만 feature 내부 함수로 둔다. ProviderFactory도 사용하지 않고 startup settings로 Mock 또는 Live adapter를 한 번 구성해 dependency로 주입한다.

## 6. 프론트엔드 UX

단일 페이지는 다음 영역으로 구성한다.

1. Prompt form
   - prompt 입력과 Scene 생성
   - 현재 startup mode를 읽기 전용으로 표시
   - Mock mode에서만 실패 시나리오 선택 표시
2. Scene summary
   - title, scenario, nominal duration 30초
3. Cut card 6개
   - order와 요청 duration 5초
   - image/video prompt
   - 이미지와 비디오 version 목록
   - 생성 또는 regenerate 버튼
   - 선택된 이미지와 비디오
   - generation status, attempt 수, retry 예정 시각, 최종 오류
   - 생성 입력과 source image lineage
4. Sequence player
   - 각 Cut의 선택된 성공 비디오를 order 순서로 재생
   - 6개 모두 준비되기 전에는 비활성화
   - 현재 Cut과 nominal 전체 진행률 표시

Scene ID는 URL query `?scene=<uuid>`에 유지한다. 새로고침하면 `GET /api/scenes/{id}`로 현재 Scene과 작업 상태를 복원한다. Scene이 바뀌면 frontend의 Cut 선택 및 player 상태를 초기화한다.

generation mutation이 pending인 동안 해당 버튼을 즉시 비활성화한다. 서버 상태와 무관한 짧은 double-click도 frontend에서 차단한다.

## 7. 데이터 모델

### 7.1 Scene

- `id`
- `user_prompt`
- `title`
- `scenario`
- `created_at`

### 7.2 Cut

- `id`
- `scene_id`
- `order`: 1부터 6
- `image_prompt`
- `video_prompt`
- `duration_sec`: 항상 5
- `selected_image_id`: nullable
- `selected_video_id`: nullable

Constraints:

- unique `(scene_id, order)`
- check `1 <= order <= 6`
- check `duration_sec = 5`

### 7.3 GenerationJob

- `id`
- `kind`: `IMAGE | VIDEO`
- `cut_id`
- `version`: Cut와 kind 안에서 증가
- `status`: `QUEUED | SUBMITTING | PROCESSING | RETRY_WAIT | SUCCEEDED | FAILED`
- `prompt`
- `source_image_id`: VIDEO만 사용
- `external_task_id`: nullable
- `attempt_count`
- `max_attempts`: 기본값 3
- `next_run_at`: nullable
- `attempt_deadline_at`: nullable
- `last_error_code`: nullable
- `last_error_message`: nullable
- `mock_scenario`: Mock mode에서만 nullable
- `created_at`, `updated_at`, `completed_at`

Constraints:

- unique `(cut_id, kind, version)`
- nonterminal status에만 적용되는 partial unique index `(cut_id, kind)`

provider와 model은 실행 환경의 adapter 설정이므로 Job에 복제하지 않는다. 사용자가 확인할 lineage에는 실제 입력 prompt, generation version, source image와 생성 시각만 저장한다.

### 7.4 CutImage

- `id`
- `cut_id`
- `generation_job_id`: unique
- `url`
- `input_prompt`
- `created_at`

### 7.5 CutVideo

- `id`
- `cut_id`
- `cut_image_id`
- `generation_job_id`: unique
- `url`
- `input_prompt`
- `created_at`

## 8. 핵심 흐름

### 8.1 startup mode

- `GENERATION_MODE`은 `mock` 또는 `live`이며 startup 이후 변경하지 않는다.
- Mock은 외부 API Key를 요구하지 않는다.
- Live는 `OPENAI_API_KEY`와 `KIE_API_KEY`가 모두 없으면 application startup을 실패시킨다.
- frontend는 `GET /api/config`에서 `{generationMode}`만 읽는다. Key 존재 여부나 값은 응답하지 않는다.
- Mock과 Live는 서로 다른 `DATABASE_URL`을 사용하는 것을 README 기본 예제로 명시한다.

### 8.2 Scene 생성

1. prompt를 trim하고 1자 이상 2,000자 이하인지 검증한다.
2. startup mode에 맞는 Scene provider를 호출한다.
3. 명시적으로 retryable인 connect error, HTTP 429, HTTP 5xx만 최대 3회 exponential backoff로 retry한다.
4. OpenAI 응답을 strict Pydantic schema로 검증한다.
5. 정확히 6개 Cut과 각 `durationSec=5`를 검증한다.
6. Scene과 Cut 6개를 한 transaction으로 저장한다.
7. 최종 실패 시 부분 Scene은 저장하지 않고 안정적인 `{code,message}` 오류를 반환한다.

### 8.3 generation 생성과 regenerate

이미지와 비디오는 같은 생성 endpoint를 사용한다.

- active job이 없으면 다음 version의 `QUEUED` job을 생성한다.
- active job이 있으면 `409 GENERATION_ALREADY_ACTIVE`를 반환한다.
- partial unique index가 동시 요청에서도 active job 하나만 허용한다.
- 이전 job과 artifact는 삭제하거나 덮어쓰지 않는다.
- 비디오는 요청 시점의 selected image를 `source_image_id`로 고정한다.
- regenerate는 terminal job 이후 같은 endpoint를 다시 호출하여 다음 version을 만드는 동작이다.
- HTTP request replay 전용 idempotency key는 이 로컬 MVP에 포함하지 않는다.

### 8.4 선택 일관성

- 같은 Cut의 성공 artifact만 선택할 수 있다.
- selected image를 다른 version으로 바꾸면 `selected_video_id`를 `null`로 초기화한다.
- selected video는 현재 selected image에서 생성된 video만 허용한다.
- 첫 성공 이미지/비디오는 아직 선택값이 없을 때만 자동 선택한다.

## 9. GenerationWorker와 retry

### 9.1 단일 worker

FastAPI lifespan에서 하나의 `GenerationWorker` loop만 실행한다. `run_once()`는 due job을 최대 하나 처리하고 반환한다. 동시 provider submit을 하지 않으며 다중 process는 지원하지 않는다.

```text
QUEUED
  └─ SUBMITTING
       ├─ accepted ──→ PROCESSING ──→ SUCCEEDED
       │                   ├─ still pending ──→ PROCESSING
       │                   └─ retryable task failure/deadline ──→ RETRY_WAIT
       ├─ clearly retryable rejection ──→ RETRY_WAIT
       ├─ uncertain submission ──→ FAILED
       └─ permanent error ──→ FAILED

RETRY_WAIT ── due and attempts remain ──→ SUBMITTING
```

### 9.2 retry 규칙

- retry 대상
  - request body가 전송되기 전의 connect failure
  - 명시적인 HTTP 429 또는 5xx 응답
  - provider가 retryable로 분류한 task failure
  - accepted task가 attempt deadline을 초과한 경우
- 같은 task를 poll하는 일시적 GET timeout/5xx는 새 generation attempt를 소비하지 않는다.
- POST body 전송 뒤 response를 받지 못한 read timeout은 `SUBMISSION_UNCERTAIN`으로 최종 실패시킨다. 자동 resubmit하지 않는다.
- HTTP 400, 401, 403, 404, 422, schema 오류, 지원하지 않는 state는 즉시 실패한다.
- retry는 기본 최대 3 attempts이며 `RETRY_BASE_DELAY_SEC * 2^(attempt_count-1)` backoff를 사용한다.
- `Retry-After`가 숫자 초로 제공되면 exponential delay보다 큰 값을 사용한다.
- worker restart 시 stale `SUBMITTING`은 `SUBMISSION_UNCERTAIN`으로 실패시킨다. `QUEUED`, due `RETRY_WAIT`, task ID가 있는 `PROCESSING`은 복구한다.

Mock retry 결과는 process memory counter가 아니라 DB의 `attempt_count`로 결정한다.

## 10. 외부 API contract

### 10.1 OpenAI Scene provider

- Responses API와 `gpt-5.4-mini`를 사용한다.
- strict JSON schema는 `SceneDraft`에서 생성한다.
- request contract test는 model, prompt, structured output schema를 검증한다.
- response contract test는 정상 응답, 429, 5xx, malformed structured output을 검증한다.
- SDK 내부 retry는 비활성화하고 application retry 횟수만 사용한다.
- 명시적인 connect/read timeout을 설정한다.

### 10.2 Kie generation provider

- image model: `google/nano-banana`
- video model: `kling-2.6/image-to-video`
- submit response는 HTTP status뿐 아니라 provider business code와 `taskId` 존재를 검증한다.
- polling response는 known pending/success/failure state만 허용한다.
- success는 비어 있지 않은 `resultUrls[0]`과 HTTP(S) URL을 요구한다.
- malformed JSON, empty result, unknown state는 정규화된 provider contract error로 처리한다.
- request/response contract test는 실제 문서의 field name을 고정된 HTTP stub으로 검증한다.

## 11. API 계약

| Method | Endpoint | 목적 |
|---|---|---|
| `GET` | `/api/config` | startup generation mode 조회 |
| `POST` | `/api/scenes` | prompt로 Scene/Cut 생성 |
| `GET` | `/api/scenes/{id}` | Cut, generation, artifact, 선택 상태 조회 |
| `POST` | `/api/cuts/{id}/images` | 이미지 생성 또는 regenerate |
| `POST` | `/api/cuts/{id}/videos` | 선택 이미지로 비디오 생성 또는 regenerate |
| `PUT` | `/api/cuts/{id}/selected-image` | 성공 이미지 선택 |
| `PUT` | `/api/cuts/{id}/selected-video` | 현재 이미지 기반 성공 비디오 선택 |
| `GET` | `/health` | container health check |

generation endpoint는 `202 Accepted`와 생성된 Job을 반환한다. Scene 생성은 `201 Created`를 반환한다.

모든 application 오류는 다음 형식을 사용한다.

```json
{
  "code": "STABLE_APPLICATION_CODE",
  "message": "User-safe explanation"
}
```

FastAPI validation error, missing resource, domain conflict와 provider error를 공통 exception handler로 위 형식에 맞춘다. stack trace, provider response body와 request header는 응답에 포함하지 않는다.

## 12. CORS, media와 보안

- `.gitignore`와 `.env.example`은 첫 application commit 전에 만든다.
- `OPENAI_API_KEY`와 `KIE_API_KEY`는 backend environment에서만 읽는다.
- Settings와 provider client의 repr에서 secret 값을 숨긴다.
- Authorization header와 설정된 secret은 log filter에서 redaction한다.
- provider 오류 응답 원문은 사용자 응답에 전달하지 않는다.
- CORS는 정확히 `FRONTEND_ORIGIN` 하나만 허용하며 credentials는 비활성화한다.
- Mock fixture는 FastAPI `StaticFiles`로 `/media/mock`에 mount하고 실제 GET test를 둔다.
- Docker Compose host port는 `127.0.0.1`에만 publish한다.
- 인증이 없는 trusted local evaluation app이며 공개 URL, 공용 LAN, production에 배포하지 않는다는 범위를 README 첫 부분에 명시한다.

## 13. 테스트 전략

### 13.1 Backend

- 정확히 6개 Cut과 5초 constraint
- Scene transaction rollback과 retryable error retry
- OpenAI request/response contract
- Kie image/video submit 및 polling contract
- malformed provider payload와 unknown state
- active generation partial unique constraint와 concurrent create
- regenerate version 증가와 기존 artifact 보존
- source image lineage와 선택 일관성
- retry backoff, attempt limit, uncertain submission 무재시도
- restart recovery와 Mock attempt persistence
- stable API error body
- CORS 허용/거부 origin
- `/media/mock` fixture serving
- API 응답, 로그와 settings repr의 secret 비노출

Worker는 `run_once()`와 주입 가능한 clock을 사용해 실제 sleep 없이 테스트한다.

### 13.2 Frontend

- startup mode 읽기 전용 표시
- mutation pending 중 중복 click 차단
- generation 상태, attempt, retry, failure 표시
- terminal job regenerate와 version 선택
- image 변경 시 incompatible video 선택 초기화
- URL의 Scene ID로 새로고침 복원
- Scene 전환 시 local state reset
- 6개 선택 비디오의 순차 재생
- video load/error 및 play promise rejection 표시

### 13.3 E2E

Mock mode에서 다음 한 흐름을 검증한다.

1. prompt로 Scene 생성
2. 정확히 6개 Cut 확인
3. 각 Cut 이미지 생성 및 선택
4. retryable 실패 후 성공 상태 확인
5. 한 비디오의 최종 실패 확인
6. 실패 비디오 regenerate 후 새 version 성공 확인
7. 모든 Cut 비디오 생성 및 선택
8. 순차 player 활성화와 Cut 전환 확인
9. 새로고침 후 같은 Scene 복원 확인

## 14. 명시적인 trade-off

- 인증은 구현하지 않는다. 대신 localhost-only Docker port와 trusted local evaluation 범위를 강제한다.
- 외부 provider가 idempotency를 제공하지 않는 POST의 ambiguous acceptance를 정확히 복구하지 않는다. 중복 과금보다 자동 retry 중단과 명시적 실패를 선택한다.
- HTTP replay idempotency key는 구현하지 않는다. active job 중복만 DB constraint로 막는다.
- provider media URL 만료는 허용한다. 자체 object storage는 범위 밖이다.
- 각 비디오에 5초를 요청하지만 실제 파일 duration을 inspect/trim하지 않는다. 결과는 nominal 30초 sequence이며 정확한 30초 MP4를 보장하지 않는다.
- 단일 process와 단일 worker만 지원한다. 수평 확장은 범위 밖이다.

## 15. Grill review disposition

| # | 분류 | 판단 |
|---|---|---|
| 1 | `NOT APPLICABLE` | 현재 단계는 사용자가 명시한 설계/계획 단계이며 구현 부재는 이번 문서 수정의 결함이 아니다. |
| 2 | `FIX` | 요구 ID와 설계·테스트 mapping을 이 문서에 추가하고 원문 부재를 명시했다. |
| 3 | `FIX` | ambiguous submit은 자동 retry하지 않고 `SUBMISSION_UNCERTAIN`으로 종료한다. |
| 4 | `REMOVE BY SIMPLIFICATION` | 단일 worker와 단일 process로 줄이고 Webhook/병렬 claim 경쟁을 제거했다. |
| 5 | `ACCEPTED TRADE-OFF` | 인증은 추가하지 않고 localhost-only trusted evaluation 범위를 명시한다. |
| 6 | `FIX` | 명시적인 CORS 설정과 Mock media mount/test를 추가했다. |
| 7 | `FIX` | active job constraint와 terminal 이후 새 version이라는 regenerate 계약을 고정했다. |
| 8 | `FIX` | 첫 active job 직후 v2 생성을 허용하던 모순을 제거했다. |
| 9 | `REMOVE BY SIMPLIFICATION` | Webhook과 callback deadline 경로 전체를 제거했다. |
| 10 | `FIX` | `.gitignore`, env contract, redaction과 secret test를 프로젝트 생성 첫 단계로 이동했다. |
| 11 | `FIX` | Live startup key validation과 OpenAI/Kie HTTP contract test를 명시했다. |
| 12 | `FIX` | business error, malformed JSON, missing field, unknown state와 retry semantics를 정의했다. |
| 13 | `FIX` | Mock retry를 persisted `attempt_count`로 결정한다. |
| 14 | `REMOVE BY SIMPLIFICATION` | provider/model snapshot과 그에 따른 lineage 복잡성을 제거했다. |
| 15 | `FIX` | runtime mode 변경을 제거하고 image 선택 변경 시 incompatible video 선택을 해제한다. |
| 16 | `FIX` | stable error contract를 추가하고 Batch 관련 부분은 기능과 함께 제거했다. |
| 17 | `FIX` | Scene ID를 URL에 유지해 새로고침 후 복원한다. |
| 18 | `FIX` | mutation pending disable, Scene state reset과 관련 frontend test를 추가했다. |
| 19 | `ACCEPTED TRADE-OFF` | 정확한 30초 보장을 제거하고 6×5초 요청 기반 nominal sequence로 표현한다. |
| 20 | `REMOVE BY SIMPLIFICATION` | Webhook, Batch, RuntimeSetting, provider factory, 병렬 runner와 의무적 repository 계층을 제거했다. |

## 16. 완료 조건

- Docker Compose 한 명령으로 localhost Mock demo가 실행된다.
- prompt가 정확히 6개의 5초 Cut으로 변환된다.
- Cut Image와 Cut Video를 생성, regenerate, 선택할 수 있다.
- retryable failure, retry 간격과 최종 실패가 UI와 테스트에서 확인된다.
- 외부 비동기 generation은 Polling 하나로 처리된다.
- Live provider의 실제 request/response 형식이 HTTP contract test로 고정된다.
- CORS와 Mock generated media serving이 자동 테스트된다.
- 선택된 6개 비디오를 순차 재생할 수 있다.
- 새로고침 후 URL의 Scene을 복원한다.
- frontend, API 응답, 로그와 Git 추적 파일에 실제 API Key가 없다.
- README는 local-only 범위, 실행 방법, 환경변수, retry/regenerate 의미와 accepted trade-off를 설명한다.
