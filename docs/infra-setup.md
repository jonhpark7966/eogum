# 어검 인프라 설정 가이드

## 1. Supabase 프로젝트 설정

### 프로젝트 생성
1. https://supabase.com/dashboard 에서 새 프로젝트 생성
2. Region: Northeast Asia (ap-northeast-1) 권장

### SQL 마이그레이션 실행
```bash
# Supabase CLI 설치
npm install -g supabase

# 마이그레이션 실행 (Supabase Dashboard > SQL Editor 에서도 가능)
# supabase/migrations/001_initial.sql 내용을 실행
```

### Auth Provider 설정
1. Dashboard > Authentication > Providers
2. Google OAuth 활성화 (Google Cloud Console에서 OAuth 2.0 Client ID 생성)
3. GitHub OAuth 활성화 (GitHub Settings > Developer settings > OAuth Apps)
4. Redirect URL: `https://eogum.sudoremove.com/auth/callback`

### 필요한 키 확인
- Dashboard > Settings > API
  - `SUPABASE_URL`: Project URL
  - `SUPABASE_ANON_KEY`: anon public key (프론트엔드용)
  - `SUPABASE_SERVICE_KEY`: service_role key (백엔드용, 비공개)
- Dashboard > Settings > API > JWT Settings
  - `SUPABASE_JWT_SECRET`: JWT Secret

## 2. Cloudflare R2 설정

### 버킷 생성
1. Cloudflare Dashboard > R2 > Create Bucket
2. Bucket name: `eogum`
3. Location: Automatic

### Lifecycle Rule (1년 보관)
1. Bucket > Settings > Object lifecycle rules
2. Add rule:
   - Name: `auto-delete-1year`
   - Scope: All objects
   - Action: Delete after 365 days

### API 토큰 생성
1. R2 > Manage R2 API Tokens > Create API Token
2. Permissions: Object Read & Write
3. Specify bucket: `eogum`
4. 생성 후 복사:
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`
   - `R2_ACCOUNT_ID`: Cloudflare Dashboard 우측 상단에서 확인

### CORS 설정 (presigned URL 업로드용)
Bucket > Settings > CORS Policy:
```json
[
  {
    "AllowedOrigins": [
      "https://eogum.sudoremove.com",
      "http://localhost:3000"
    ],
    "AllowedMethods": ["GET", "PUT", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }
]
```

## 3. Cloudflare Tunnel 설정

### 설치
```bash
# 홈 서버에 cloudflared 설치
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# 인증
cloudflared tunnel login
```

### 터널 생성
```bash
# 터널 생성
cloudflared tunnel create eogum-api

# 설정 파일 생성
mkdir -p ~/.cloudflared
```

`~/.cloudflared/config.yml`:
```yaml
tunnel: <TUNNEL_ID>
credentials-file: /home/jonhpark/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: api.eogum.sudoremove.com
    service: http://localhost:8000
  - service: http_status:404
```

### DNS 설정
```bash
# Cloudflare DNS에 CNAME 추가
cloudflared tunnel route dns eogum-api api.eogum.sudoremove.com
```

### 서비스 등록 (자동 시작)
```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

## 4. Vercel 설정

### 프로젝트 연결
```bash
cd apps/web
npx vercel link
```

### 환경 변수 설정
Vercel Dashboard > Settings > Environment Variables:
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `NEXT_PUBLIC_API_URL` = `https://api.eogum.sudoremove.com/api/v1`

### 커스텀 도메인
1. Vercel Dashboard > Settings > Domains
2. `eogum.sudoremove.com` 추가
3. Cloudflare DNS에 CNAME 추가: `eogum.sudoremove.com` → `cname.vercel-dns.com`
4. Cloudflare에서 해당 레코드의 Proxy를 끄기 (DNS only) — Vercel SSL과 충돌 방지

## 5. 홈 서버 (어검 API) 설정

### 환경 변수
```bash
cd /home/jonhpark/workspace/eogum/apps/api
cp .env.example .env
# .env 파일에 실제 값 입력
```

### 설치 및 실행
```bash
cd /home/jonhpark/workspace/eogum/apps/api
python -m venv .venv
source .venv/bin/activate
pip install -e .

# 실행
eogum-api
# 또는
uvicorn eogum.main:app --host 0.0.0.0 --port 8000
```

### systemd 서비스 (자동 시작)
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

```bash
sudo systemctl enable eogum-api
sudo systemctl start eogum-api
```

## 6. 이메일 (Resend) 설정

1. https://resend.com 가입
2. API Key 생성 → `RESEND_API_KEY`
3. Domain 추가: `sudoremove.com`
4. Cloudflare DNS에 Resend 제공 레코드 추가 (SPF, DKIM, DMARC)
5. 발신 주소: `noreply@sudoremove.com`

## 체크리스트

- [ ] Supabase 프로젝트 생성 + 마이그레이션 실행
- [ ] Google/GitHub OAuth 설정
- [ ] Cloudflare R2 버킷 생성 + lifecycle rule + CORS
- [ ] R2 API 토큰 생성
- [ ] Cloudflare Tunnel 설치 + 설정
- [ ] DNS: api.eogum.sudoremove.com → Tunnel
- [ ] DNS: eogum.sudoremove.com → Vercel
- [ ] Vercel 프로젝트 연결 + 환경 변수
- [ ] 홈 서버 .env 설정
- [ ] eogum-api systemd 서비스 등록
- [ ] Resend 도메인 인증
