# 어검 백엔드 수동 검증 전략

> 최종 갱신: 2026-03-13
> 범위: `apps/api`
> 원칙: 프론트 없이도 API 와 worker 를 사람이 직접 검증할 수 있어야 한다.

## 1. 목표

검증 목표는 세 가지다.

1. `avid-cli` 경계가 현재 문서와 맞는지 확인한다.
2. API 와 worker 가 실제 상태 전이와 재처리를 수행하는지 확인한다.
3. 실패 시 어떤 계층에서 깨졌는지 바로 구분할 수 있게 한다.

`avid` 연동은 문서상 `CLI-only` 이다.
따라서 검증도 Python import 가 아니라 CLI 실행 결과와 API 응답 기준으로 진행한다.

## 2. 먼저 볼 문서

- [docs/avid-integration-spec.md](/home/jonhpark/workspace/eogum/docs/avid-integration-spec.md)
- [docs/avid-submodule-layout.md](/home/jonhpark/workspace/eogum/docs/avid-submodule-layout.md)
- [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)
- [third_party/auto-video-edit/apps/backend/TESTING.md](/home/jonhpark/workspace/eogum/third_party/auto-video-edit/apps/backend/TESTING.md)
- [third_party/auto-video-edit/apps/backend/TEST_DATA_GUIDE.md](/home/jonhpark/workspace/eogum/third_party/auto-video-edit/apps/backend/TEST_DATA_GUIDE.md)

## 3. 검증 순서

### 3.1 avid submodule 상태 확인

```bash
git submodule status
third_party/auto-video-edit/apps/backend/.venv/bin/avid-cli version --json
third_party/auto-video-edit/apps/backend/.venv/bin/avid-cli doctor --json
```

확인할 것:

- submodule 이 기대 커밋을 가리키는지
- `avid-cli` 자체가 실행되는지

### 3.2 API 서버 기동

```bash
cd apps/api
eogum-api
```

확인할 것:

- `/health`
- startup 로그에 `avid-cli` 경로 오류가 없는지

### 3.3 단일 프로젝트 처리

시나리오:

1. source 업로드 초기화
2. `POST /projects`
3. 상태 polling
4. `GET /projects/{id}/segments`
5. `GET /projects/{id}/download/fcpxml`

확인할 것:

- 프로젝트가 `completed` 까지 가는지
- completed job 에 `project_json`, `fcpxml`, `srt` 가 남는지

### 3.4 평가 저장 후 재처리

시나리오:

1. `POST /projects/{id}/evaluation`
2. `POST /projects/{id}/multicam` with no extra source

확인할 것:

- 내부적으로 `apply-evaluation` 후 `export-project` 로 이어지는지
- 새로운 `project_json`, `fcpxml`, `srt` 가 업로드되는지

### 3.5 멀티캠 재처리

시나리오:

1. `extra_sources` 등록
2. 필요하면 `offset_ms` 포함
3. `POST /projects/{id}/multicam`

확인할 것:

- extra source 가 있는 경우 `rebuild-multicam`
- extra source 제거만 요청한 경우 `clear-extra-sources`
- 마지막에는 항상 `export-project`

### 3.6 다운로드와 아티팩트 확인

확인할 것:

- `source`, `fcpxml`, `srt`, `project_json` 다운로드 분기
- 존재하지 않는 파일 요청 시 404
- preview 가 없어도 핵심 산출물은 살아 있는지

## 4. 준비물

- Supabase
- R2
- Resend
- Chalna
- `third_party/auto-video-edit/apps/backend/.venv/bin/avid-cli`
- provider CLI (`claude`, `codex`)
- source media 1개 또는 multicam source 2개

## 5. 리팩터링 원칙 확인

수동 검증 중에도 아래 규칙은 계속 유지돼야 한다.

1. `avid` 는 `avid-cli` 로만 호출한다.
2. route 안에서 `avid` Python 모듈을 직접 import 하지 않는다.
3. reprocess 는 split command 조합으로 수행한다.
4. manual offset 은 `extra_sources[].offset_ms` 로만 전달한다.

## 6. 현재 결론

- `Python SDK` 방향은 채택하지 않았다.
- 내부 Python 모듈은 존재하지만 public API 가 아니다.
- 외부 오케스트레이터인 `eogum` 이 기대하는 표면은 `avid-cli` 뿐이다.
