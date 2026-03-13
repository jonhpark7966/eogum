# 어검 (eogum) - TODO

## Deferred Items

### Desktop App
- [ ] Electron 기반 데스크탑 앱
- [ ] 처리 방식 결정: 서버 처리(Option A) vs 로컬 처리(Option B)
- [ ] ffmpeg 번들링 (ffmpeg-static)
- [ ] Mac 우선, Windows 추후

### Web Preview
- [ ] 웹에서 편집 결과 미리보기 (타임라인 시각화)
- [ ] 영상 플레이어 + 컷 구간 하이라이트

### Eval Data → 프롬프트 피드백 루프
- [ ] eval 데이터 수집 → avid 프롬프트 개선 파이프라인
  - evaluations 테이블에 (project_id, avid_version) 별로 사람의 판단이 쌓임
  - AI와 사람이 불일치한 세그먼트 = 프롬프트 개선 포인트
  - 예: "촬영 준비 발화(슬레이트, 셋업)를 AI가 keep으로 판단" → meta_comment 패턴 학습
- [ ] eval 데이터 기반 정량 지표: 버전별 AI-Human 일치율 추적
- [ ] `avid-cli` provider runtime spec 연동
  - `apps/api/src/eogum/services/avid.py` 의 `--provider claude` 하드코딩 제거
  - provider 이름뿐 아니라 model/effort 도 `avid-cli` 표면으로 전달
  - provider/model/effort 를 audit metadata 와 doctor 결과에 함께 기록
  - `auto-video-edit` 기본 프로필 변경이 있어도 `eogum` 코드 수정 없이 env/config 로 따라갈 수 있게 정리
- [ ] deprecated `reexport` 사용 제거
  - `apps/api/src/eogum/services/avid.py` 의 `reexport()` 호출을 호환용으로만 남기고 새 경로로 교체
  - `apply-evaluation` -> `rebuild-multicam` / `clear-extra-sources` -> `export-project` 순으로 호출하도록 변경
  - `/projects/{id}/multicam` endpoint 이름/의미 재정리
  - 중간 project JSON 은 기존 `settings.avid_temp_dir` 아래 단계별 파일로 유지
  - preview/report 재생성 정책 결정
  - manual offset 을 API 에 노출
- [ ] 리뷰 완료 후 reviewed FCPXML/SRT 자동 생성 (현재 수동 스크립트)
  - 프론트 리뷰 페이지에서 "최종 내보내기" 버튼 추가
  - evaluation → avid.json edit_decisions 머지 → FCPXMLExporter 호출 → R2 업로드
- [ ] 리뷰 페이지 reason 드롭다운에 avid EditReason enum과 맞지 않는 값 정리
  - `meta_comment`, `retake_signal` 등 → avid enum에 추가하거나 매핑 테이블 관리

### Auto Backup
- [ ] R2 데이터 자동 백업 시스템
- [ ] 1년 보관 만료 전 알림 시스템 (유저에게 + 관리자에게)

### FCP 호환성 — ProRes 변환 문제
- **문제**: H.264 소스로 FCPXML 생성 시, FCP에서 relink하면 CPU 100% + 앱 크래시
  - 원인: H.264 keyframe 간격 5초(GOP ~300프레임) × 수백 개 편집 클립 = 각 클립마다 최대 300프레임 디코딩 필요
  - H.264 keyint=60(1초)으로 재인코딩해도 여전히 크래시
  - ProRes Proxy로 변환하면 문제 해결 (모든 프레임 독립 디코딩 가능)
- **현재 워크어라운드**: 소스를 ProRes Proxy로 변환 후 FCPXML 생성
  - 문제: 용량 폭증 (212MB H.264 → 17GB ProRes Proxy)
  - R2 저장/전송 비용 비현실적
- [ ] **해결 방안 조사**:
  - [ ] FCP Proxy 워크플로우: 원본 H.264 유지 + ProRes Proxy를 편집용으로만 생성, 최종 출력 시 원본 relink
  - [ ] FCPXML 내 proxy media 참조 방식 조사 (optimized/proxy media attribute)
  - [ ] 클라이언트 로컬에서 ProRes 변환 (데스크탑 앱 연동 시)
  - [ ] FCP-compatible intermediate codec 비교: ProRes Proxy vs ProRes LT vs DNxHR LB (용량/품질 트레이드오프)
  - [ ] 클립 수 자체를 줄이는 최적화 (인접 keep 구간 병합, 짧은 컷 구간 무시)
  - [ ] Apple의 FCP H.264 성능 제한 공식 문서 확인

### Export Formats
- [ ] Premiere Pro XML 지원 (avid 로드맵)
- [ ] DaVinci Resolve 지원

### Billing
- [ ] Stripe 결제 연동 (크레딧 충전)
- [ ] B2B 계정/계약 관리
- [ ] 팀/조직 계정

### Scaling (나중에)
- [ ] 동시 처리 (worker pool)
- [ ] 클라우드 서버 마이그레이션
- [ ] Job queue (Redis/Celery)

### Infra
- [ ] Cloudflare Tunnel 설정 (api-eogum.sudoremove.com)
- [ ] Vercel 커스텀 도메인 (eogum.sudoremove.com)
- [ ] R2 bucket 생성 + lifecycle rule (1년)
- [ ] 이메일 서비스 설정 (Resend 또는 SES)

### Monitoring
- [ ] 에러 트래킹 (Sentry)
- [ ] 서버 모니터링
- [ ] 잡 처리 통계 대시보드
