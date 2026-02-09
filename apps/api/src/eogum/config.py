from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase
    supabase_url: str
    supabase_service_key: str
    supabase_jwt_secret: str

    # Cloudflare R2
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str = "eogum"
    r2_public_url: str = ""

    # AVID
    avid_cli_path: Path = Path("/home/jonhpark/workspace/auto-video-edit/apps/backend")
    avid_temp_dir: Path = Path("/tmp/eogum")
    avid_output_dir: Path = Path("/tmp/eogum/outputs")

    # Chalna
    chalna_url: str = "http://localhost:8001"

    # Email
    resend_api_key: str = ""
    email_from: str = "noreply@sudoremove.com"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
