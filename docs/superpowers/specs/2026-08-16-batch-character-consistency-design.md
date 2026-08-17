# Batch 생성 · 캐릭터 일관성 · 런타임 모드 전환 · Webhook 설계

> 이 문서는 `2026-08-14-prompt-to-animation-design.md`의 **범위 결정 일부를 대체(supersede)** 한다.
> 기존 문서 §3.2는 Webhook, Batch 생성, 런타임 Mock/Live 전환을 제외하면서 그 근거를
> "원 과제 원문이 저장소에 없음"이라고 기록했고, "원문이 나중에 제공되어 필수임이 확인되면
> 별도 scope 변경으로만 다시 추가한다"고 명시했다. 2026-08-16 과제 원문이 제공되어 세 항목이
> 모두 필수로 확인되었으므로, 예고된 scope 변경 조건이 발동한다.
> 대체되지 않는 나머지 결정(단일 프로세스, 인증 없음, 로컬 전용, nominal 30초, 자체 스토리지 없음)은 그대로 유효하다.

## 1. 이번 변경의 목적

1. `scene → 6 cut → cut image → cut video` 흐름을 **batch 단위**로 묶어 동시성 있게 처리한다.
2. 한 scene의 6개 cut에 **동일 인물**이 이어지게 만든다.
3. 결과물을 실사풍이 아닌 **애니메틱하고 귀여운** 톤으로 고정한다.
4. Mock/Live를 **런타임에 전환**할 수 있게 한다.
5. Polling과 **Webhook을 동일 상태 머신 위에서** 함께 지원한다.
6. 결과물마다 **어떤 입력으로 생성되었는지** 전부 추적 가능하게 한다.

이미 요구 수준을 넘게 구현된 retry/실패 처리와 regenerate 버저닝은 건드리지 않는다.

## 2. 요구사항 추적

| ID | 요구 | 설계 반영 | 검증 |
|---|---|---|---|
| `REQ-12` | 2개 이상 batch 생성 | scene 단위 batch endpoint 2종 + bounded concurrency worker | batch API/worker test, E2E |
| `REQ-13` | scene 내 캐릭터 일관성 | `Scene.character_profiles` + 결정론적 프롬프트 합성 + anchor 이미지 참조 | prompting 단위 test, 합성 스냅샷 test |
| `REQ-14` | 애니메틱/귀여운 스타일 | 공통 style guide를 image/video 프롬프트에 주입 | prompting 단위 test |
| `REQ-15` | 런타임 Mock/Live 전환 | `PUT /api/config` + job별 모드 스냅샷 | config API test |
| `REQ-16` | Polling + Webhook 동시 지원 | `POST /api/webhooks/kie`가 polling과 같은 전이 함수 사용 | webhook test, 경합 test |
| `REQ-17` | 입출력 추적 | job에 `generation_mode`·`reference_image_id` 저장, UI 노출 | scene detail test, UI test |

## 3. 데이터 모델 변경

### 3.1 Scene
- `+ character_profiles: JSON` — LLM이 생성한 등장인물 시트 목록. cut 프롬프트 합성의 anchor.

### 3.2 Cut
- `+ shot_description: str` — cut별 장면 설명. **공통 캐릭터 설정과 분리**해서 보관한다.
- `image_prompt` / `video_prompt`의 의미가 바뀐다. 더 이상 LLM 원문이 아니라
  `style guide + character sheet + shot description`을 합성한 **최종 프롬프트**다.

합성을 scene 생성 시점에 1회 수행하고 결과를 저장한다. 이유는 두 가지다.
regenerate가 v1과 **글자 단위로 동일한** 프롬프트를 재사용하므로 캐릭터 일관성이 버전 간에도 깨지지 않고,
사용자에게 보여줄 "생성 입력"이 곧 저장된 값이라 추적이 자명해진다.

### 3.3 GenerationJob
- `+ generation_mode: str` — 요청 시점의 `MOCK | LIVE` 스냅샷.
- `+ reference_image_id: UUID | None` — IMAGE job이 참조한 anchor 이미지.
- `+ batch_id: UUID | None` — 소속 batch.

`source_image_id`(VIDEO의 원본 이미지)와 `reference_image_id`(IMAGE의 참조 이미지)는
의미가 다르므로 컬럼을 분리한다. 하나를 겸용하면 조회 시점에 kind를 봐야 뜻이 정해져 버린다.

### 3.4 GenerationBatch (신규)
- `id`, `scene_id`, `kind`, `requested_count`, `created_at`

배치 상태 컬럼은 **두지 않는다.** 상태는 소속 job들에서 파생한다.
denormalized 상태 컬럼은 worker 전이마다 동기화 대상이 하나 더 늘고 불일치 버그의 근원이 된다.

## 4. 캐릭터 일관성

### 4.1 1단계 — 캐릭터 시트
Scene 생성 시 LLM이 등장인물에 대해 다음을 산출한다. 시스템 지시는 2~4명을 요구하지만,
**스키마 하한은 1명이다**(2026-08-17 개정). 요구사항은 "cut 간 동일 인물 유지"이지 인원수가
아니며, 1인 주인공 프롬프트가 scene 생성 자체를 502로 실패시키면 안 되기 때문이다.

`name, role, ageRange, hairColor, hairStyle, outfit, build, faceImpression, signatureProp`

필드를 고정된 스키마로 강제하는 이유는, 자유 서술이면 cut마다 LLM이 다른 축을 강조해
프롬프트가 흔들리기 때문이다. 고정 필드는 문자열 합성을 결정론적으로 만든다.

### 4.2 2단계 — 결정론적 프롬프트 합성
`app/prompting.py`의 순수 함수가 담당한다.

```text
<style guide> | Characters: <character sheet> | Shot: <shot description> | <negative guide>
```

같은 입력이면 항상 같은 문자열이 나온다. 단위 테스트의 주 대상이다.

### 4.3 3단계 — anchor 이미지 참조
Cut 1의 **선택된** 이미지를 scene의 anchor로 삼고, cut 2~6의 이미지 생성 시
Kie 이미지 모델에 `image_urls: [anchorUrl]`로 함께 전달한다.

anchor는 별도 컬럼 없이 `order=1 인 Cut의 selected_image`에서 **파생**한다.
파생으로 두면 사용자가 Cut 1의 이미지를 다른 버전으로 바꿨을 때 이후 생성이 자동으로 따라간다.

**게이트 규칙 (2026-08-17 개정, 커밋 `f49276c`):** anchor가 없으면 cut 2~6 이미지 job은 **대기한다.**
Cut 1을 아직 요청하지 않은 경우도 포함한다. 최초 설계는 anchor job이 *활성일 때만* 대기시켰는데,
그러면 배치를 거치지 않고 Cut 3 버튼을 누른 사용자가 참조 없는 이미지를 받고 캐릭터 일관성이
조용히 깨진다. 게이트 판단은 `app/anchor.py`의 순수 함수 하나가 내리고, worker(집행)와
scene 응답(설명)이 같은 함수를 부른다. 대기 중인 job은 `waitingForAnchor`로 응답에 드러나
UI가 어느 컷을 기다리는지 말해 준다.

**교착 방지:** 유일한 해제 조건은 Cut 1 이미지 job의 **재시도 소진(FAILED)** 이다.
스스로 끝날 수 없는 대기는 그 경우뿐이므로, 그때만 참조 없이 진행한다.

## 5. 배치 처리

### 5.1 엔드포인트
| Method | Endpoint | 목적 |
|---|---|---|
| `POST` | `/api/scenes/{id}/images` | scene의 모든 cut 이미지 batch 생성 |
| `POST` | `/api/scenes/{id}/videos` | 선택 이미지가 있는 모든 cut 비디오 batch 생성 |

이미 활성 job이 있는 cut은 **건너뛴다**(전체 실패시키지 않는다). 응답에 생성/건너뜀 내역을 담는다.
6개 중 1개가 이미 돌고 있다고 나머지 5개를 못 만들 이유가 없다.

### 5.2 worker 동시성
`run_once()`는 최대 `GENERATION_CONCURRENCY`(기본 3)개의 due job을 처리한다.

- **claim은 직렬**: 한 트랜잭션에서 due job을 N개까지 골라 `SUBMITTING`으로 바꾸고 attempt를 올린다.
- **provider 호출은 동시**: `asyncio.gather`로 claim된 요청들을 병렬 실행하고, 각자 짧은 트랜잭션으로 결과를 기록한다.

claim을 직렬화하면 double-claim이 구조적으로 불가능하다. SQLite 쓰기 락 경합도 짧게 유지된다.
폴링도 같은 방식(직렬 조회 → 동시 poll)을 쓴다.

## 6. 런타임 Mock/Live 전환

- 현재 모드는 프로세스 메모리에 둔다. 단일 프로세스 전제이므로 DB 설정 테이블은 불필요하다.
- `GET /api/config` → `{generationMode, liveAvailable}`. 키 값이나 존재 여부의 상세는 노출하지 않는다.
- `PUT /api/config {"generationMode": "LIVE"}` → 키가 없으면 `409 LIVE_MODE_UNAVAILABLE`.
- provider는 startup에 Mock/Live 둘 다 구성하고(Live는 키가 있을 때만) 레지스트리가 골라준다.
- **진행 중인 job은 자기 `generation_mode` 스냅샷을 따른다.** 전역 모드가 바뀌어도 갈아타지 않는다.
  Live로 제출한 task를 Mock으로 폴링하는 사고를 원천 차단한다.

## 7. Webhook

- `POST /api/webhooks/kie`. `WEBHOOK_SECRET`이 설정된 경우에만 활성화되고, `X-Webhook-Secret`을 상수 시간 비교한다.
- 페이로드는 polling과 **같은 정규화 함수**를 거쳐 같은 전이 함수(`_apply_task_result`)로 들어간다.
- `external_task_id`로 job을 찾는다. job이 없거나 이미 terminal이면 `200 {"status":"ignored"}`를 준다.
  재전송은 흔하고, 4xx를 주면 provider가 무한 재시도한다.
- 모든 전이는 트랜잭션 안에서 `job.status is PROCESSING`을 다시 확인한다.
  polling과 webhook이 동시에 도착해도 뒤에 온 쪽은 조건이 깨져 아무 일도 하지 않는다. 이것이 idempotency의 근거다.
- Live: `WEBHOOK_PUBLIC_URL`이 설정되면 createTask에 `callBackUrl`을 실어 보낸다.
- Mock: 주입된 sender로 자기 자신의 webhook 엔드포인트를 실제 호출한다.
  `MockScenario.SUCCEED_VIA_WEBHOOK`에서만 동작하며, 나머지 시나리오는 기존대로 polling으로 끝난다.

## 8. 상태 모델

기존 6상태를 유지한다.
`QUEUED → SUBMITTING → PROCESSING → SUCCEEDED | FAILED`, 재시도는 `RETRY_WAIT`.

과제 예시의 `PENDING`은 `QUEUED`, `RETRYING`은 `RETRY_WAIT`에 대응한다.
이미 26개 worker 테스트가 이 이름에 묶여 있어 개명 이득이 없다.

## 9. 프론트엔드

- Scene 요약에 **캐릭터 카드**를 표시한다. 어떤 설정으로 생성됐는지 눈으로 확인 가능해야 한다.
- **Generate all images / Generate all videos** 버튼과 batch 진행률(성공·실패·진행 수)을 표시한다.
- Cut 카드에 shot description, 합성된 최종 프롬프트, `MOCK|LIVE` 배지, 참조 이미지 계보를 표시한다.
- 헤더에서 모드를 전환한다. Live 키가 없으면 Live 선택지를 비활성화한다.

## 10. 명시적 trade-off

- anchor 참조는 Kie 이미지 모델의 `image_urls` 문서 계약에 의존한다. 실제 응답으로 검증하지 못했고
  HTTP contract test(고정 stub)로만 고정한다. 모델이 참조를 무시하면 캐릭터 시트 주입 수준으로 품질이 내려간다.
- anchor 때문에 이미지 batch는 `Cut 1 → 나머지 5개` 2페이즈가 된다. 완전 병렬보다 느리지만
  일관성을 얻는 대가로 받아들인다. 비디오 batch는 2페이즈가 필요 없다.
- 런타임 모드는 메모리에 있으므로 재시작하면 `GENERATION_MODE` 기본값으로 돌아간다.
- Webhook 시크릿은 공유 비밀 헤더다. provider가 HMAC 서명을 제공하면 그쪽이 낫지만 계약을 확인할 수 없다.

## 11. 완료 조건

- Mock 모드에서 batch 2종이 6개 job을 만들고 동시성 한도 안에서 처리된다.
- 6개 cut 프롬프트가 동일한 캐릭터 시트와 style guide를 포함한다(테스트로 고정).
- cut 2~6 이미지 job이 anchor 이미지를 참조한다.
- `PUT /api/config`로 모드가 바뀌고, 진행 중 job은 자기 스냅샷 모드를 유지한다.
- webhook 수신이 polling과 동일한 전이를 만들고, 중복 수신은 무시된다.
- scene 상세에서 프롬프트·모드·참조 이미지·에러를 모두 확인할 수 있다.
- 기존 131개 백엔드 테스트와 41개 프론트 테스트가 계속 통과한다.
