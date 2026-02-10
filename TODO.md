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
