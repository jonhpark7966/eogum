# 어검이 기대하는 avid CLI 명령 명세

> 최종 갱신: 2026-03-12
> 범위: `eogum` 백엔드가 호출하는 `avid-cli`
> 상태: 목표 명세. 일부 명령은 아직 `avid` 쪽에 추가 구현이 필요하다.

## 1. 목적

이 문서는 `eogum` 백엔드가 `avid-cli` 를 어떤 명령, 어떤 인자, 어떤 출력 형식으로 호출해야 하는지 정의한다.

핵심 원칙:

- 사람 친화적 stdout 은 유지해도 된다.
- 하지만 `eogum` 이 의존하는 결과는 machine-readable 해야 한다.
- 새 명령은 가능하면 `--json` 또는 `--manifest-out` 을 지원해야 한다.

## 2. 공통 규칙

### 2.1 실행 파일

실행 파일:

- `AVID_BIN=/path/to/eogum/third_party/auto-video-edit/apps/backend/.venv/bin/avid-cli`

실행 디렉터리:

- `cwd=/path/to/eogum/third_party/auto-video-edit/apps/backend`

### 2.2 종료 규칙

- 성공: exit code `0`
- 실패: exit code `!= 0`
- 에러 상세는 stderr 에 남긴다

### 2.3 출력 규칙

모든 핵심 명령은 아래 둘 중 하나 이상을 지원해야 한다.

- `--json`: stdout 에 JSON 출력
- `--manifest-out <path>`: 지정 경로에 JSON manifest 저장

`eogum` 백엔드는 stdout 파싱보다 manifest JSON 을 우선 사용한다.

### 2.4 공통 JSON 필드

권장 공통 필드:

```json
{
  "command": "subtitle-cut",
  "status": "ok",
  "avid_version": "4d60eb6",
  "artifacts": {},
  "stats": {}
}
```

## 3. 기존 명령의 목표 명세

### 3.1 `transcribe`

예시:

```bash
avid-cli transcribe /tmp/source.mp4 -l ko -d /tmp/work --json
```

성공 시 최소 출력 필드:

```json
{
  "command": "transcribe",
  "status": "ok",
  "artifacts": {
    "srt": "/tmp/work/source.srt"
  },
  "stats": {
    "segments": 128,
    "language": "ko"
  }
}
```

### 3.2 `transcript-overview`

예시:

```bash
avid-cli transcript-overview /tmp/work/source.srt -o /tmp/work/storyline.json --provider claude --json
```

성공 시 최소 출력 필드:

```json
{
  "command": "transcript-overview",
  "status": "ok",
  "artifacts": {
    "storyline": "/tmp/work/storyline.json"
  },
  "stats": {
    "chapters": 6,
    "dependencies": 4,
    "key_moments": 12
  }
}
```

### 3.3 `subtitle-cut`

예시:

```bash
avid-cli subtitle-cut /tmp/source.mp4 --srt /tmp/work/source.srt --context /tmp/work/storyline.json -d /tmp/work/output --provider claude --json
```

성공 시 최소 출력 필드:

```json
{
  "command": "subtitle-cut",
  "status": "ok",
  "artifacts": {
    "project_json": "/tmp/work/output/source.subtitle.avid.json",
    "fcpxml": "/tmp/work/output/source_subtitle_cut.fcpxml",
    "srt": "/tmp/work/output/source_subtitle_cut.srt",
    "report": "/tmp/work/output/source.report.md"
  },
  "stats": {
    "edit_decisions": 42
  }
}
```

### 3.4 `podcast-cut`

예시:

```bash
avid-cli podcast-cut /tmp/source.mp3 --srt /tmp/work/source.srt --context /tmp/work/storyline.json -d /tmp/work/output --provider claude --json
```

성공 시 출력 구조는 `subtitle-cut` 과 동일하다.

## 4. 새로 필요한 명령 명세

### 4.1 `reexport`

이 명령은 현재 `eogum` route 가 Python import 로 직접 처리하는 일을 CLI 로 옮기기 위해 필요하다.

예시:

```bash
avid-cli reexport \
  --project-json /tmp/input/project.avid.json \
  --source /tmp/source.mp4 \
  --evaluation /tmp/evaluation.json \
  --extra-source /tmp/extra_0.mp4 \
  --extra-source /tmp/extra_1.mp4 \
  --output-dir /tmp/output \
  --content-mode cut \
  --json
```

성공 시 최소 출력 필드:

```json
{
  "command": "reexport",
  "status": "ok",
  "artifacts": {
    "project_json": "/tmp/output/project.avid.json",
    "fcpxml": "/tmp/output/source_subtitle_cut.fcpxml",
    "srt": "/tmp/output/source_subtitle_cut.srt"
  },
  "stats": {
    "applied_evaluation_segments": 18,
    "extra_sources": 2
  }
}
```

### 4.2 `version`

예시:

```bash
avid-cli version --json
```

성공 시 최소 출력 필드:

```json
{
  "command": "version",
  "status": "ok",
  "avid_version": "4d60eb6"
}
```

### 4.3 `doctor`

예시:

```bash
avid-cli doctor --json
```

성공 시 최소 출력 필드:

```json
{
  "command": "doctor",
  "status": "ok",
  "checks": {
    "python": true,
    "ffmpeg": true,
    "chalna": true,
    "provider": true
  }
}
```

## 5. `eogum` 기능과 CLI 명령 매핑

| `eogum` 기능 | 호출할 `avid-cli` 명령 |
|-------------|------------------------|
| transcription | `transcribe` |
| story analysis | `transcript-overview` |
| lecture edit | `subtitle-cut` |
| podcast edit | `podcast-cut` |
| 평가 반영 재-export | `reexport` |
| startup version check | `version` |
| startup env check | `doctor` |

## 6. `eogum` 백엔드 사용 규칙

1. 결과물 위치는 manifest JSON 을 우선 읽는다.
2. stdout 사람용 메시지는 로그 용도로만 사용한다.
3. `version` 과 `doctor` 는 startup 또는 health 진단에 사용한다.
4. `reexport` 로 `/multicam` direct Python import 경로를 제거한다.

## 7. 현재 구현과의 차이

현재 남아 있는 작업은 아래다.

- startup `doctor` 호출 연결
- adapter 명세 테스트 추가
- 실제 submodule pointer 추가 및 bootstrap 검증

목표는 이 명세대로 수렴하는 것이다.
