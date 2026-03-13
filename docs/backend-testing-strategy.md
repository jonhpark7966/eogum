# 어검 백엔드 수동 검증 전략

> 최종 갱신: 2026-03-13
> 범위: `apps/api`
> 원칙: 프론트 없이도 실제 사용자 워크플로우 순서대로 검증할 수 있어야 한다.

## 1. 목표

검증 목표는 세 가지다.

1. `POST /projects` 가 초기 편집 workflow 를 끝까지 수행하는지 본다.
2. 그 다음 사람 평가와 멀티캠 추가가 재처리 workflow 로 이어지는지 본다.
3. 마지막 다운로드 FCPXML 이 실제 최종 결과로 이어지는지 본다.

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

### 3.3 초기 편집 workflow

시나리오:

1. source 업로드 초기화
2. `POST /projects`
3. 상태 polling
4. `GET /projects/{id}/segments`
5. `GET /projects/{id}/download/fcpxml`

이 단계의 개념적 내부 순서:

1. `transcribe`
2. `transcript-overview`
3. `subtitle-cut` 또는 `podcast-cut`

확인할 것:

- 프로젝트가 `completed` 까지 가는지
- completed job 에 `project_json`, `fcpxml`, `srt` 가 남는지
- 세그먼트 조회가 `avid-cli review-segments` 기반 payload 로 가능해지는지

### 3.4 사람 평가 반영

시나리오:

1. `POST /projects/{id}/evaluation`
2. 필요하면 평가 결과를 다시 조회
3. `POST /projects/{id}/multicam` with no extra source

이 단계의 개념적 내부 순서:

1. `apply-evaluation`
2. `export-project`

확인할 것:

- 평가가 저장되는지
- 저장 payload 가 그대로 `apply-evaluation` 입력 shape 를 유지하는지
- 새 `project_json`, `fcpxml`, `srt` 가 업로드되는지

### 3.5 멀티캠 추가

시나리오:

1. initial create job 완료
2. 필요하면 human evaluation 저장
3. `extra_sources` 등록
4. 필요하면 `offset_ms` 포함
5. `POST /projects/{id}/multicam`

이 단계의 개념적 내부 순서:

1. 필요 시 `apply-evaluation`
2. `rebuild-multicam`
3. 마지막 `export-project`

확인할 것:

- extra source 가 반영되는지
- offset 이 전달되는지
- 최종 FCPXML 이 다시 생성되는지

### 3.6 멀티캠 제거

시나리오:

1. extra source 가 있던 프로젝트에서 제거 요청
2. `POST /projects/{id}/multicam`

이 단계의 개념적 내부 순서:

1. 필요 시 `apply-evaluation`
2. `clear-extra-sources`
3. `export-project`

확인할 것:

- extra source 가 제거된 결과로 다시 export 되는지

### 3.7 다운로드와 아티팩트 확인

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
3. 재처리는 split command 조합으로 수행한다.
4. manual offset 은 `extra_sources[].offset_ms` 로만 전달한다.
5. deprecated `reexport` 는 새 경로의 기준으로 보지 않는다.

## 6. 현재 결론

- `Python SDK` 방향은 채택하지 않았다.
- 내부 Python 모듈은 존재하지만 public API 가 아니다.
- 외부 오케스트레이터인 `eogum` 이 기대하는 표면은 `avid-cli` 뿐이다.
