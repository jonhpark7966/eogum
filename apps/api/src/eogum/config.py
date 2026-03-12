from pathlib import Path

from pydantic_settings import BaseSettings


def _default_avid_backend_root() -> Path:
    submodule_root = Path("/home/jonhpark/workspace/eogum/third_party/auto-video-edit/apps/backend")
    legacy_root = Path("/home/jonhpark/workspace/auto-video-edit/apps/backend")
    return submodule_root if submodule_root.exists() else legacy_root


class Settings(BaseSettings):
    # Supabase
    supabase_url: str
    supabase_service_key: str
    supabase_jwt_secret: str = ""  # Not needed for ES256 (JWKS used instead)

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "eogum"
    r2_public_url: str = ""

    # AVID
    avid_backend_root: Path | None = None
    avid_bin: Path | None = None
    avid_cli_path: Path | None = None  # Legacy fallback while local envs migrate.
    avid_temp_dir: Path = Path("/tmp/eogum")
    avid_output_dir: Path = Path("/tmp/eogum/outputs")

    # Chalna
    chalna_url: str = "http://localhost:7861"

    # Email
    resend_api_key: str = ""
    email_from: str = "noreply@sudoremove.com"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def resolved_avid_backend_root(self) -> Path:
        return self.avid_backend_root or self.avid_cli_path or _default_avid_backend_root()

    @property
    def resolved_avid_bin(self) -> Path:
        return self.avid_bin or (self.resolved_avid_backend_root / ".venv" / "bin" / "avid-cli")


settings = Settings()
