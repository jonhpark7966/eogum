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
- [ ] 리뷰 완료 후 reviewed FCPXML/SRT 자동 생성 (현재 수동 스크립트)
  - 프론트 리뷰 페이지에서 "최종 내보내기" 버튼 추가
  - evaluation → avid.json edit_decisions 머지 → FCPXMLExporter 호출 → R2 업로드
- [ ] 리뷰 페이지 reason 드롭다운에 avid EditReason enum과 맞지 않는 값 정리
  - `meta_comment`, `retake_signal` 등 → avid enum에 추가하거나 매핑 테이블 관리

### Auto Backup
- [ ] R2 데이터 자동 백업 시스템
- [ ] 1년 보관 만료 전 알림 시스템 (유저에게 + 관리자에게)

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
