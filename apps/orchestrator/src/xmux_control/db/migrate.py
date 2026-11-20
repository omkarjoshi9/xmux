import hashlib
from pathlib import Path

import psycopg

from xmux_control.settings import Settings

ROOT = Path(__file__).resolve().parents[5]


def migrate(database_url: str, directory: Path | None = None) -> None:
    directory = directory or ROOT / "infrastructure/migrations"
    with psycopg.connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(8773001)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for path in sorted(directory.glob("*.sql")):
            sql = path.read_text()
            digest = hashlib.sha256(sql.encode()).hexdigest()
            existing = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version=%s", (path.name,)
            ).fetchone()
            if existing:
                if existing[0] != digest:
                    raise RuntimeError("applied migration checksum changed")
                continue
            connection.execute(sql)
            connection.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s,%s)", (path.name, digest)
            )


if __name__ == "__main__":
    migrate(Settings.from_env().database_url)
