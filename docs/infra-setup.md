# 어검 인프라 설정 가이드

> 기준 코드: 2026-03-12
> 대상: Supabase + Cloudflare R2 + FastAPI API + Next.js Web

이 문서는 현재 코드 기준 셋업 문서다.
`avid` 를 submodule + CLI-only 구조로 옮기는 목표 문서는 아래를 본다.

- [docs/avid-submodule-layout.md](/home/jonhpark/workspace/eogum/docs/avid-submodule-layout.md)
- [docs/avid-cli-spec.md](/home/jonhpark/workspace/eogum/docs/avid-cli-spec.md)

## 1. 사전 준비

필수 준비물:

- Supabase 프로젝트
- Cloudflare 계정 (R2, Tunnel 사용 시)
- Vercel 프로젝트
- 홈 서버 또는 리눅스 머신
- Python 3.11+
- `ffmpeg`, `ffprobe`, `yt-dlp` 가 서비스 사용자 PATH 에서 실행 가능해야 함
- `auto-video-edit` backend 가 `AVID_BACKEND_ROOT` 에 존재하고 `.venv` 포함
- `AVID_BIN` 이 실제 `avid-cli` 실행 파일을 가리켜야 함
- Chalna 서버가 `CHALNA_URL` 에서 응답해야 함
- avid CLI 에서 사용할 Claude provider 설정 완료

## 2. Supabase 설정

### 프로젝트 생성

1. https://supabase.com/dashboard 에서 새 프로젝트 생성
2. Region: Northeast Asia (`ap-northeast-1`) 권장

### SQL 마이그레이션 실행

아래 파일을 순서대로 적용한다.

1. [supabase/migrations/001_initial.sql](/home/jonhpark/workspace/eogum/supabase/migrations/001_initial.sql)
2. [supabase/migrations/002_evaluations.sql](/home/jonhpark/workspace/eogum/supabase/migrations/002_evaluations.sql)
3. [supabase/migrations/003_extra_sources.sql](/home/jonhpark/workspace/eogum/supabase/migrations/003_extra_sources.sql)

`003_extra_sources.sql` 까지 적용해야 멀티캠 재-export가 정상 동작한다.

### Auth Provider 설정

1. Dashboard > Authentication > Providers
2. Google OAuth 활성화
3. GitHub OAuth 활성화
4. Redirect URL: `https://eogum.sudoremove.com/auth/callback`

### 필요한 키

- Dashboard > Settings > API
  - `SUPABASE_URL`
  - `SUPABASE_ANON_KEY`
  - `SUPABASE_SERVICE_KEY`
- `SUPABASE_JWT_SECRET` 는 현재 백엔드에서 필수가 아니다.
  - API는 Supabase JWKS (`/auth/v1/.well-known/jwks.json`) 기반 ES256 검증을 사용한다.
  - 구형 설정 호환용으로만 남겨둘 수 있다.

## 3. Cloudflare R2 설정

### 버킷 생성

1. Cloudflare Dashboard > R2 > Create Bucket
2. Bucket name: `eogum`
3. Location: Automatic

### Lifecycle Rule

1. Bucket > Settings > Object lifecycle rules
2. Delete after 365 days 규칙 추가

### API 토큰 생성

필요한 값:

- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_ACCOUNT_ID`
- `R2_BUCKET_NAME` (`eogum`)

### CORS 설정

Presigned URL 업로드를 위해 아래 origin 을 허용한다.

```json
[
  {
    "AllowedOrigins": [
      "https://eogum.sudoremove.com",
      "https://eogum.vercel.app",
      "http://localhost:3000"
    ],
    "AllowedMethods": ["GET", "PUT", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }
]
```

로컬 LAN 환경에서 프론트를 띄우면 `http://192.168.x.x:3000` 같은 origin 도 추가해야 한다.

## 4. Cloudflare Tunnel 설정

### 설치 및 로그인

```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel login
```

### 터널 생성

```bash
cloudflared tunnel create eogum-api
mkdir -p ~/.cloudflared
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /home/jonhpark/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: api-eogum.sudoremove.com
    service: http://localhost:8000
  - service: http_status:404
```

### DNS 및 서비스 등록

```bash
cloudflared tunnel route dns eogum-api api-eogum.sudoremove.com
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

## 5. Web (Vercel) 설정

### 환경 변수

Vercel Dashboard > Settings > Environment Variables:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `NEXT_PUBLIC_API_URL=https://api-eogum.sudoremove.com/api/v1`

### 커스텀 도메인

1. Vercel Dashboard > Settings > Domains
2. `eogum.sudoremove.com` 추가
3. Cloudflare DNS 에 CNAME 추가: `eogum.sudoremove.com -> cname.vercel-dns.com`
4. Cloudflare Proxy 는 끄는 편이 안전하다 (`DNS only`)

## 6. API 서버 설정

### 환경 변수

```bash
cd /home/jonhpark/workspace/eogum/apps/api
cp .env.example .env
```

핵심 값:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `AVID_BACKEND_ROOT`
- `AVID_BIN`
- `CHALNA_URL` (기본값 `http://localhost:7861`)
- `RESEND_API_KEY` / `EMAIL_FROM` (선택)

예시는 [apps/api/.env.example](/home/jonhpark/workspace/eogum/apps/api/.env.example) 참고.

### 런타임 의존성

다음 바이너리가 서비스 사용자 기준으로 실행 가능해야 한다.

- `ffmpeg`
- `ffprobe`
- `yt-dlp`

추가로 `AVID_BACKEND_ROOT` / `AVID_BIN` 이 가리키는 avid backend 에서 아래가 동작해야 한다.

```bash
cd /path/to/eogum/third_party/auto-video-edit/apps/backend
source .venv/bin/activate
avid-cli --help
avid-cli version --json
avid-cli doctor --provider claude --json
```

`transcript-overview`, `subtitle-cut`, `podcast-cut` 단계는 현재 `--provider claude` 를 사용한다.

### API 설치 및 실행

```bash
cd /home/jonhpark/workspace/eogum/apps/api
python -m venv .venv
source .venv/bin/activate
pip install -e .
eogum-api
```

또는:

```bash
uvicorn eogum.main:app --host 0.0.0.0 --port 8000
```

### systemd 서비스

샘플 파일: [apps/api/eogum-api.service](/home/jonhpark/workspace/eogum/apps/api/eogum-api.service)

`/etc/systemd/system/eogum-api.service`:

```ini
[Unit]
Description=eogum API Server
After=network.target

[Service]
Type=simple
User=jonhpark
WorkingDirectory=/home/jonhpark/workspace/eogum/apps/api
ExecStart=/home/jonhpark/workspace/eogum/apps/api/.venv/bin/uvicorn eogum.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
EnvironmentFile=/home/jonhpark/workspace/eogum/apps/api/.env

[Install]
WantedBy=multi-user.target
```

적용:

```bash
sudo systemctl daemon-reload
sudo systemctl enable eogum-api
sudo systemctl start eogum-api
```

## 7. 이메일 설정

현재 코드는 Resend 가 없으면 이메일 발송을 건너뛴다. 운영 환경에서는 아래를 설정한다.

1. Resend 가입
2. API Key 생성 -> `RESEND_API_KEY`
3. 발신 도메인 인증
4. `EMAIL_FROM=noreply@sudoremove.com`

## 8. 로컬 개발 체크리스트

- [ ] Supabase 마이그레이션 3개 적용
- [ ] OAuth Provider 설정
- [ ] R2 버킷 및 CORS 설정
- [ ] API `.env` 작성
- [ ] `ffmpeg`, `ffprobe`, `yt-dlp` 설치
- [ ] `AVID_BACKEND_ROOT` / `AVID_BIN` 준비
- [ ] Chalna 실행
- [ ] `apps/api` 실행
- [ ] `apps/web` 에서 `NEXT_PUBLIC_*` 환경 변수 설정 후 실행
