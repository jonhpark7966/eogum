# 어검 백엔드 테스트 전략

> 최종 갱신: 2026-03-12
> 범위: `apps/api`
> 원칙: 프론트 없이도 백엔드 API 와 worker 를 검증할 수 있어야 한다.

## 1. 목표

백엔드 테스트의 목표는 세 가지다.

1. 순수 비즈니스 규칙을 빠르게 검증한다.
2. 외부 의존성을 가짜로 대체해 상태 전이와 오류 처리를 검증한다.
3. 실제 HTTP 레벨에서 API 명세가 유지되는지 확인한다.

현재 저장소에는 테스트 파일이 없다.
따라서 리팩터링은 문서 작성 다음 단계부터 모듈 분리와 테스트 추가를 같이 진행해야 한다.

`avid` 연동은 CLI-only 를 목표로 하므로,
테스트도 Python import 가 아니라 CLI 명세와 manifest 출력 기준으로 짜야 한다.

## 2. 테스트 계층

### 2.1 Unit

대상:

- 입력을 받아 계산 결과만 반환하는 함수
- DB, subprocess, thread, network 에 직접 닿지 않는 로직

첫 후보:

- 평가 메트릭 계산
- 세그먼트와 AI decision 병합
- markdown edit report 파싱
- artifact 파일명 정책

### 2.2 Service

대상:

- 모듈 service 가 repository / adapter 를 조합해 상태를 바꾸는 흐름

첫 후보:

- 프로젝트 생성 시 credit check + queue enqueue
- job processing 성공/실패 시 credit hold/confirm/release
- 재-export 요청 시 evaluation + extra_sources 조합
- YouTube task lifecycle

### 2.3 API Integration

대상:

- FastAPI route 와 dependency wiring
- status code, 응답 스키마, 인증/권한 확인

첫 후보:

- `POST /projects`
- `POST /projects/{id}/retry`
- `GET /projects/{id}/segments`
- `POST /projects/{id}/evaluation`
- `GET /projects/{id}/download/{type}`
- `POST /youtube/download`

### 2.4 Smoke

목적:

- 프론트 없이도 백엔드 전체 시나리오가 살아 있는지 확인한다.

최소 시나리오:

1. 파일 업로드 초기화
2. 프로젝트 생성
3. 처리 상태 polling
4. 세그먼트 조회
5. 평가 저장
6. 결과 다운로드 링크 생성

### 2.5 Interface Spec

목적:

- 외부 도구와의 경계가 유지되는지 검증한다.

현재 최우선 대상:

- [docs/avid-integration-spec.md](/home/jonhpark/workspace/eogum/docs/avid-integration-spec.md)
- [docs/avid-submodule-layout.md](/home/jonhpark/workspace/eogum/docs/avid-submodule-layout.md)
- [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)

후보:

- `avid-cli version --json`
- `avid-cli doctor --json`
- transcribe 결과 manifest
- transcript-overview 결과 manifest
- subtitle/podcast 결과 manifest
- reexport 결과 manifest

## 3. 권장 디렉터리 구조

```text
apps/api/tests/
  conftest.py
  unit/
    evaluations/
    processing/
    credits/
    artifacts/
  service/
    projects/
    processing/
    youtube/
  integration/
    test_projects_api.py
    test_evaluations_api.py
    test_downloads_api.py
    test_youtube_api.py
  interface/
    test_avid_cli_version.py
    test_avid_cli_doctor.py
    test_avid_cli_manifests.py
```

## 4. 도구와 실행 기준

현재 [apps/api/pyproject.toml](/home/jonhpark/workspace/eogum/apps/api/pyproject.toml)에 `pytest` 가 이미 포함돼 있으므로,
테스트 체계는 `pytest` 기반으로 시작하는 것이 맞다.

권장 실행 순서:

```bash
cd apps/api
pytest tests/interface
pytest tests/unit
pytest tests/service
pytest tests/integration
```

추가를 권장하는 dev dependency:

- `pytest-cov`
- `pytest-mock`

## 5. 모듈별 우선 테스트 항목

### 5.1 avid Interface

- `version --json` 파싱
- `doctor --json` 파싱
- 각 명령의 manifest 필수 필드 검증
- CLI 실행 실패 시 에러 메시지 전달 검증

### 5.2 Evaluations

- AI decision 과 transcript segment 병합
- implicit agree / disagreement 계산
- confusion matrix, precision, recall, F1

### 5.3 Processing

- report markdown 에서 cut duration / percentage 추출
- 처리 성공 시 상태 전이
- 처리 실패 시 hold release 와 failed 처리
- preview 생성 실패를 non-fatal 로 다루는지

### 5.4 Projects

- create 시 credit 부족 402
- retry 가능한 상태/불가능한 상태 구분
- extra_sources 업데이트 허용 상태 검증

### 5.5 Artifacts

- 지원 file type 검증
- source 와 completed result 의 분기 처리
- filename extension 정책

### 5.6 YouTube

- info fetch 실패 시 400/502 분기
- 다운로드 task 상태 전이
- 본인 task 만 조회 가능해야 함

## 6. 테스트 가능성을 높이기 위한 리팩터링 규칙

1. route 안에서 `subprocess.run`, `threading.Thread`, `sys.path` 조작을 직접 하지 않는다.
2. 순수 계산 함수는 별도 파일로 추출한다.
3. DB 호출은 service 내부에 흩뿌리지 말고 repository 로 모은다.
4. 외부 서비스 호출은 adapter 경계 뒤에 둔다.
5. 시간, UUID, 스레드 시작 같은 부수효과는 주입 가능한 함수로 감싼다.
6. `avid` direct import 는 금지하고 `avid-cli` adapter 만 테스트 대상으로 둔다.

## 7. 통과 기준

모듈 하나를 업데이트할 때 최소 기준:

1. 순수 로직 unit test 추가
2. API 변동이 있으면 integration test 추가
3. 실패 케이스 1개 이상 포함
4. `avid` 연동이 관련되면 interface spec test 추가

저장소 단위 목표:

- 핵심 모듈별 unit test 존재
- 주요 route 별 integration test 존재
- 외부 네트워크 없이 기본 테스트 전체 실행 가능
- `avid-cli` 명세 테스트 존재
- smoke 시나리오 1개 이상 존재

## 8. 이번 단계의 실제 시작점

문서 다음 단계에서 바로 만들 테스트 우선순위는 아래가 적절하다.

1. `avid-cli` 명세 테스트
2. `evaluations.metrics`
3. `processing.report_parser`
4. `processing` 상태 전이 service
5. `projects` create / retry API integration
