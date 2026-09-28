import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    model: str = "gemini-3.8-live"
    policy: str = "guarded"
    settle_ms: int = 1000
    write_settle_ms: int = 900
    gate_timeout_s: float = 12.0
    tool_timeout_s: float = 20.0
    latency_profile: str = "instant"
    mode: str = "benchmark"

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env.local", override=False)
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
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    mode = os.getenv("GEMINI_AUTH_MODE", "auto")
    if mode == "auto":
        mode = "ephemeral" if key.startswith("auth_tokens/") else "api_key"
    return key, mode
