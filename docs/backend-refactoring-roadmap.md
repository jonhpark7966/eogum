# 어검 백엔드 리팩터링 로드맵

> 최종 갱신: 2026-03-14
> 범위: `apps/api`
> 전제: 프론트엔드 개선은 뒤로 미루고, 백엔드 단독 운영 가능성을 먼저 높인다.

## 1. 원칙

1. API 명세를 가능한 한 유지한다.
2. 모듈 분리와 수동 workflow 검증 문서 정리를 같은 단계에서 진행한다.
3. 순수 로직 추출이 먼저고, 폴더 이동은 그 다음이다.
4. 프론트 요구가 아니라 백엔드 상태 정합성과 운영 가능성을 기준으로 우선순위를 정한다.
5. `avid` 연동은 최종적으로 CLI-only + submodule 구조로 수렴한다.

## 2. 단계별 계획

현재 진행 메모:

- Phase 1의 핵심 토대는 이미 들어갔다.
  - submodule + CLI-only 호출
  - split command 후처리 경로
  - manual offset API 노출
- Phase 2의 핵심 토대도 이미 들어갔다.
  - `avid-cli review-segments`
  - engine-native review payload 저장/재적용
  - legacy overlap fallback 유지

### Phase 0. 문서 기준선 고정

산출물:

- [docs/backend-module-map.md](/home/jonhpark/workspace/eogum/docs/backend-module-map.md)
- [docs/backend-testing-strategy.md](/home/jonhpark/workspace/eogum/docs/backend-testing-strategy.md)
- [docs/avid-integration-spec.md](/home/jonhpark/workspace/eogum/docs/avid-integration-spec.md)
- [docs/avid-submodule-layout.md](/home/jonhpark/workspace/eogum/docs/avid-submodule-layout.md)
- [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)
- 이 로드맵 문서

완료 기준:

- 백엔드 모듈 경계와 순서가 문서로 합의됨
- `avid` submodule 경로와 CLI 명세가 문서로 고정됨
- 프론트 작업이 현재 범위 밖임이 명시됨

### Phase 1. avid CLI-only 경계 정리

목표:

- `eogum` 이 `avid` Python 모듈을 직접 import 하지 않도록 만든다.
- `avid` 를 submodule + CLI 명세 기반 외부 엔진으로 고정한다.

현재 대상:

- [avid.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/services/avid.py)
- [projects.py](/home/jonhpark/workspace/eogum/apps/api/src/eogum/routes/projects.py#L130)

산출물:

- `adapters/avid_cli.py`
- `avid-cli version --json`
- `avid-cli doctor --json`
- `avid-cli apply-evaluation`
- `avid-cli rebuild-multicam`
- `avid-cli clear-extra-sources`
- `avid-cli export-project`
- manifest JSON 기반 결과 수집

검증:

- `version` / `doctor` live CLI smoke
- 초기 workflow 와 후처리 workflow 수동 시나리오
- deprecated `reexport` 는 parity 확인만 수행

현재 상태:

- 완료
- 남은 일은 compatibility wrapper 제거 시점과 endpoint naming 정리

### Phase 2. Evaluations 분리

목표:

- 평가 도메인을 순수 로직 중심으로 먼저 분리한다.
- review payload 의 source of truth 를 `eogum` 이 아니라 `avid-cli` 로 돌린다.

산출물:

- `avid-cli review-segments`
- `evaluations/segments.py`
- `evaluations/metrics.py`
- `evaluations/service.py`
- `evaluations/versions.py`

검증:

- segment merge / metric 계산을 engine-native review payload 기준으로 확인
- evaluation save / get API 수동 시나리오
- `eogum` 이 저장한 evaluation JSON 을 그대로 `avid-cli apply-evaluation` 에 넣는 round-trip 검증

현재 상태:

- 핵심 payload 정렬은 완료
- 남은 일은 route slim-down 과 metrics/service 추출

### Phase 3. Processing 분리

목표:

- worker 와 처리 흐름을 백엔드의 핵심 모듈로 독립시킨다.

산출물:

- `processing/worker.py`
- `processing/service.py`
- `processing/reprocess.py`
- `processing/report_parser.py`

검증:

- report parser 수동 검증 경로 정리
- 성공/실패 상태 전이와 retry / postprocess 수동 시나리오

### Phase 4. Projects / Artifacts 분리

목표:

- 프로젝트 메타데이터 관리와 결과물 접근 정책을 분리한다.

산출물:

- `projects/service.py`
- `projects/repository.py`
- `artifacts/service.py`

검증:

- create/list/detail/delete API 수동 시나리오
- download type validation
- extra_sources / offset 상태 검증

### Phase 5. Credits 분리 및 정합성 강화

목표:

- 크레딧 계산과 ledger 기록을 별도 모듈로 고정한다.

산출물:

- `credits/service.py`
- `credits/repository.py`

검증:

- available balance 계산 결과 확인
- hold / confirm / release 흐름 수동 점검
- credit API 수동 시나리오

### Phase 6. YouTube 분리

목표:

- 메타데이터 조회, task registry, 다운로드 worker 를 분리한다.

산출물:

- `youtube/service.py`
- `youtube/task_registry.py`
- `youtube/worker.py`

검증:

- info fetch 실패/성공 수동 시나리오
- task ownership 확인
- 다운로드 상태 전이 점검

### Phase 7. 런타임 하드닝

목표:

- 재시작 복구, worker 충돌 방지, smoke 검증을 추가한다.

산출물:

- startup recovery 범위 재정의
- active worker 충돌 방지 규칙
- API-only smoke 시나리오 문서 또는 스크립트

검증:

- recovery 수동 점검
- API-only smoke test

## 3. 이번 라운드에서 하지 않을 것

- Next.js 페이지 컴포넌트 분리
- 프론트 API client 재구성
- 프론트 E2E 테스트

이들은 백엔드 모듈 경계와 API 명세가 안정된 뒤에 진행한다.

## 4. 각 단계의 완료 체크리스트

1. 대상 모듈 책임이 문서와 일치한다.
2. route 파일이 얇아졌다.
3. 해당 단계의 핵심 동작에 대한 수동 검증 문서가 갱신됐다.
4. 해당 API 변화에 대한 live workflow 검증 경로가 있다.
5. 기존 동작을 깨지 않았다는 수동 검증 기록이 있다.
6. `avid` direct import 와 `sys.path` 조작이 제거됐다.

## 5. 추천 실제 작업 순서

1. `avid-submodule-layout` / `avid-cli-spec` 기준으로 backend adapter 정리
2. split command 기준 후처리 경로 정리
3. `evaluations.metrics` 추출
4. `processing.report_parser` 추출
5. `processing.reprocess` 추출
6. `projects` route slim-down
7. `credits` 정합성 강화
8. `youtube` 분리
