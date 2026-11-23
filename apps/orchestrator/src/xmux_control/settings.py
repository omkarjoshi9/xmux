import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def secret(name: str, default: str = "") -> str:
    """Allow mounted secret files without putting their values in configuration logs."""
    path = os.getenv(name + "_FILE")
    return Path(path).read_text().strip() if path else os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    environment: str = "development"
    auth_registry_file: str = ""
    cursor_secret: str = field(default="", repr=False)
    multiplexer_url: str = "http://multiplexer:8000"
    multiplexer_token: str = field(default="", repr=False)
    gateway_url: str = "http://gateway:8000"
    gateway_token: str = field(default="", repr=False)
    jwt_issuer: str = ""
    jwt_audience: str = "xmux-orchestrator"
    jwt_public_key_file: str = ""
    requests_per_minute: int = 0
    registry_url: str = ""
    max_request_bytes: int = 262144
    metrics_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        database = secret("DATABASE_URL")
        redis = secret("REDIS_URL", "redis://redis:6379/0")
        if urlsplit(database).scheme not in {"postgres", "postgresql"}:
            raise ValueError("DATABASE_URL must use PostgreSQL")
        if urlsplit(redis).scheme not in {"redis", "rediss"}:
            raise ValueError("REDIS_URL must use Redis")
        return cls(
            database_url=database,
            redis_url=redis,
            environment=os.getenv("XMUX_ENV", "development"),
            auth_registry_file=os.getenv("AUTH_REGISTRY_FILE", ""),
            cursor_secret=secret("CURSOR_SECRET"),
            multiplexer_url=os.getenv("MULTIPLEXER_URL", "http://multiplexer:8000"),
            multiplexer_token=secret("MULTIPLEXER_TOKEN"),
            gateway_url=os.getenv("GATEWAY_URL", "http://gateway:8000"),
            gateway_token=secret("GATEWAY_TOKEN"),
            jwt_issuer=os.getenv("JWT_ISSUER", ""),
            jwt_audience=os.getenv("JWT_AUDIENCE", "xmux-orchestrator"),
            jwt_public_key_file=os.getenv("JWT_PUBLIC_KEY_FILE", ""),
            metrics_token=secret("METRICS_TOKEN"),
            registry_url=os.getenv("MULTIPLEXER_REGISTRY_URL", ""),
            requests_per_minute=int(os.getenv("REQUESTS_PER_MINUTE", "120" if os.getenv("XMUX_ENV") == "production" else "0")),
        )
