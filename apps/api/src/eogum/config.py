from pathlib import Path
import shutil

from pydantic_settings import BaseSettings


def _default_avid_backend_root() -> Path:
    eogum_root = Path(__file__).resolve().parents[4]
    return eogum_root.parent / "auto-video-edit" / "apps" / "backend"


def _default_yt_dlp_bin() -> Path:
    candidates = [
        Path("/home/jonhpark/.local/bin/yt-dlp"),
        Path("/usr/local/bin/yt-dlp"),
        Path("/usr/bin/yt-dlp"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    resolved = shutil.which("yt-dlp")
    return Path(resolved) if resolved else Path("yt-dlp")


class Settings(BaseSettings):
    # Supabase
    supabase_url: str
    supabase_service_key: str
    supabase_jwt_secret: str = ""  # Not needed for ES256 (JWKS used instead)
    admin_user_ids: str = ""
    admin_emails: str = ""
    public_project_ids: str = ""

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "eogum"
    r2_public_url: str = ""

    # AVID
    avid_backend_root: Path | None = None
    avid_bin: Path | None = None
    avid_cli_path: Path | None = None  # Deprecated. Use AVID_BACKEND_ROOT/AVID_BIN.
    avid_temp_dir: Path = Path("/tmp/eogum")
    avid_output_dir: Path = Path("/tmp/eogum/outputs")
    avid_provider: str = "codex"
    avid_provider_model: str | None = "gpt-5.5"
    avid_provider_effort: str | None = "xhigh"
    junction_audit_global_enabled: bool = True

    # Chalna
    chalna_url: str = "http://localhost:7861"
    huggingface_cache_dir: Path = Path("/tmp/eogum/hf-cache")
    hf_token: str = ""
    huggingface_hub_token: str = ""

    # Tools
    yt_dlp_bin: Path | None = None

    # Email
    resend_api_key: str = ""
    email_from: str = "noreply@sudoremove.com"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    api_public_url: str = ""

    # Job workers
    project_worker_count: int = 1
    reprocess_worker_count: int = 1
    source_derive_worker_count: int = 1
    cut_decision_worker_count: int = 1
    final_preview_worker_count: int = 1

    # Local preview cache
    final_preview_cache_dir: Path = Path("/tmp/eogum/final-previews")
    source_cache_dir: Path = Path("/tmp/eogum/sources")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def resolved_avid_backend_root(self) -> Path:
        return self.avid_backend_root or _default_avid_backend_root()

    @property
    def resolved_avid_bin(self) -> Path:
        return self.avid_bin or (self.resolved_avid_backend_root / ".venv" / "bin" / "avid-cli")

    @property
    def resolved_yt_dlp_bin(self) -> Path:
        return self.yt_dlp_bin or _default_yt_dlp_bin()


settings = Settings()
