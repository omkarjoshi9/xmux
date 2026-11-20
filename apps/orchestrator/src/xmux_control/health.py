import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError

from .settings import Settings


def dependencies(settings: Settings) -> dict[str, bool]:
    result = {"postgres": False, "redis": False}
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as connection:
            result["postgres"] = connection.execute("SELECT 1").fetchone()[0] == 1
    except psycopg.Error:
        result["postgres"] = False
    try:
        with Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2) as redis:
            result["redis"] = bool(redis.ping())
    except RedisError:
        result["redis"] = False
    return result


def mount_health(app: FastAPI, settings: Settings) -> None:
    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        checks = dependencies(settings)
        return JSONResponse(
            {"status": "ready" if all(checks.values()) else "not_ready", "dependencies": checks},
            status_code=200 if all(checks.values()) else 503,
        )
