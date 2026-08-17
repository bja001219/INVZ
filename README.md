> 로컬 평가 전용입니다. 이 앱에는 인증이 없으므로 공용 인터넷이나 신뢰할 수 없는 LAN에 노출하면 안 됩니다.
> Docker Compose는 두 포트를 모두 `127.0.0.1`에만 게시합니다.

# InvzAssign Prompt-to-Animation MVP

자연어 프롬프트 한 줄이 정확히 6개, 각 5초짜리 Cut을 가진 Scene이 됩니다. 각 Cut은 버전이 매겨진
이미지와 영상을 생성하고, 한정된 범위 안에서 실패를 재시도하며, 이전 버전을 절대 지우지 않습니다.
마지막에는 선택된 6개 영상이 Cut 순서대로 이어서 재생됩니다.

- 설계 스펙: [`docs/superpowers/specs/2026-08-14-prompt-to-animation-design.md`](docs/superpowers/specs/2026-08-14-prompt-to-animation-design.md)
- 배치·캐릭터 일관성·런타임 모드·웹훅 설계: [`docs/superpowers/specs/2026-08-16-batch-character-consistency-design.md`](docs/superpowers/specs/2026-08-16-batch-character-consistency-design.md)
- 구현 계획: [`docs/superpowers/plans/2026-08-14-prompt-to-animation.md`](docs/superpowers/plans/2026-08-14-prompt-to-animation.md)
- AI 코딩 에이전트 규칙: [`AGENTS.md`](AGENTS.md) (그리고 이 파일을 가리키는 [`CLAUDE.md`](CLAUDE.md))

과제가 요구한 네 항목은 각각 다음 절에 있습니다.
**실행 방법** → [실행 방법](#실행-방법) ·
**환경변수 목록** → [환경변수](#환경변수) ·
**설계 설명** → [설계](#설계) ·
**테스트·검증 방법** → [검증](#검증).
동작을 눈으로 확인하고 싶다면 [데모 시나리오](#데모-시나리오)부터 보시면 됩니다.

## 범위와 안전

신뢰된 로컬 환경에서만 평가하는 앱입니다. 로그인도, 권한 모델도, 사용자별 데이터 격리도 없습니다.
Compose는 게시하는 두 포트를 모두 루프백에 묶으므로 다른 기기에서는 어느 서비스에도 접근할 수
없습니다. 공개 URL, 공유 LAN, 운영 환경에 배포하지 마십시오.

`OPENAI_API_KEY`와 `KIE_API_KEY`는 백엔드 환경변수에서만 읽습니다. 어떤 API도 이 값을 반환하지 않고,
프론트엔드로 전달되지 않으며, 추적되는 파일에 기록되지 않습니다. 로그에서는 `WEBHOOK_SECRET`,
`Authorization: Bearer …` 헤더와 함께 마스킹됩니다.

마스킹은 로거에 필터를 붙이는 대신 **프로세스의 log record factory 자체를 교체**하는 방식입니다.
루트 로거에 붙인 필터는 루트로 직접 기록된 레코드에만 적용됩니다. 자식 로거가 만든 레코드는 상위
로거의 *핸들러*에는 전달되지만 상위 *필터*에는 전달되지 않습니다. Uvicorn은 자기 로거에 전용 핸들러를
달고 `propagate = False`로 두며 루트에는 핸들러를 남기지 않으므로, 루트 필터는 웹훅 토큰이 실려 있는
액세스 로그 한 줄조차 보지 못합니다.

## 실행 방법

### Mock 모드 빠른 시작 (API 키 불필요)

```bash
cp .env.example .env      # compose가 이 파일을 읽습니다
docker compose up --build -d
# http://localhost:5173 접속
docker compose down
```

`.env` 없이도 스택은 그대로 뜹니다. 각 변수는 compose에 적힌 기본값으로 떨어지고,
`WEBHOOK_SECRET`만 비어 있게 되어 `POST /api/webhooks/kie`가 비활성화됩니다. 즉 키가 없을 때
못 하는 일은 `SUCCEED_VIA_WEBHOOK` 시나리오 하나뿐입니다.

`8000`이나 `5173`을 이미 다른 프로세스가 쓰고 있으면 컨테이너 기동이
`Bind for 0.0.0.0:8000 failed: port is already allocated`로 실패합니다. 두 포트는 compose 토폴로지에
묶여 있어 변수로 바꿀 수 없으므로, 점유 중인 쪽을 내리거나 `docker-compose.yml`의 `ports`를 직접
고쳐야 합니다.

Compose 스택은 `GENERATION_MODE=mock`으로 시작하고, API 키를 요구하지 않으며, SQLite 파일을 명명
볼륨 `backend-data`에 두고, 백엔드의 `GET /health`가 healthy가 된 뒤에야 프론트엔드를 띄웁니다.
Mock 결과물은 백엔드가 `/media/mock/*`에서 직접 서빙합니다.

`DATABASE_URL`, `FRONTEND_ORIGIN`, `SELF_BASE_URL` 세 개는 compose에서 **일부러 덮어쓸 수 없게**
고정했습니다. 이 값들은 취향이 아니라 compose 토폴로지를 서술하기 때문입니다. DB 경로는
`backend-data` 마운트 안에 있어야 `down` 이후에도 데이터가 남고, CORS origin과 Mock 자기 콜백 주소는
아래에서 게시하는 포트와 일치해야 합니다.

### Docker 없이 실행

`Settings`는 `env_file=".env"`를 **프로세스의 작업 디렉터리 기준**으로 해석합니다. 백엔드를
`backend/`에서 띄우는 이 절차에서는 루트의 `.env`가 아니라 `backend/.env`가 읽힙니다. 따라서 설정
파일을 `backend/` 안에 두는 것이 이 경로에서 유일하게 동작하는 방법입니다. (루트 `.env`만 만들고
`backend/`에서 uvicorn을 띄우면 `WEBHOOK_SECRET`이 빈 값이 되어 웹훅 데모가 완료되지 않습니다.)

```bash
# backend
cd backend
cp ../.env.example .env       # 이 파일이 읽히는 설정 파일입니다
python -m venv .venv          # 시스템 파이썬은 PEP 668로 직접 설치를 거부합니다
source .venv/bin/activate     # Windows: .venv/Scripts/activate
python -m pip install -e ".[dev]"
alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

# frontend (두 번째 셸)
cd frontend
npm ci
npm run dev
```

이렇게 띄우면 SQLite 파일은 `backend/data/` 아래에 생깁니다. 모드별로 프로세스를 따로 굴릴 때는
파일도 분리해서 Live 실행이 Mock 행을 물려받지 않게 하십시오. (compose는 `DATABASE_URL`을 고정하므로
이 설정은 Docker 없이 실행할 때만 의미가 있습니다.)

```dotenv
# Mock
DATABASE_URL=sqlite+aiosqlite:///./data/app-mock.db
```

반대로 한 프로세스가 런타임에 모드를 바꾸면 두 모드의 산출물이 필연적으로 한 파일에 섞입니다.
그래도 괜찮습니다. 모든 job이 자기를 만든 모드를 기록하고, 모드를 넘나드는 조합은
`409 ARTIFACT_MODE_MISMATCH`로 거부되기 때문입니다.

### Live 모드

Live는 Scene 초안 작성에 OpenAI를, 이미지와 image-to-video 생성에 Kie를 호출합니다.

```dotenv
# Live — 두 키는 본인 환경에만 두고 추적되는 파일에는 절대 넣지 마십시오
GENERATION_MODE=live
OPENAI_API_KEY=
KIE_API_KEY=
DATABASE_URL=sqlite+aiosqlite:///./data/app-live.db
```

**Live는 실제로 과금되며 이 앱에는 비용 가드가 없습니다.** "Generate all images" 다음에 "Generate all
videos"를 누르면 클릭 두 번으로 실제 프로바이더 호출 12건이 나갑니다. 확인 단계도, 사용량 카운터도
없습니다. Live를 점검할 때는 단일 Cut 버튼을 쓰십시오.

`GENERATION_MODE`는 프로세스가 **시작할 때의** 모드를 정할 뿐입니다. 실행 중에는 `PUT /api/config`로
바꿀 수 있고, 각 job은 생성 시점에 찍힌 모드 스냅숏을 따릅니다.
[런타임 Mock/Live 전환](#런타임-mocklive-전환)을 보십시오.

리뷰에 가장 쓸모 있는 구성은 **`GENERATION_MODE=mock` + 두 키가 모두 있는 상태**입니다. 앱은 안전하게
시작하고, 아무것도 과금되지 않으며, 헤더의 Live 버튼이 활성화되어 런타임 전환을 실수가 아니라 의도적으로
보여줄 수 있습니다.

```dotenv
GENERATION_MODE=mock        # Mock으로 시작
OPENAI_API_KEY=…            # 값이 있으므로 `liveAvailable`이 true가 되고 Live를 고를 수 있습니다
KIE_API_KEY=…
```

**compose에서는 이 두 키를 반드시 루트 `.env`에 넣어야 합니다.** compose는 루트 `.env`만 읽고
`backend/.env`는 읽지 않습니다. `backend/.env`에 키가 들어 있어도 `docker compose config`는 두 키를
모두 `""`로 렌더링하고, 그러면 `liveAvailable`은 계속 false입니다.

시작 시 거부되는 경우는 `GENERATION_MODE=live`인데 키가 빠졌을 때뿐입니다. Live로 뜨기로 한 프로세스가
조용히 Mock 데이터를 내보내는 일은 없습니다.

한 프로세스가 DB 하나를 소유하므로, 런타임에 모드를 바꾸면 두 모드의 산출물이 같은 파일에 쌓입니다.
의도된 동작이고, 섞어 쓰는 것만 명시적으로 거부합니다 — [모드 간 산출물](#모드-간-산출물) 참조.

모델은 고정입니다. `gpt-5.4-mini`(Scene), `google/nano-banana`(이미지),
`kling-2.6/image-to-video`(영상).

## 환경변수

[`.env.example`](.env.example)을 복사해서 쓰십시오. 변수 이름과 비밀이 아닌 기본값, 그리고 이 앱으로
오는 콜백만 인증하는 로컬 데모용 `WEBHOOK_SECRET` 하나가 들어 있습니다. 외부 서비스의 자격증명이
아닙니다. API 키 두 개는 일부러 비워 두었습니다.

`docker-compose.yml`의 백엔드 튜너블은 전부 `${VAR:-기본값}` 형태로 환경(=루트 `.env`)에서 보간합니다.
따라서 이 파일에는 리터럴이 박혀 있지 않고, 변수를 설정하지 않은 리뷰어도 아래 표의 기본값 그대로 도는
Mock 스택을 얻습니다. 예외는 위에서 설명한 `DATABASE_URL` / `FRONTEND_ORIGIN` / `SELF_BASE_URL`
세 개로, compose가 고정합니다.

| 변수 | 기본값 | 의미 |
|---|---|---|
| `GENERATION_MODE` | `mock` | 프로세스가 **시작할** 모드(`mock` 또는 `live`). 실행 중에는 `PUT /api/config`로 변경 |
| `OPENAI_API_KEY` | *(빈 값)* | 백엔드 전용 비밀값. Live에 필수 |
| `KIE_API_KEY` | *(빈 값)* | 백엔드 전용 비밀값. Live에 필수 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/app-mock.db` | SQLite 파일 경로. 모드별로 분리 권장(compose에서는 고정) |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | 허용하는 유일한 CORS origin |
| `GENERATION_MAX_ATTEMPTS` | `3` | 최종 실패 전까지 job 하나에 허용되는 시도 횟수 |
| `GENERATION_CONCURRENCY` | `3` | 프로바이더에 **동시에 물려 있을 수 있는** job 수(SUBMITTING·PROCESSING 합계) |
| `RETRY_BASE_DELAY_SEC` | `1` | `base * 2^(attempt-1)` 백오프의 기준값 |
| `PROVIDER_POLL_INTERVAL_SEC` | `1` | 워커의 유휴 간격이자 다음 폴링까지의 지연 |
| `GENERATION_ATTEMPT_TIMEOUT_SEC` | `300` | 수락된 프로바이더 작업 하나의 마감. kling-2.6 image-to-video의 실측 소요가 3분 30초~4분이라 이전 기본값 120초는 정상 지연 중에 만료되어 모든 영상을 두 번 청구시켰습니다 |
| `WEBHOOK_SECRET` | *(빈 값)* | `POST /api/webhooks/kie`의 공유 비밀값. 비어 있으면 라우트가 비활성화됩니다 |
| `WEBHOOK_PUBLIC_URL` | *(빈 값)* | Kie에 `callBackUrl`로 넘기는 공개 콜백 URL. `?token=<WEBHOOK_SECRET>`으로 끝나야 하며 아니면 Live 콜백이 전부 거부됩니다 |
| `SELF_BASE_URL` | `http://127.0.0.1:8000` | Mock 모드가 자기 자신에게 콜백을 보낼 주소 |
| `MOCK_WEBHOOK_DELAY_SEC` | `1` | Mock 콜백 전에 흉내 내는 프로바이더 지연 |
| `VITE_API_BASE_URL` | `http://localhost:8000` | 프론트엔드 빌드 타임 백엔드 URL (비밀 아님) |
| `E2E_BASE_URL` | `http://localhost:5173` | 테스트 전용. Playwright가 붙을 주소(`frontend/playwright.config.ts`) |

## 설계

```text
React SPA
   │ REST + 비종결 job이 하나라도 있으면 1초 폴링
   ▼
FastAPI (프로세스 1개, Uvicorn 워커 1개)
   ├─ scenes.py       라우트 + 짧은 트랜잭션
   ├─ generations.py  라우트 + 짧은 트랜잭션
   ├─ batches.py      Scene 단위 배치 등록(스케줄러가 아니라 장부)
   ├─ webhooks.py     콜백 인증과 정규화, 상태 기계는 워커 것을 그대로 사용
   ├─ runtime.py      런타임 모드와 프로바이더 레지스트리
   ├─ anchor.py       Scene 앵커 게이트, 순수 함수 하나
   ├─ prompting.py    결정적 프롬프트 합성, 순수 함수
   ├─ providers/      Mock/Live 어댑터. job의 모드 스냅숏으로 매번 해석
   └─ GenerationWorker (코루틴 1개, 프로바이더에 동시에 물린 job은 최대 GENERATION_CONCURRENCY)
             │
             ▼
           SQLite
```

외부 비동기 완료는 폴링으로 몰아가고, 프로바이더 웹훅이 있으면 **같은 상태 기계**에 결과를 흘려
넣습니다. Redis/Celery/RabbitMQ도, 오브젝트 스토리지도, FFmpeg도 쓰지 않습니다.

| Method | Endpoint | 용도 |
|---|---|---|
| `GET` | `/api/config` | 현재 생성 모드와 Live 사용 가능 여부 |
| `PUT` | `/api/config` | 실행 중 생성 모드 전환 |
| `POST` | `/api/scenes` | Scene과 Cut 6개 생성 (`201`) |
| `GET` | `/api/scenes/{id}` | Cut, job, 산출물, 선택 상태 |
| `POST` | `/api/cuts/{id}/images` | 이미지 생성/재생성 (`202`) |
| `POST` | `/api/cuts/{id}/videos` | 영상 생성/재생성 (`202`) |
| `POST` | `/api/scenes/{id}/images` | 배치: Cut마다 이미지 job 하나 (`202`) |
| `POST` | `/api/scenes/{id}/videos` | 배치: 선택 이미지가 있는 Cut마다 영상 job 하나 (`202`) |
| `POST` | `/api/webhooks/kie` | 프로바이더 콜백. 폴링과 같은 상태 기계를 공유 |
| `PUT` | `/api/cuts/{id}/selected-image` | 성공한 이미지 선택 |
| `PUT` | `/api/cuts/{id}/selected-video` | 선택된 이미지로 만든 영상 선택 |
| `GET` | `/health` | 컨테이너 헬스체크 |

애플리케이션 오류는 스택 트레이스도, 프로바이더 응답 본문도, 요청 헤더도 없이 한 가지 형태만
사용합니다.

```json
{ "code": "STABLE_APPLICATION_CODE", "message": "User-safe explanation" }
```

### 오류 코드

HTTP 응답으로 나가는 코드입니다. 새 코드를 만들면 이 표에 추가합니다.

| 코드 | HTTP | 의미 |
|---|---|---|
| `WEBHOOK_UNAUTHORIZED` | 401 | 헤더·쿼리 토큰 어느 쪽도 비밀값과 일치하지 않습니다 |
| `SCENE_NOT_FOUND` | 404 | Scene이 없습니다 (`scenes.py`, `batches.py`) |
| `CUT_NOT_FOUND` | 404 | Cut이 없습니다 |
| `IMAGE_NOT_FOUND` | 404 | 그 Cut에 속한, 성공한 이미지가 아닙니다 |
| `VIDEO_NOT_FOUND` | 404 | 그 Cut에 속한, 성공한 영상이 아닙니다 |
| `ROUTE_NOT_FOUND` | 404 | 존재하지 않는 라우트 |
| `GENERATION_ALREADY_ACTIVE` | 409 | 같은 Cut·종류에 이미 활성 job이 있습니다 |
| `SELECTED_IMAGE_REQUIRED` | 409 | 영상 생성에 필요한 "성공한 선택 이미지"가 없습니다 |
| `ARTIFACT_MODE_MISMATCH` | 409 | 다른 모드에서 만든 이미지를 소스로 요청했습니다 |
| `VIDEO_SOURCE_MISMATCH` | 409 | 현재 선택된 이미지에서 나온 영상이 아닙니다 |
| `LIVE_MODE_UNAVAILABLE` | 409 | 키가 없어 Live를 구성할 수 없습니다 |
| `REQUEST_VALIDATION_FAILED` | 422 | 요청 본문/파라미터 검증 실패 |
| `GENERATION_REQUEST_INVALID` | 422 | Live 모드에서 `mockScenario`를 보냈습니다 |
| `WEBHOOK_PAYLOAD_INVALID` | 422 | 콜백 payload에 `taskId`가 없거나 정규화에 실패했습니다 |
| `SCENE_SCHEMA_INVALID` | 502 | 재시도를 모두 쓰고도 모델 응답이 스키마를 통과하지 못했습니다 |
| `SCENE_PROVIDER_UNAVAILABLE` | 502 | 재시도를 모두 쓴 전송 계층 장애 |
| `SCENE_PROVIDER_FAILED` | 502 | Scene 프로바이더의 영구 오류 |
| `WEBHOOK_DISABLED` | 503 | `WEBHOOK_SECRET`이 비어 라우트가 꺼져 있습니다 |
| `HTTP_ERROR` | 원래 상태 코드 | 위에 해당하지 않는 Starlette HTTP 예외의 공통 봉투 |

아래 코드들은 HTTP 응답으로 나가지 않고 생성 job의 `lastErrorCode`로 남습니다. Scene 응답에
그대로 실려 오며, UI의 job 이력에는 함께 저장된 `lastErrorMessage`가 표시됩니다. (배치 `skipped`의
`reason`은 예외로, 위 HTTP 표의 코드를 그대로 담아 UI에 노출됩니다.)

| 코드 | 언제 기록되는가 |
|---|---|
| `SUBMISSION_UNCERTAIN` | POST 본문을 보낸 뒤 읽기 타임아웃. 또는 틱 시작 시점에 아직 `SUBMITTING`으로 남아 있던 job |
| `ATTEMPT_TIMEOUT` | 수락된 작업이 `GENERATION_ATTEMPT_TIMEOUT_SEC`를 넘김 |
| `SOURCE_IMAGE_MISSING` | 영상 job의 소스 이미지 행이 사라짐 (claim 시점 또는 완료 시점) |
| `CUT_MISSING` | 완료 시점에 대상 Cut이 사라짐 |
| `PROVIDER_RESPONSE_INVALID` | 성공 응답인데 결과 URL이 비어 있음 |
| `GENERATION_PROVIDER_FAILED` | 프로바이더가 실패를 알렸으나 코드를 주지 않았을 때의 폴백 |
| `KIE_SUBMIT_RETRYABLE` · `KIE_POLL_RETRYABLE` | 연결 실패, HTTP 429/5xx |
| `KIE_REQUEST_FAILED` · `KIE_REQUEST_INVALID` · `KIE_RESPONSE_INVALID` · `KIE_TASK_FAILED` | Kie 어댑터의 영구 오류와 작업 실패 |
| `MOCK_GENERATION_FAILED` · `MOCK_TASK_INVALID` | Mock 시나리오가 만들어 내는 실패 |

Scene 초안용 OpenAI 어댑터의 코드(`OPENAI_RETRYABLE`, `OPENAI_CONNECTION_FAILED`,
`OPENAI_REQUEST_FAILED`, `OPENAI_RESPONSE_INVALID`)는 프로세스 밖으로 나가지 않습니다. Scene 생성에는
job 행이 없어서, 위 `SCENE_*` 코드로 변환된 뒤에만 노출됩니다.

### 폴링 상태 기계

워커가 실제로 밟는 전이 전부입니다. 상태 값은 `models.py`의 `JobStatus`와 같습니다.

```text
QUEUED
  ├─ 예산이 남고 앵커 조건 충족 → SUBMITTING
  └─ 영상 job의 소스 이미지 행이 없음 → FAILED (SOURCE_IMAGE_MISSING)

RETRY_WAIT
  ├─ 재시도 시각 도래, 예산이 남음 → SUBMITTING
  └─ 영상 job의 소스 이미지 행이 없음 → FAILED (SOURCE_IMAGE_MISSING)

SUBMITTING
  ├─ 수락됨 → PROCESSING
  ├─ 재시도 가능한 거절 → RETRY_WAIT  (시도 소진이면 FAILED)
  ├─ 본문 전송 후 읽기 타임아웃 → FAILED (SUBMISSION_UNCERTAIN)
  ├─ 영구 오류 → FAILED
  └─ 틱 시작 시점에 아직 SUBMITTING(재시작 복구) → FAILED (SUBMISSION_UNCERTAIN)

PROCESSING
  ├─ 결과 URL 있음, Cut·소스 이미지 온전 → SUCCEEDED
  ├─ 아직 대기 중 / 폴링 중 일시적 오류 → PROCESSING  (폴링 재예약, 시도 소모 없음)
  ├─ 재시도 가능한 작업 실패 또는 마감 초과 → RETRY_WAIT  (시도 소진이면 FAILED)
  ├─ 재시도 불가한 작업 실패 → FAILED
  ├─ 폴링 중 영구 프로바이더 오류 → FAILED
  ├─ 성공 payload인데 결과 URL이 비어 있음 → FAILED (PROVIDER_RESPONSE_INVALID)
  ├─ 완료 시점에 Cut 행이 없음 → FAILED (CUT_MISSING)
  └─ 완료 시점에 영상 job의 소스 이미지가 없음 → FAILED (SOURCE_IMAGE_MISSING)
```

"시도 소진"은 별도의 전이가 아니라 판정 시점의 조건입니다. 실패를 처리하는 순간 job은 `SUBMITTING`
아니면 `PROCESSING`이고, `attempt_count >= max_attempts`이면 `RETRY_WAIT` 대신 곧바로 `FAILED`로
떨어집니다. 반대로 `QUEUED`나 `RETRY_WAIT`에서 곧장 `FAILED`로 가는 유일한 경로는 claim 시점에 영상
job의 소스 이미지가 사라진 경우입니다. 앵커를 기다리는 이미지 job은 아무 것도 쓰지 않고 건너뛰므로
원래 상태에 그대로 머무릅니다.

### 실패 분류

- **재시도 가능** — 요청 본문 전송 전의 연결 실패, 명시적인 HTTP 429 또는 5xx, 재시도 가능 표시가 붙은
  프로바이더 작업 실패, 그리고 수락된 작업이 `GENERATION_ATTEMPT_TIMEOUT_SEC`를 넘긴 경우.
  재시도는 `GENERATION_MAX_ATTEMPTS`에서 멈추고 간격은
  `RETRY_BASE_DELAY_SEC * 2^(attempt_count - 1)`입니다. 숫자형 `Retry-After`가 더 크면 그쪽을 씁니다.
- **영구** — 429와 5xx를 제외한 모든 비2xx HTTP 응답(400·401·403·404·422 등), 스키마 위반, 알 수 없는
  프로바이더 상태는 즉시 실패입니다.
- **불확실한 제출** — POST 본문을 이미 보낸 *뒤* 발생한 읽기 타임아웃은 `SUBMISSION_UNCERTAIN`이 되고
  절대 재제출하지 않습니다. 프로바이더가 멱등성 키를 제공하지 않으므로 눈 감고 재시도하면 두 번
  청구될 수 있습니다. 재시작 때문에 `SUBMITTING`으로 남은 행도 같은 방식으로 정리합니다.

이미 수락된 작업을 폴링하다 생긴 일시적 실패는 폴링만 다시 잡을 뿐 시도 횟수를 소모하지 않습니다.
Mock의 재시도 동작은 메모리 카운터가 아니라 영속화된 `attempt_count`가 결정합니다.

`GENERATION_ATTEMPT_TIMEOUT_SEC`의 기본값이 120에서 **300**으로 바뀐 이유는 실측 때문입니다. 이
프로젝트의 실제 Live 실행에서 kling-2.6 image-to-video는 3분 30초~4분이 걸렸습니다. 120초 마감은
정상적인 생성 도중에 매번 만료되어, 이미 수락되어 **이미 과금된** 프로바이더 작업을 취소하지 않은 채
버리고 재제출했습니다. 영상 6개를 만드는 데 생성 12번 값이 나갔습니다. 바뀐 것은 기본값 하나뿐이고,
메커니즘은 여전히 "버리고 재제출"입니다. 이 앱은 업스트림 작업을 취소하지 않고, 버려진 작업이 나중에
성공해도 그 결과를 회수하지 않습니다.

Scene 초안 작성에는 네 번째 분류가 하나 더 있습니다. 모델이 스키마가 거부하는 형태로 답하면 그것은
영구 실패가 아니라 **재시도 가능한** `SchemaProviderError`입니다. 업스트림에 만들어진 것이 없으므로
다시 물어봐도 중복이 생기지 않고, 형태를 한 번 틀린 모델은 대개 다음에 맞힙니다. 전송 오류만
재시도했다면 정작 더 드문 문제에 예산을 다 썼을 것입니다. 시도를 모두 소진하면 장애 코드가 아니라
`SCENE_SCHEMA_INVALID`로 보고해서 두 원인이 구분되게 남깁니다.

### 모드 간 산출물

산출물은 그것을 만든 프로바이더만 쓸 수 있습니다. Mock 이미지는 이 앱이 서빙하는 경로이고, Live
이미지는 프로바이더 CDN의 URL입니다. 그래서 Mock 이미지 위에 Live 영상을 요청하면 job 생성 시점에
`409 ARTIFACT_MODE_MISMATCH`로 거부합니다. 세 단계 뒤 프로바이더 요청 빌더 안에서 익명으로 터지게
두지 않습니다. Scene 중간에 모드를 바꾸는 것이 런타임 전환을 시연하는 가장 자연스러운 방법이라 이
가드가 필요합니다.

### 배치 생성

`POST /api/scenes/{id}/images`와 `.../videos`는 요청 한 번으로 Cut마다 job 하나를 등록하고 공통 배치
id를 찍습니다. 시작할 수 없는 Cut — 활성 job이 있거나, 선택된 이미지가 없는 영상 — 은 `skipped`에
담기고 나머지는 진행합니다. 쓸 수 없는 Cut 하나가 나머지 다섯을 취소시키면 안 됩니다.

UI도 이 목록을 그대로 보여 줍니다. 각 배치 버튼 옆의 `role="status"` 영역에 "N of 6 cuts were
skipped"와 함께 Cut 순서별 한 줄씩("Cut 1 · a generation is already running") 나옵니다. 다섯 개의
AppError 코드를 사람이 읽는 문장으로 매핑하고, 매핑에 없는 코드는 코드 그대로 노출합니다. 이름 붙일 수
없는 skip도 시작하지 못한 Cut인 것은 같기 때문입니다.

동시성은 배치가 아니라 워커가 소유합니다. `run_once()`는 먼저 이미 `SUBMITTING` 또는 `PROCESSING`인
job 수를 세고, **같은 트랜잭션 안에서** `GENERATION_CONCURRENCY - in_flight` 만큼만 due job을
claim합니다. 즉 이 설정은 틱당 claim 수가 아니라 **프로바이더에 동시에 물려 있는 작업 수**를
제한합니다. 예산이 꽉 찬 틱은 아무것도 claim하지 않고 만료 처리와 폴링으로 흘러가는데, 바로 그것이
진행 중인 job을 끝내서 예산을 되돌려 주는 경로입니다. claim이 끝난 뒤에는 프로바이더 호출만
`asyncio.gather`로 펼칩니다. claim을 직렬로 두면 이중 claim이 구조적으로 불가능해지고 SQLite 쓰기 락도
짧게 유지되며, 병렬성은 실제로 지연이 있는 곳에만 씁니다.

claim은 한 번의 제한된 조회 대신 keyset 페이지로 큐를 걷습니다. 앵커 대기 중인 job은 **아무 것도 쓰지
않고 건너뛰기** 때문에, 단순 LIMIT 조회는 대기 중인 job만으로 가득 차서 실행 가능한 job을 전부 뒤에
숨길 수 있습니다. 건너뛴 job은 예산도 소모하지 않습니다.

UI의 배치 진행률은 배치 행에 저장된 값이 아니라 job들에서 유도합니다. 그래서 표시되는 숫자가 상태
기계와 어긋날 수 없습니다.

### 캐릭터 일관성

프롬프트를 Cut마다 따로 쓰면 그림이 서로 어긋납니다. 그래서 최종 프롬프트는 모델이 쓰지 않습니다.
Scene 생성 때 모델이 만드는 것은 두 가지입니다.

1. **캐릭터 시트** — 반복 등장 인물 2~4명을 고정된 축(머리색, 헤어스타일, 복장, 체형, 인상, 시그니처
   소품)으로 서술하게 요청합니다. 스키마는 1~4명을 허용하는데, 주인공이 한 명인 프롬프트가 502 대신
   Scene을 만들어 내야 하기 때문입니다.
2. Cut별 **샷 설명** — 구도와 행동만 적고 외형을 다시 언급하는 것은 명시적으로 금지합니다.

그다음 `app/prompting.py`가 하나의 템플릿으로 모든 Cut 프롬프트를 합성합니다.

```text
<style guide>. Characters, keep identical in every shot: <character sheet>. Shot: <shot>. Avoid: <negative guide>
```

합성은 순수 함수라 여섯 Cut의 캐릭터 구절이 바이트 단위로 동일하고, 재생성은 이전 버전을 만든 프롬프트를
문자 그대로 재사용합니다. 합성된 프롬프트는 Cut에 저장되며, UI가 `Image prompt` / `Video prompt`로
보여 주는 것도 job 이력의 `Prompt`에 찍히는 것도 같은 값입니다.

여기에 더해 Cut 1의 **선택된 이미지가 Scene 앵커**가 됩니다. Cut 2~6은 그 이미지를 이미지 모델에
`image_urls`로 넘겨 같은 얼굴을 다시 그리게 합니다. 그래서 이미지 배치는 자연히 두 단계로 흐릅니다.
Cut 1 먼저, 그다음 나머지가 병렬로.

게이트는 앵커가 아직 올 가능성이 있는 동안 Cut 2~6을 붙잡습니다. Cut 1을 아예 요청하지 않은 경우도
포함합니다. 배치 밖에서 단일 Cut 버튼을 누른다고 앵커 없는 이미지가 조용히 나오면 안 되기 때문입니다.
붙잡힌 job은 `waitingForAnchor`로 보고되고 UI가 어느 Cut을 기다리는지 문장으로 말해 줍니다. 이유 없이
멈춰 있는 상태는 없습니다. 유일한 해제 조건은 Cut 1이 재시도를 모두 소진한 경우입니다. 영구 실패한
앵커는 Scene을 영원히 멈추게 두는 대신 게이트를 엽니다.

### 비주얼 스타일

스타일 가이드 하나와 네거티브 가이드 하나가 모든 이미지·영상 프롬프트에 주입되고, 모델에게는 매체나
사실성 자체를 언급하지 말라고 지시합니다. 목표는 스타일라이즈된, 부드럽게 셰이딩된 귀여운 애니메이션
룩입니다. `photorealistic`, `live action`, `3D render`는 오직 네거티브 가이드 안에만 등장하며 이는
테스트로 단언되어 있습니다.

### 런타임 Mock/Live 전환

`PUT /api/config {"generationMode":"LIVE"}`는 재시작 없이 모드를 바꿉니다. 두 프로바이더 쌍은 시작할 때
모두 만들어 두고 레지스트리가 해석하므로, 레지스트리 위의 코드는 어디서도 모드로 분기하지 않습니다.
`GET /api/config`는 `liveAvailable`을 함께 보고하고, 키 없이 Live로 바꾸려 하면
`409 LIVE_MODE_UNAVAILABLE`이 돌아옵니다.

모든 job은 생성 시점의 모드를 저장하고 워커는 **그 스냅숏**으로 프로바이더를 고릅니다. 그래서 스위치를
눌러도 이미 진행 중인 Live 작업이 Mock 프로바이더로 넘어가는 일은 없습니다. 모드는 프로세스 메모리에
있으므로 재시작하면 `GENERATION_MODE`로 돌아갑니다.

### 웹훅

`POST /api/webhooks/kie`는 `WEBHOOK_SECRET`이 설정된 경우에만 열립니다. payload는 폴링이 쓰는 것과
같은 함수로 정규화되고 같은 워커 전이로 적용되므로, 같은 작업에 대한 콜백과 폴링은 동일한 결과를
만듭니다.

비밀값은 `X-Webhook-Secret` 헤더로도, `?token=` 쿼리 파라미터로도 올 수 있고 둘 다 상수 시간으로
비교합니다. 채널이 둘인 이유는 **프로바이더에게 커스텀 헤더를 보내라고 시킬 수 없기** 때문입니다. Kie는
콜백 URL 하나만 받습니다. 그래서 Live 콜백은 URL에 비밀값을 싣습니다.

```dotenv
WEBHOOK_PUBLIC_URL=https://<your-tunnel>/api/webhooks/kie?token=<WEBHOOK_SECRET>
```

그 토큰은 액세스 로그에 남습니다. `WEBHOOK_SECRET`을 두 API 키와 함께 로그 마스킹 대상으로 등록한
이유입니다.

두 채널 모두 **UTF-8로 인코딩한 바이트**를 비교합니다. `compare_digest`는 비ASCII `str`을 거부하는데,
이 바이트를 고르는 쪽은 인증되지 않은 호출자입니다. 쿼리 토큰이 그렇고, 헤더도 마찬가지입니다. Starlette은
원시 헤더 바이트를 latin-1로 디코딩할 뿐 거부하지 않으므로 헤더 채널도 똑같이 공격 가능했습니다.
`str`로 비교하면 그 `TypeError`가 그대로 새어 나가 500이 되지만, 바이트로 비교하면 깔끔한 401이 됩니다.

멱등성은 전이 자체에서 나옵니다. 결과를 적용하는 모든 분기는 트랜잭션 안에서 job을 다시 읽고, **그
전이가 기대하는 상태가 아니면 아무 것도 하지 않습니다.** 결과 적용과 폴링 실패는 `PROCESSING`을,
제출 실패는 `SUBMITTING`을 기대합니다(제출 실패는 job이 아직 `SUBMITTING`일 때 발생하므로 가드가 기대
상태를 인자로 받아야 하고, `PROCESSING`으로 못박으면 제출 실패 경로가 조용히 죽습니다).
`apply_external_result`의 다섯 분기 — 대기 중, 재시도 가능한 실패, 재시도 불가한 실패, 결과 URL 없음,
완료 — 가 **예외 없이 전부** 이 가드를 지납니다. 한때 두 실패 분기에는 가드가 없었고, 그것이 곧
"중복 전달이 두 번째 산출물을 만드는" 경로였습니다. 지금은 중복 전달도, 폴링과 콜백의 경합도 두 번째
산출물을 만들 수 없습니다. 알 수 없거나 이미 끝난 작업에는 오류 대신 `200 {"status":"ignored"}`를
돌려줍니다. 4xx를 주면 프로바이더가 절대 결과를 바꿀 수 없는 전달을 계속 재시도하기 때문입니다.

Mock 모드의 `SUCCEED_VIA_WEBHOOK` 시나리오는 프로바이더가 `SELF_BASE_URL`로 실제 콜백을 보내게 하고,
그 작업의 폴링은 일부러 영원히 성공하지 않습니다. 그래서 이 job은 웹훅 라우트가 동작할 때만 끝납니다.

결과를 스스로 밀어 주는 프로바이더는 그 전달을 `submit()` 중이 아니라 `Submission.on_processing`으로
돌려주고, 워커는 job이 `PROCESSING`으로 커밋된 다음에야 그것을 발화합니다. 그러지 않으면 빠른 콜백이
바로 그 커밋을 앞질러 도착해 적용할 job을 찾지 못하고, job은 시도 마감이 만료될 때까지 놀게 됩니다.

### 재생성과 활성 job 충돌

한 Cut에는 종류별로 활성 job이 최대 하나입니다. 이 규칙은 부분 유니크 인덱스로 강제되므로, 동시에 두
요청이 오면 정확히 하나가 `202`, 하나가 `409 GENERATION_ALREADY_ACTIVE`를 받습니다.

```sql
CREATE UNIQUE INDEX uq_active_generation_per_cut_kind
ON generation_jobs (cut_id, kind)
WHERE status IN ('QUEUED', 'SUBMITTING', 'PROCESSING', 'RETRY_WAIT');
```

재생성은 이전 job이 종결 상태에 도달한 뒤 같은 엔드포인트를 다시 호출하는 것입니다. 다음 버전을 만들 뿐
이전 job이나 산출물을 지우거나 덮어쓰지 않습니다. 영상 job은 요청 시점에 선택되어 있던 이미지를
`source_image_id`로 고정합니다. **이미지를 (같은 것이든 다른 것이든) 다시 선택하면
`selected_video_id`가 해제되고**, 현재 선택된 이미지에서 나온 영상만 선택할 수 있습니다. 첫 성공
산출물의 자동 선택은 해당 선택이 아직 비어 있을 때만 일어납니다(영상은 여기에 더해 그 영상의 소스가
지금 선택된 이미지와 같아야 합니다). 사용자가 명시적으로 고른 선택은 절대 덮어쓰지 않습니다.

HTTP 재전송용 idempotency key는 없습니다. 중복 작업은 위의 DB 제약만으로 막습니다.

### CORS와 Mock 미디어

CORS는 `FRONTEND_ORIGIN` 하나만 허용하고, credentials는 끄고, 메서드는 `GET`, `POST`, `PUT`,
`OPTIONS`만 엽니다. 다른 origin에는 `access-control-allow-origin` 헤더 자체가 나가지 않습니다. Mock
픽스처는 FastAPI `StaticFiles`로 `/media/mock`에 마운트되어 실제 `image/png`, `video/mp4` 응답으로
서빙됩니다.

## 감수한 트레이드오프

의도적인 MVP 한계이며 운영 수준 보장이 아닙니다.

- **인증 없음.** 루프백 바인딩과 "신뢰된 로컬" 범위만이 완화책입니다.
- **명목상 30초.** 각 Cut은 5초를 요청하지만 생성된 미디어를 검사하거나 잘라내지 않습니다. 결과물은
  "5초를 요청한 Cut 6개"이지 30초 재생 시간을 보장하는 시퀀스가 아닙니다.
- **합쳐진 MP4 없음.** 브라우저에서 Cut을 차례로 재생할 뿐 이어붙이기도, 다운로드도, FFmpeg도
  없습니다.
- **프로바이더 URL은 만료될 수 있음.** Live 결과는 프로바이더 URL로 저장하며 자체 오브젝트 스토리지가
  없습니다.
- **단일 프로세스 전용.** 백엔드 프로세스 1개, Uvicorn 워커 1개, 워커 코루틴 1개. 수평 확장과 분산 락은
  범위 밖입니다.
- **불확실한 제출은 닫는 쪽으로 실패.** 위 `SUBMISSION_UNCERTAIN` 참조. 이중 청구 가능성보다 명시적
  실패를 택했습니다.
- **버려진 시도는 취소되지 않음.** 시도 마감이 지나면 앞선 프로바이더 작업을 취소하지 않고 새로
  제출합니다. 기본값을 300초로 올려 정상 지연에서는 발생하지 않게 만들었을 뿐, 메커니즘은 그대로입니다.
- **Scene/Cut 편집 없음**, 여러 프롬프트를 한 번에 오케스트레이션하는 기능도 없습니다.
- **앵커 참조는 이미지 모델이 `image_urls`를 존중한다는 가정에 의존합니다.** 요청 형태는 HTTP 계약
  테스트로 고정되어 있지만, 그것이 실제 출력에 미치는 효과는 실제 모델로 검증하지 못했습니다. 모델이
  참조를 무시한다면 일관성은 캐릭터 시트만으로 얻는 수준까지 떨어집니다.
- **런타임 모드는 인메모리.** 프로세스 하나에서는 옳지만, 두 번째 프로세스가 생기는 순간 공유 상태가
  필요해집니다.

## 데모 시나리오

Mock 모드에서 비용 없이 모든 요구사항을 훑는 여섯 단계입니다. 스택을 띄우고 http://localhost:5173 을
연 뒤 순서대로 진행하십시오.

**1 — 프롬프트에서 여섯 Cut으로.** 아무 프롬프트나 입력하고 **Create scene**을 누릅니다. 각각 `5 sec`인
Cut 카드 6개와, 모델이 만들어 낸 반복 캐릭터를 보여 주는 **Characters in every cut** 패널이 나타납니다.
Scene id가 URL에 들어가므로 새로고침해도 같은 작업 공간이 복원됩니다.

**2 — 이미지 배치 생성.** **Generate all images**를 누릅니다. 요청 한 번이 job 6개를 등록하고,
`Images 0/6 done`이 올라갑니다. 워커는 프로바이더에 동시에 최대 `GENERATION_CONCURRENCY`개까지만
물립니다.

**3 — 캐릭터 일관성.** 아무 Cut의 이미지 이력을 엽니다. 모든 Cut의 프롬프트가 같은 캐릭터 구절을
담고 있고, Cut 2~6에는 `Reference  Cut 1 · Image v1`이 추가로 표시됩니다. Cut 1의 선택 이미지가 Scene
앵커로 모델에 전달되었다는 뜻입니다. 라벨이 소유 Cut을 함께 부르는 이유는 그 이미지가 다른 Cut에 속해
있기 때문이고, 같은 Cut의 이미지라면 `Image v1`처럼 짧게 나옵니다. Cut 1에는 Reference가 없습니다.
자기 자신이 앵커이기 때문입니다.

게이트가 추측을 거부하는 것을 보려면, **새** Scene을 만들고 Cut 1은 그대로 둔 채 Cut 3에서만
**Generate image**를 누르십시오. job은 `Queued`에 머무르며 *"Waiting for the Cut 1 image so this cut
keeps the same characters."*라고 말합니다. Cut 1을 생성하면 스스로 풀립니다.

**4 — 재시도, 최종 실패, 재생성 이력.** 어떤 Cut의 **Mock scenario** 드롭다운을 `Always fail`로 두고
생성하면 job이 백오프와 함께 재시도하다 `Failed after 3/3 attempts`로 정착합니다. 드롭다운을
`Success`로 바꾸고 **Regenerate image**를 누르면 v2가 성공하고 **v1은 실패 사유와 함께 이력에 그대로
남습니다**. `Fail twice, then succeed`가 그 중간 사례를 보여 줍니다.

**5 — 폴링 대신 웹훅.** 드롭다운을 `Succeed via webhook`으로 두고 생성합니다. 이 시나리오의 Mock
폴링은 일부러 성공을 반환하지 않으므로, 콜백 라우트가 동작할 때만 job이 끝납니다. 백엔드 로그에
`POST /api/webhooks/kie`가 정확히 한 줄 찍힙니다.

**6 — 런타임 모드 전환과 재생.** 헤더의 스위치가 재시작 없이 Mock/Live를 뒤집습니다. Live는 두 키가
모두 설정된 경우에만 선택할 수 있습니다. Mock에서 만든 이미지 위에 Live 영상을 요청하면 프로바이더
내부에서 터지는 대신 `409 ARTIFACT_MODE_MISMATCH`가 돌아옵니다. 마지막으로 **Generate all videos**를
누르고 `6 of 6 videos ready`를 기다린 뒤 **Play sequence**를 누르면 선택된 영상 6개가 Cut 순서대로
재생됩니다.

모든 job 카드는 무엇이 그것을 만들었는지 보여 줍니다. 합성된 프롬프트, `MOCK`/`LIVE`, 시도 횟수, 영상의
소스 이미지, 이미지의 참조 이미지, 그리고 실패했다면 그 사유까지.

## 검증

저장소 루트에서 시작하는 한 벌의 셸 명령입니다. 백엔드 블록은 [Docker 없이
실행](#docker-없이-실행)에서 만든 가상환경이 활성화된 상태를 가정합니다.

```bash
# backend
cd backend
python -m ruff check app tests
python -m mypy app
python -m pytest tests -q --cov=app

# frontend
cd ../frontend
npm run lint
npm run test:run
npm run build

# Compose
cd ..
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose logs backend --no-color
```

E2E는 Mock 스택이 이미 떠 있어야 합니다.

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
# 5173이 이미 쓰이고 있다면 다른 주소를 가리킵니다:
#   E2E_BASE_URL=http://localhost:5174 npm run test:e2e
```

커밋 전 스캔 두 개입니다. **둘 다 아무 것도 출력하지 않아야 합니다.** 패턴 문자열 자체를 인용하는
README와 계획 문서는 제외합니다.

```bash
cd ..
git grep -n -E "(sk-[A-Za-z0-9_-]{16,}|Bearer [A-Za-z0-9_-]{16,}|OPENAI_API_KEY=.+|KIE_API_KEY=.+)" \
  -- . ":(exclude).env.example" ":(exclude)README.md" \
  ":(exclude)docs/superpowers/plans/2026-08-14-prompt-to-animation.md"
git grep -n -E "runtime-mode|RuntimeSetting|completion_method|WEBHOOK_SIGNING_SECRET|generations/batch|BatchService" \
  -- backend frontend
```

두 번째 스캔은 설계에서 빠진 이름들이 코드에 남아 있지 않은지 확인합니다. 한때 `/api/webhooks`도 이
목록에 있었지만 지금은 살아 있는 라우트라서 제거했습니다. 그대로 두면 스캔이 항상 매치를 뱉어, 아무도
읽지 않는 검증이 됩니다.

`git grep`은 **추적되는 파일만** 훑습니다. 따라서 이 스캔은 "비밀값이 커밋되지 않았다"는 보장이지
"작업 트리에 비밀값이 없다"는 보장이 아닙니다. `.env`와 `backend/.env`는 `.gitignore`에 걸려 추적되지
않으므로 스캔 대상에서 빠집니다. 실제 키를 로컬 파일에 두었다면 그 파일은 이 스캔이 보호해 주지
않습니다.

자동화된 테스트는 실제 OpenAI나 Kie API에 도달하지 않습니다. `pytest-socket`이 루프백을 제외한 모든
호스트를 차단하고, 프로바이더 계약은 `respx` HTTP 스텁으로 고정합니다.

## 요구사항 추적

| ID | 요구사항 | 근거 |
|---|---|---|
| `REQ-01` | 프롬프트 하나로 Scene 생성 | `cd backend && pytest tests/test_scenes.py`; `cd frontend && npm run test:run src/app/App.test.tsx`; E2E |
| `REQ-02` | 정확히 6개 Cut, 각 5초 | `cd backend && pytest tests/test_scenes.py tests/test_core.py`; E2E |
| `REQ-03` | Cut별 이미지 생성과 선택 | `cd backend && pytest tests/test_generations.py`; `cd frontend && npm run test:run src/features/generations/CutCard.test.tsx`; E2E |
| `REQ-04` | 선택된 이미지로 영상 생성 | `cd backend && pytest tests/test_generations.py tests/test_worker.py`; E2E |
| `REQ-05` | 재시도, 진행 표시, 최종 실패 | `cd backend && pytest tests/test_worker.py`; `cd frontend && npm run test:run src/features/generations/CutCard.test.tsx`; E2E |
| `REQ-06` | 재생성이 이전 결과를 보존 | `cd backend && pytest tests/test_generations.py`; E2E |
| `REQ-07` | 선택된 영상 6개가 순서대로 재생 | `cd frontend && npm run test:run src/features/player/SequencePlayer.test.tsx`; E2E |
| `REQ-08` | 외부 API 없이 데모 가능 | `cd backend && pytest tests`; `docker compose up --build -d` 후 E2E |
| `REQ-09` | 실제 OpenAI·Kie 연동 | `cd backend && pytest tests/providers` |
| `REQ-10` | API 키가 노출되지 않음 | `cd backend && pytest tests/test_core.py -k secret`; 위 비밀값 스캔 |
| `REQ-11` | 재현 가능한 로컬 실행 | `docker compose config`; `cd backend && pytest tests/test_core.py -k "cors or media"` |
| `REQ-12` | 동시성이 제한된 배치 생성 | `cd backend && pytest tests/test_batches.py tests/test_worker.py`; `cd frontend && npm run test:run src/features/scene/BatchControls.test.tsx`; E2E |
| `REQ-13` | 여섯 Cut에 같은 캐릭터 | `cd backend && pytest tests/test_prompting.py tests/test_scenes.py`; `pytest tests/test_worker.py -k anchor`; E2E |
| `REQ-14` | 애니메이션 톤, 비사실적 스타일 | `cd backend && pytest tests/test_prompting.py`; E2E |
| `REQ-15` | 런타임 Mock/Live 전환 | `cd backend && pytest tests/test_core.py -k "config or mode or live"`; `cd frontend && npm run test:run src/app/App.test.tsx` |
| `REQ-16` | 폴링과 웹훅이 한 상태 기계 위에 | `cd backend && pytest tests/test_webhooks.py`; E2E |
| `REQ-17` | 입력·출력 전체 추적성 | `cd backend && pytest tests/test_generations.py`; `cd frontend && npm run test:run src/features/generations/CutCard.test.tsx` |
