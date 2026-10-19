import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def source_digest():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "src/reprise").glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class Settings:
    model: str = "gemini-3.8-live"
    policy: str = "guarded"
    settle_ms: int = 1000
    write_settle_ms: int = 900
    gate_timeout_s: float = 30.0
    tool_timeout_s: float = 20.0
    latency_profile: str = "instant"
    mode: str = "benchmark"

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env.local", override=False)
        load_dotenv(ROOT / ".env", override=False)
        return cls(
            model=os.getenv("GEMINI_MODEL", "gemini-3.8-live"),
            policy=os.getenv("REPRISE_POLICY", "guarded"),
            settle_ms=int(os.getenv("REPRISE_SETTLE_MS", "1000")),
            write_settle_ms=int(os.getenv("REPRISE_WRITE_SETTLE_MS", "900")),
            latency_profile=os.getenv("REPRISE_LATENCY_PROFILE", "instant"),
            mode=os.getenv("REPRISE_MODE", "benchmark"),
        )


def credential():
    load_dotenv(ROOT / ".env.local", override=False)
    load_dotenv(ROOT / ".env", override=False)
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    mode = os.getenv("GEMINI_AUTH_MODE", "auto")
    if mode == "auto":
        mode = "ephemeral" if key.startswith("auth_tokens/") else "api_key"
    return key, mode
