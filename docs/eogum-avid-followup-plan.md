# eogum ↔ avid Follow-up Plan

> 최종 갱신: 2026-03-15
> 범위: `apps/api`, `apps/web`
> 목적: 현재 `avid-cli` 표면과 `eogum` 소비 코드를 다시 맞추고, human review 재연결 전에 위험 구간을 정리한다.

## 1. 현재 상태 요약

이 문서를 보기 전에 아래 문서를 먼저 본다.

- [eogum-api-runtime.md](/home/jonhpark/workspace/eogum/docs/eogum-api-runtime.md)
- [eogum-api-reference.md](/home/jonhpark/workspace/eogum/docs/eogum-api-reference.md)

### 1.1 지금 `avid` 는 어떻게 서빙되고 있는가

현재 `avid` 는 별도 HTTP 서버로 서빙되지 않는다.

- `eogum` API 서버가 [avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py) 에서 로컬 `avid-cli` subprocess 를 호출한다.
- 실행 대상은 submodule 안의 `avid-cli` 다.
  - `third_party/auto-video-edit/apps/backend/.venv/bin/avid-cli`
- `eogum` 이 직접 HTTP로 붙는 외부 서비스는 `Chalna` 뿐이다.
- 결과물은 `avid-cli` 가 로컬 temp dir에 쓴 뒤, `eogum` 이 R2로 업로드하고 presigned URL로 다시 사용자에게 제공한다.

즉 런타임 구조는 아래와 같다.

```text
browser
  -> eogum web
  -> eogum api (FastAPI)
      -> avid-cli (subprocess, local)
      -> Chalna (HTTP)
      -> R2 / Supabase
```

중요한 의미:

- `avid` 는 엔진이지 서비스가 아니다.
- `eogum` 은 `avid-cli` 의 machine-readable JSON artifact 를 읽는 오케스트레이터다.
- 따라서 `eogum` 이 엔진 내부 구조를 재구현하지 말고, `avid-cli` 표면을 그대로 소비해야 한다.

### 1.2 지금 잘 맞는 부분

- `review-segments -> save evaluation -> apply-evaluation` 의 큰 방향은 맞다.
- `avid` Python import 는 제거돼 있고 CLI-only 경계가 유지된다.
- 후처리 split command (`apply-evaluation`, `rebuild-multicam`, `clear-extra-sources`, `export-project`) 도 이미 사용 중이다.

### 1.3 지금 안 맞는 부분

- `avid.py` 가 provider / model / effort 를 여전히 하드코딩한다.
- `sync_diagnostics.json` artifact 를 `eogum` 이 아직 수집하지 않는다.
- `/projects/{id}/multicam` 재처리 경로가 bare thread 라서 실패/충돌/재시작 복구가 약하다.
- review payload 는 engine-native 로 바뀌었지만, `eogum` 스키마는 여전히 너무 고정적이다.

## 2. 핵심 원칙

1. `avid-cli` 가 만든 review payload 를 `eogum` 이 가공하지 않고 저장/반환/재적용한다.
2. `eogum` 이 엔진 payload 의 필드를 미리 다 안다고 가정하지 않는다.
3. 새 artifact 가 `avid-cli` 에 추가되면 `eogum` 은 가능한 한 그대로 업로드/노출한다.
4. human review UI 작업보다 먼저 backend 경로를 정합하게 만든다.
5. 재처리는 기존 queue / job 모델 쪽으로 수렴시키고, thread ad-hoc 실행은 줄인다.

## 3. human review / evaluation 스키마를 어떻게 맞출 것인가

### 3.1 source of truth

review payload 의 source of truth 는 `eogum` 이 아니라 `avid-cli review-segments` 다.

즉 `eogum` 은:

- `GET /projects/{id}/segments` 에서 `avid-cli review-segments` 결과를 그대로 돌려준다.
- `POST /projects/{id}/evaluation` 에서 그 shape 를 거의 그대로 저장한다.
- 후처리 시 저장된 evaluation payload 를 그대로 `avid-cli apply-evaluation` 에 넘긴다.

### 3.2 저장 전략

지금처럼 `evaluations.segments` 에 JSON blob 으로 저장하는 방향은 유지해도 된다.
다만 해석 규칙을 더 엔진 친화적으로 바꿔야 한다.

저장 단위:

```json
{
  "schema_version": "review-segments/v1",
  "review_scope": "content_segments",
  "join_strategy": "source_segment_index",
  "segments": [...]
}
```

이 구조는 유지한다.

### 3.3 스키마를 왜 느슨하게 해야 하는가

현재 `eogum` 의 `AiDecision` / `EvalSegment` 는 필드를 고정해 놓았다.
문제는 `avid-cli` 가 나중에 예를 들어 아래를 추가할 수 있다는 점이다.

- `score_breakdown`
- `diagnostics`
- `model`
- `provider`
- `segment_labels`

이때 `eogum` 이 필드를 다 모르면 두 가지 위험이 있다.

1. validation 단계에서 에러가 나서 저장 자체가 실패한다
2. 더 나쁜 경우, 에러 없이 **필드를 조용히 버린다**

지금 우리가 피하고 싶은 건 2번이다.

### 3.4 `extra="allow"` 가 무슨 뜻인가

`Pydantic` 모델은 기본적으로 정의되지 않은 필드를 엄격히 다루지 않는다.
기본 동작은 사실상 `ignore` 에 가깝고, 모르는 필드는 버릴 수 있다.

예를 들어:

```python
class AiDecision(BaseModel):
    action: str
    reason: str
```

여기에 아래 payload 가 들어오면:

```json
{
  "action": "cut",
  "reason": "dragging",
  "confidence": 0.9,
  "diagnostics": {"foo": "bar"}
}
```

`confidence`, `diagnostics` 를 모델이 모르면 저장/응답 과정에서 잃어버릴 수 있다.

`extra="allow"` 를 주면 의미는 이렇다.

- 모델이 모르는 필드도 허용한다
- 허용한 필드를 버리지 않고 같이 들고 간다
- `model_dump()` 할 때도 같이 살아남는다

즉 이 프로젝트에서의 의미는:

- `eogum` 이 엔진 payload 의 모든 미래 필드를 당장 몰라도 된다
- 하지만 **엔진이 준 정보를 훼손하지 않고 그대로 round-trip** 할 수 있다

권장 방향:

- top-level response 모델은 지금처럼 typed field 를 유지
- nested `AiDecision`, `HumanDecision`, `Segment` 류는 `extra="allow"` 또는 raw dict 보존 전략을 사용

### 3.5 권장 스키마 방향

v1 수정 목표:

- `schema_version`, `review_scope`, `join_strategy` 는 typed field 유지
- `segments` 는 기본 shape 검증만 유지
- nested `ai`, `human` 는 unknown field 허용

안전한 구현 선택지:

1. `AiDecision`, `HumanDecision`, `EvalSegment` 에 `extra="allow"` 추가
2. 또는 `segments: list[dict]` 로 더 느슨하게 저장하고, UI에서 필요한 최소 필드만 사용

현재 단계에서는 1번이 더 현실적이다.

## 4. 작업 순서

### Phase 1. avid wrapper / artifact alignment

대상 파일:

- [avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py)
- [job_runner.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/job_runner.py)
- [downloads.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/downloads.py)

작업:

- provider / model / effort 를 settings 기반으로 wrapper 에서 전달
- `sync_diagnostics` artifact 를 초기 처리와 재처리 모두 업로드
- download type 에 `sync_diagnostics` 추가
- content type / filename 규칙 추가

완료 기준:

- `subtitle-cut`, `podcast-cut`, `rebuild-multicam` 결과에서 `sync_diagnostics` 가 있으면 R2로 올라감
- API로 다운로드 가능
- wrapper 에 `claude` 하드코딩이 사라짐

### Phase 2. reprocess reliability

대상 파일:

- [projects.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/projects.py)
- [job_runner.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/job_runner.py)

작업:

- `/multicam` 재처리의 bare thread 제거
- queue/job 기반 재처리 job 도입
- 실패를 `completed` 로 숨기지 않음
- 중복 재처리 방지

완료 기준:

- 재처리 실패 시 사용자에게 실패가 보임
- 동시 실행이 막힘
- 프로세스 재시작에도 상태가 설명 가능함

### Phase 3. review payload schema alignment

대상 파일:

- [schemas.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/models/schemas.py)
- [evaluations.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/evaluations.py)
- [api.ts](/home/jonhpark/workspace/eogum/apps/web/src/lib/api.ts)
- [review/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/projects/[id]/review/page.tsx)

작업:

- `SegmentWithDecision` / `EvalSegment` 중복 제거
- nested decision 모델에 unknown field 허용 또는 raw dict 보존
- frontend 타입도 같은 방향으로 정리
- 저장/조회/재적용 round-trip 검증

완료 기준:

- `avid-cli review-segments` 결과를 저장 후 다시 읽어도 필드가 손실되지 않음
- `apply-evaluation` 로 다시 넘겨도 문제 없음
- human review UI 가 새 payload 로 정상 렌더링됨

### Phase 4. human review UI reconnect

대상 파일:

- [review/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/projects/[id]/review/page.tsx)
- 필요 시 detail page / reprocess action 관련 파일

작업:

- reason 목록을 엔진 enum 과 다시 맞춤
- reviewed export / multicam 재처리 UX 재점검
- `sync_diagnostics` 노출 여부 결정

완료 기준:

- 실제 UI에서 review 저장 -> 재처리 -> 결과 다운로드까지 한 번에 확인 가능

## 5. 바로 수정할 파일 목록

우선순위 순:

1. [apps/api/src/eogum/services/avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py)
2. [apps/api/src/eogum/services/job_runner.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/job_runner.py)
3. [apps/api/src/eogum/routes/projects.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/projects.py)
4. [apps/api/src/eogum/routes/downloads.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/downloads.py)
5. [apps/api/src/eogum/models/schemas.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/models/schemas.py)
6. [apps/api/src/eogum/routes/evaluations.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/evaluations.py)
7. [apps/web/src/lib/api.ts](/home/jonhpark/workspace/eogum/apps/web/src/lib/api.ts)
8. [apps/web/src/app/projects/[id]/review/page.tsx](/home/jonhpark/workspace/eogum/apps/web/src/app/projects/[id]/review/page.tsx)

## 6. 이번 라운드에서 할 것 / 안 할 것

이번 라운드에서 할 것:

- `avid` wrapper 와 artifact alignment
- review payload schema alignment 준비
- 재처리 reliability 설계

이번 라운드에서 안 할 것:

- human review UX 전면 개편
- piecewise sync 같은 엔진 알고리즘 변경
- 프론트 디자인 개편

## 7. 참고 리뷰

- Claude 외부 리뷰: [claude_review.md](/home/jonhpark/workspace/eogum/reviews/260315_0518/claude_review.md)

이 문서는 구현 시작 전 기준 문서다.
실제 수정은 `Phase 1 -> Phase 2 -> Phase 3 -> Phase 4` 순으로 진행한다.
