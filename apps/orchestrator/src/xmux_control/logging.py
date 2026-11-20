import json
import logging


class SafeJsonFormatter(logging.Formatter):
    """Emit an allowlist, never raw messages, exception bodies or request headers."""

    def format(self, record: logging.LogRecord) -> str:
        fields = {"level": record.levelname, "event": getattr(record, "event", "application_log")}
        for name in ("correlation_id", "task_id", "state", "duration_ms", "status_code"):
            value = getattr(record, name, None)
            if value is not None:
                fields[name] = value
        return json.dumps(fields)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
