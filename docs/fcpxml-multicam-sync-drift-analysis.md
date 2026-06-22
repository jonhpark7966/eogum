# FCPXML 멀티캠 extra_0 싱크 문제 정리

## 한 줄 결론

이번 문제는 하나의 원인만으로 설명하기 어렵다. 처음에는 FCPXML에서 extra_0 앞의 leading gap이 누락된 구조 문제가 있었고, 그 문제를 수정한 뒤에는 extra_0 미디어 자체의 stream timing과 FCP가 기대하는 nominal 29.97fps 해석 사이의 미세한 차이가 끝부분 drift로 남았다.

현재까지의 판단은 다음과 같다.

- leading `<gap>` 누락은 실제 문제였고 수정되었다.
- 끝부분에서 extra_0 입모양이 main/source 오디오보다 늦어지는 현상은 gap 문제가 아니라 media timing drift 쪽이 더 강하다.
- `frameDuration`을 실제 timing에 맞게 바꾸는 방식은 drift 원인에는 맞지만 FCP relink를 깨뜨렸다.
- `timeMap` retime 방식은 계산상 합리적이고 XML에도 들어갔지만, FCP multicam angle 안에서 기대한 효과가 확인되지 않았다.
- 따라서 다음으로 가장 유력한 검증 후보는 extra_0 미디어 자체를 안정적인 CFR 29.97 파일로 normalize하는 방식이다.

## 문제 현상

프로젝트:

```text
https://eogum.sudoremove.com/projects/20007e30-5966-4fbe-b697-b4db5d0ff745
```

Final Cut Pro에서:

- extra_0를 video angle로 사용
- source/main을 audio angle로 사용
- 초반에는 대략 싱크가 맞음
- 30분 이후, 특히 끝부분으로 갈수록 extra_0 입모양이 main audio보다 늦어짐

즉 현상은 고정 offset이라기보다 시간이 지날수록 차이가 커지는 drift 패턴이다.

## 핵심 개념: offset과 drift는 다르다

### Offset 문제

offset은 시작 위치 문제다.

extra_0가 source보다 약 21.1초 늦게 녹화를 시작했다면, multicam 안에서는 extra_0 앞에 21.1초짜리 빈 공간이 있어야 한다.

```text
source:  |----------------------------------->
extra_0:                     |--------------->
                            21.1s
```

FCPXML에서는 이 빈 공간을 `<gap>`으로 명시하는 것이 안전하다.

```xml
<mc-angle name="extra_0">
  <gap name="Gap" offset="0s" start="0s" duration="1266/60s" />
  <asset-clip ref="r4" offset="1266/60s" start="0s" ... />
</mc-angle>
```

### Drift 문제

drift는 시작은 맞지만 시간이 흐를수록 어긋나는 문제다.

```text
초반: 거의 맞음
중반: 조금 늦음
후반: 더 늦음
```

이 패턴은 초기 offset보다 재생 속도 또는 media timing 해석 문제에 가깝다.

## FCPXML 시간축 구분

이 문제는 세 시간축을 나눠서 봐야 한다.

```text
1. Media time
   원본 파일 자체의 시간

2. Multicam time
   source와 extra_0를 한 multicam 안에 정렬한 시간

3. Project timeline time
   최종 컷 편집본에서 잘라 붙인 시간
```

leading `<gap>`은 multicam time의 시작 위치 문제를 고친다.

반면 끝부분 drift는 extra_0 media time이 FCP 안에서 어떻게 해석되는지와 관련된다.

최종 project timeline에서 끝부분을 보고 있더라도, 그 컷이 원본 multicam의 59분 근처를 참조하면 원본 기준 59분치 drift가 드러날 수 있다.

## 실제 숫자로 보는 drift

extra_0 미디어 정보는 대략 다음과 같다.

```text
video frame count: 107,655
actual video duration: 약 3,591.218초
actual fps: 약 29.96975fps
```

반면 FCPXML에서는 relink 호환성을 위해 다음과 같이 설명했다.

```xml
frameDuration="1001/30000s"
```

이는 nominal 29.97fps다.

```text
fps = 30000 / 1001
fps ≈ 29.97003
```

FCP가 107,655 frames를 nominal 29.97fps로 해석하면:

```text
107,655 × 1001/30000초
≈ 3,592.088초
```

하지만 실제 extra_0 video duration은:

```text
≈ 3,591.218초
```

차이는:

```text
3,592.088 - 3,591.218
≈ 0.870초
```

즉 FCP가 extra_0를 nominal 29.97로 해석하면 전체 길이가 실제보다 약 0.87초 길게 늘어진 것처럼 보일 수 있다. 이는 extra_0 video가 FCP 안에서 아주 조금 느리게 흐르는 것과 같다.

오차 비율은:

```text
0.870 / 3591.218
≈ 0.000242
≈ 0.0242%
```

따라서 대략적인 누적 drift는:

```text
10분:  600초 × 0.000242 ≈ 0.145초
30분: 1800초 × 0.000242 ≈ 0.436초
59분: 3576초 × 0.000242 ≈ 0.866초
```

사용자가 본 "main audio가 extra_0 입모양보다 앞선다"는 방향도 이 설명과 맞다. extra_0 video가 조금 느리게 흐르면, 같은 시점에서 main audio는 이미 지나간 발음을 extra_0 입모양은 아직 따라오지 못한 상태가 된다.

## 지금까지 시도한 수정

### 1. leading `<gap>` 추가

목표:

```text
extra_0가 source보다 21.1초 늦게 시작했다는 multicam 배치를 FCPXML에 명확히 표현
```

변경:

```xml
<gap name="Gap" offset="0s" start="0s" duration="1266/60s" />
<asset-clip ref="r4" offset="1266/60s" start="0s" ... />
```

결과:

- 타당한 수정
- 유지해야 함
- 시작 위치 표현 문제를 해결
- 하지만 누적 drift 자체를 해결하는 수정은 아님

### 2. `frameDuration`을 실제 timing 기반 값으로 변경

목표:

```text
FCP가 extra_0의 media time을 실제 duration/frame count에 가깝게 해석하도록 함
```

의미:

```text
FCP야, 이 파일을 일반 29.97fps로 보지 말고 실제 프레임 간격에 맞춰 해석해줘.
```

장점:

- drift 원인과 직접 관련 있음
- 끝부분 drift를 줄일 가능성이 큼

문제:

- FCP relink가 frame rate mismatch로 실패

대표 에러:

```text
The video frame rates don’t match.

Relinked files must have the same media type, same frame rate,
and similar audio channels as the original files...
```

판단:

- drift에는 맞는 접근이지만 FCP relink identity를 깨므로 운영 방식으로 부적합

### 3. `timeMap` retime 추가

목표:

```text
frameDuration은 nominal 29.97로 유지하여 relink를 깨지 않고,
extra_0 clip만 약 0.024% 빠르게 재생하도록 XML에 표현
```

현재 v2 XML에는 다음과 같은 구조가 들어갔다.

```xml
<asset-clip ref="r4" offset="1266/60s" start="0s" duration="215419/60s" ...>
  <timeMap frameSampling="floor">
    <timept time="0s" value="0s" interp="linear" />
    <timept time="215419/60s" value="1547608225163/430946200s" interp="linear" />
  </timeMap>
  <adjust-conform type="fit" />
</asset-clip>
```

의미:

```text
타임라인에서 약 3590.316초를 재생하는 동안
실제 extra_0 source에서는 약 3591.186초를 소비하라.
```

즉 extra_0를 아주 조금 빠르게 재생시키는 보정이다.

장점:

- relink-safe한 `frameDuration="1001/30000s"` 유지
- 새 미디어 생성 필요 없음
- 계산상 drift 원인과 직접 연결됨

문제:

- XML에는 들어갔지만 FCP에서 끝부분 싱크가 여전히 맞지 않음
- FCP가 multicam angle 내부의 `asset-clip timeMap`을 angle playback/switching에서 기대한 방식으로 적용하지 않는 것으로 의심됨

판단:

- 계산은 합리적이지만, FCP multicam workflow에서 운영 해결책으로 신뢰하기 어려움

## 현재 확정 가능한 것과 아직 불확실한 것

### 확정 가능한 것

```text
1. 현재 다운로드 XML에는 extra_0 앞 gap이 있다.
2. 현재 다운로드 XML에는 timeMap retime도 있다.
3. frameDuration은 relink-safe한 29.97로 유지되어 있다.
4. extra_0 실제 media timing은 nominal 29.97과 미세하게 다르다.
5. 그 차이는 끝부분에서 약 0.86초 drift를 만들 수 있다.
6. 사용자가 본 "main audio가 extra_0 입모양보다 앞선다"는 방향은 이 drift와 맞다.
7. frameDuration을 실제값으로 바꾸는 방식은 relink 에러를 냈다.
```

### 아직 확정 전인 것

```text
1. FCP가 timeMap을 import 단계에서 버렸는지
2. timeMap을 보존하지만 multicam playback에서 무시하는지
3. timeMap을 적용하지만 우리가 기대한 방향/위치와 다르게 적용하는지
4. 실제 관찰 오차가 0.86초 수준인지, 그보다 훨씬 큰지
```

이 부분은 FCP에서 import/relink 후 다시 export한 FCPXML을 보면 더 명확해진다.

## 남은 선택지 비교

### A. gap-only FCPXML

내용:

```text
- leading gap 유지
- frameDuration은 29.97 유지
- timeMap 없음
```

장점:

- FCP import 가능
- relink 가능
- 시작 위치 표현 정상

단점:

- extra_0 timing drift는 해결하지 못함
- 끝부분에서 약 0.8~0.9초 어긋날 수 있음

판단:

- 안전하지만 drift가 남는 선택

### B. frameDuration 변경

내용:

```text
- extra_0의 frameDuration을 실제 duration/frame count에 맞춤
```

장점:

- drift 원인과 직접 관련 있음

단점:

- FCP relink 실패

판단:

- 운영 선택지에서 제외

### C. timeMap retime

내용:

```text
- frameDuration은 29.97 유지
- extra_0 asset-clip 안에 timeMap 추가
- 약 0.024% 빠르게 재생하도록 지시
```

장점:

- relink 조건을 깨지 않음
- 수학적으로 drift 보정값이 맞음
- 새 미디어 생성 필요 없음

단점:

- FCP multicam angle 내부에서 실제 재생에 적용되는지 불확실
- 현재 사용자 관찰상 끝부분 싱크가 여전히 맞지 않음

판단:

- 실험 후보로는 의미가 있지만 운영 기본값으로 삼기 어려움

### D. extra_0 normalize

내용:

```text
- extra_0.mov를 안정적인 CFR 29.97 파일로 변환
- FCPXML이 normalize된 파일을 참조
- frameDuration은 1001/30000s 유지
- leading gap 유지
- timeMap은 사용하지 않거나 최소화
```

장점:

- FCPXML 설명과 실제 미디어 time이 같은 말을 하게 됨
- relink frame rate 문제를 피할 수 있음
- FCP multicam 내부 timeMap 적용 여부에 의존하지 않음
- 운영상 가장 일반화 가능한 후보

단점:

- 미디어 처리 비용 발생
- 새 파일 생성 필요
- 인코딩 설정, 품질, 용량, 처리시간 정책 필요

판단:

- 다음으로 검증할 가장 유력한 해결 후보

## normalize에 대한 주의점

normalize는 전체 프레임을 한 번에 메모리에 올리는 방식이 아니다. 일반적으로 ffmpeg 같은 도구가 프레임을 순차적으로 읽고 순차적으로 새 파일을 쓴다.

따라서 병목은 메모리보다 다음에 가깝다.

```text
- CPU/GPU 인코딩 시간
- 출력 파일 크기
- 화질 유지 정책
- 업로드/다운로드 비용
```

normalize를 운영에 넣기 전에는 다음 정책을 정해야 한다.

```text
- 비디오 코덱: ProRes, H.264, H.265 등
- 오디오 처리: copy, resample, re-encode
- target fps: 30000/1001
- duration 보존 방식
- 원본/normalize 파일 보관 정책
- FCPXML에서 어떤 파일을 참조할지
```

## 다음 검증 제안

### 검증 1. FCP 재-export XML 분석

절차:

```text
1. 현재 v2 FCPXML을 FCP에 import/relink
2. FCP에서 다시 FCPXML export
3. export된 XML 분석
```

확인할 것:

```text
1. extra_0 앞 gap이 유지됐는가?
2. extra_0 asset-clip 안의 timeMap이 유지됐는가?
3. timeMap이 다른 위치나 다른 값으로 바뀌었는가?
4. extra_0의 duration/start/offset이 바뀌었는가?
5. sequence에서 extra_0 angle 선택이 어떻게 표현됐는가?
```

판단:

```text
timeMap이 사라짐
→ FCP가 import 과정에서 버린 것

timeMap은 남아 있음
→ FCPXML 구조로는 보존하지만 multicam 재생에서 실효성 있게 적용하지 않는 것일 수 있음

timeMap이 다른 구조로 바뀜
→ 우리가 만든 구조를 FCP가 재해석한 것
```

### 검증 2. normalize 샘플 테스트

절차:

```text
1. 문제 프로젝트의 extra_0만 안정적인 29.97 CFR 파일로 normalize
2. FCPXML에서 extra_0 asset이 normalize된 파일을 참조하게 변경
3. frameDuration은 1001/30000s 유지
4. leading gap은 유지
5. timeMap은 제거하거나 사용하지 않음
6. FCP import 확인
7. 초반 싱크 확인
8. 30분/끝부분 싱크 확인
9. FCP에서 다시 export해 구조 확인
```

판단:

```text
normalize 샘플에서 끝부분 싱크가 맞음
→ normalize 방향 채택

normalize 샘플에서도 끝부분 싱크가 안 맞음
→ frame timing 외에 추가 offset/편집 구조 문제가 있음
```

## 현재 의사결정 상태

현재 상태를 한 문장으로 정리하면:

```text
timeMap 방식은 이론상 맞지만 FCP multicam에서 신뢰가 부족해졌고,
normalize는 비용이 있지만 문제의 원인층에 더 직접적으로 접근한다.
```

따라서 바로 운영 전체에 normalize를 넣기보다는, 먼저 이 프로젝트의 extra_0 하나만 대상으로 normalize 샘플을 만들어 FCP에서 실제로 끝부분 싱크가 잡히는지 검증하는 것이 가장 합리적이다.

## 최종 요약

```text
문제:
FCP에서 extra_0 video와 source/main audio가 뒤로 갈수록 어긋남.

1차 원인:
FCPXML에서 extra_0 앞 21.1초 gap 누락.
→ 수정 완료.

남은 원인:
extra_0 미디어의 실제 stream timing이 FCPXML의 nominal 29.97 설명과 미세하게 다름.
→ 끝부분에서 약 0.86초 drift 가능.

시도한 해결:
1. frameDuration을 실제값으로 변경
   → drift에는 직접적
   → relink 실패

2. timeMap으로 0.024% retime
   → relink는 유지
   → XML에는 들어감
   → FCP multicam에서 실제 효과 부족

현재 결론:
XML-only 해결은 불안정하다.
normalize 샘플 검증이 다음 단계다.
```
