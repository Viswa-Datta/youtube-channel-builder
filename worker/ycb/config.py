"""Worker configuration. Every value comes from the environment; see .env.example."""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://postgres@localhost:54322/postgres"))
    worker_id: str = field(default_factory=lambda: _env("WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"))
    lease_seconds: int = field(default_factory=lambda: int(_env("JOB_LEASE_SECONDS", "90")))
    poll_seconds: float = field(default_factory=lambda: float(_env("JOB_POLL_SECONDS", "2")))
    idle_exit_minutes: int = field(default_factory=lambda: int(_env("IDLE_EXIT_MINUTES", "0")))  # >0: stop when idle
    work_dir: Path = field(default_factory=lambda: Path(_env("WORK_DIR", "/tmp/ycb-work")))

    # storage
    storage_driver: str = field(default_factory=lambda: _env("STORAGE_DRIVER", "local"))  # local | supabase | r2
    local_storage_dir: Path = field(default_factory=lambda: Path(_env("LOCAL_STORAGE_DIR", str(REPO_ROOT / ".storage"))))
    supabase_url: str | None = field(default_factory=lambda: _env("SUPABASE_URL") or _env("NEXT_PUBLIC_SUPABASE_URL"))
    supabase_service_key: str | None = field(default_factory=lambda: _env("SUPABASE_SERVICE_ROLE_KEY"))
    storage_bucket: str = field(default_factory=lambda: _env("STORAGE_BUCKET", "projects"))
    r2_account_id: str | None = field(default_factory=lambda: _env("R2_ACCOUNT_ID"))
    r2_access_key_id: str | None = field(default_factory=lambda: _env("R2_ACCESS_KEY_ID"))
    r2_secret_access_key: str | None = field(default_factory=lambda: _env("R2_SECRET_ACCESS_KEY"))
    r2_bucket: str = field(default_factory=lambda: _env("R2_BUCKET", "ycb-media"))
    r2_endpoint: str | None = field(default_factory=lambda: _env("R2_ENDPOINT"))  # tests only; default derives from the account id

    # LLM providers
    openai_api_key: str | None = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    gemini_api_key: str | None = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    # AI illustrations (needs the Gemini API paid tier): off | images | mixed (images + a few short video clips)
    gen_media: str = field(default_factory=lambda: (_env("GEN_MEDIA", "mixed") or "mixed").lower())
    gen_images_max: int = field(default_factory=lambda: int(_env("GEN_IMAGES_MAX", "20")))
    gen_videos_max: int = field(default_factory=lambda: int(_env("GEN_VIDEOS_MAX", "6")))
    gen_video_seconds: int = field(default_factory=lambda: int(_env("GEN_VIDEO_SECONDS", "6")))
    ollama_url: str | None = field(default_factory=lambda: _env("OLLAMA_URL"))
    allow_fixtures: bool = field(default_factory=lambda: _env("ALLOW_FIXTURES", "false") == "true")

    # Voice providers
    elevenlabs_api_key: str | None = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    google_tts_api_key: str | None = field(default_factory=lambda: _env("GOOGLE_TTS_API_KEY"))
    kokoro_model: Path = field(default_factory=lambda: Path(_env("KOKORO_MODEL", "/models/kokoro-v1.0.onnx")))
    kokoro_voices: Path = field(default_factory=lambda: Path(_env("KOKORO_VOICES", "/models/voices-v1.0.bin")))
    kokoro_vocab: Path = field(default_factory=lambda: Path(_env("KOKORO_VOCAB", "/models/kokoro-vocab.json")))
    espeak_bin: str = field(default_factory=lambda: _env("ESPEAK_BIN", "espeak-ng"))

    # Vision models for the blur pipeline
    face_model: Path = field(default_factory=lambda: Path(_env("FACE_MODEL", "/models/face_detection_yunet_2023mar.onnx")))
    plate_model: Path = field(default_factory=lambda: Path(_env("PLATE_MODEL", "/models/yolo-v9-t-384-license-plates-end2end.onnx")))

    # Footage + research sources (server-side keys only)
    pexels_api_key: str | None = field(default_factory=lambda: _env("PEXELS_API_KEY"))
    pixabay_api_key: str | None = field(default_factory=lambda: _env("PIXABAY_API_KEY"))
    courtlistener_token: str | None = field(default_factory=lambda: _env("COURTLISTENER_TOKEN"))
    nara_api_key: str | None = field(default_factory=lambda: _env("NARA_API_KEY"))
    youtube_api_key: str | None = field(default_factory=lambda: _env("YOUTUBE_API_KEY"))  # YouTube Data API key: search only
    data_gov_api_key: str | None = field(default_factory=lambda: _env("DATA_GOV_API_KEY"))  # api.data.gov: Smithsonian Open Access
    # auto: the agent fetches only freely licensed media and never asks the user to save clips (default).
    # assisted: also finds official videos it may not download (e.g. agency YouTube uploads) and asks the user.
    footage_mode: str = field(default_factory=lambda: _env("FOOTAGE_MODE", "auto"))

    # YouTube
    google_client_id: str | None = field(default_factory=lambda: _env("GOOGLE_OAUTH_CLIENT_ID"))
    google_client_secret: str | None = field(default_factory=lambda: _env("GOOGLE_OAUTH_CLIENT_SECRET"))
    token_encryption_key: str | None = field(default_factory=lambda: _env("TOKEN_ENCRYPTION_KEY"))

    fonts_dir: Path = field(default_factory=lambda: Path(_env("FONTS_DIR", "/usr/share/fonts")))
    music_dir: Path = field(default_factory=lambda: Path(_env("MUSIC_DIR", str(REPO_ROOT / "assets" / "music"))))

    def missing_for(self, provider: str) -> list[str]:
        """Names of env vars a provider needs that are not set. Used to stop and list, never to invent."""
        need = {
            "openai": ["OPENAI_API_KEY"], "anthropic": ["ANTHROPIC_API_KEY"], "gemini": ["GEMINI_API_KEY"],
            "ollama": ["OLLAMA_URL"], "elevenlabs": ["ELEVENLABS_API_KEY"], "google": ["GOOGLE_TTS_API_KEY"],
            "pexels": ["PEXELS_API_KEY"], "pixabay": ["PIXABAY_API_KEY"],
            "youtube": ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "TOKEN_ENCRYPTION_KEY"],
            "supabase": ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"],
            "r2": ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"],
        }.get(provider, [])
        env_map = {
            "OPENAI_API_KEY": self.openai_api_key, "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "GEMINI_API_KEY": self.gemini_api_key, "OLLAMA_URL": self.ollama_url,
            "ELEVENLABS_API_KEY": self.elevenlabs_api_key, "GOOGLE_TTS_API_KEY": self.google_tts_api_key,
            "PEXELS_API_KEY": self.pexels_api_key, "PIXABAY_API_KEY": self.pixabay_api_key,
            "GOOGLE_OAUTH_CLIENT_ID": self.google_client_id, "GOOGLE_OAUTH_CLIENT_SECRET": self.google_client_secret,
            "TOKEN_ENCRYPTION_KEY": self.token_encryption_key, "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_key,
            "R2_ACCOUNT_ID": self.r2_account_id, "R2_ACCESS_KEY_ID": self.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key,
        }
        return [n for n in need if not env_map.get(n)]


class MissingConfig(RuntimeError):
    """Raised when a step needs a key/account that is not configured. The job fails with the list, it never fakes output."""

    def __init__(self, provider: str, names: list[str]):
        self.provider, self.names = provider, names
        super().__init__(f"{provider} is not configured: set {', '.join(names)}")


def require(provider: str) -> None:
    missing = settings.missing_for(provider)
    if missing:
        raise MissingConfig(provider, missing)


settings = Settings()


def load_json(rel: str) -> dict:
    with open(REPO_ROOT / rel, encoding="utf-8") as f:
        return json.load(f)
