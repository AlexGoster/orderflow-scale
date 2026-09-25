"""Структурное (JSON) логирование на stdlib logging — без внешних зависимостей."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "exc_text",
}

_app_handler: logging.Handler | None = None


class JsonFormatter(logging.Formatter):
    """Форматирует запись лога в одну JSON-строку (для сборщика логов)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def build_formatter(json_output: bool = True) -> logging.Formatter:
    if json_output:
        return JsonFormatter()
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging(level: str = "INFO", json_output: bool = True, force: bool = False) -> None:
    """Настраивает корневой logger. Идемпотентно: обработчик добавляется один раз."""
    global _app_handler
    root = logging.getLogger()

    if _app_handler is not None:
        if not force:
            root.setLevel(level.upper())
            return
        root.removeHandler(_app_handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(build_formatter(json_output))
    root.addHandler(handler)
    root.setLevel(level.upper())
    _app_handler = handler


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
