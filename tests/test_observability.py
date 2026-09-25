import json
import logging
import sys

from httpx import AsyncClient

from app.core import logging as app_logging
from app.core.middleware import normalize_path


async def test_request_id_is_generated_when_missing(client: AsyncClient) -> None:
    resp = await client.get("/health")
    request_id = resp.headers["X-Request-ID"]
    assert request_id
    assert len(request_id) == 32


async def test_request_id_is_echoed_back(client: AsyncClient) -> None:
    resp = await client.get("/health", headers={"X-Request-ID": "my-trace-id-1"})
    assert resp.headers["X-Request-ID"] == "my-trace-id-1"


async def test_unsafe_request_id_is_replaced(client: AsyncClient) -> None:
    resp = await client.get("/health", headers={"X-Request-ID": "bad id with spaces <script>"})
    assert resp.headers["X-Request-ID"] != "bad id with spaces <script>"
    assert len(resp.headers["X-Request-ID"]) == 32


async def test_request_id_present_on_error_responses(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/products/999999")
    assert resp.status_code == 404
    assert resp.headers["X-Request-ID"]


async def test_json_formatter_emits_single_json_line() -> None:
    record = logging.LogRecord(
        "orderflow.test", logging.INFO, "module.py", 42, "request completed", (), None
    )
    record.request_id = "abc123"
    record.path = "/api/v1/products"
    record.status = 200

    payload = json.loads(app_logging.JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "orderflow.test"
    assert payload["message"] == "request completed"
    assert payload["request_id"] == "abc123"
    assert payload["path"] == "/api/v1/products"
    assert payload["status"] == 200
    assert payload["ts"].endswith("+00:00")


async def test_json_formatter_serializes_exception() -> None:
    try:
        msg = "boom-value"
        raise ValueError(msg)
    except ValueError:
        record = logging.LogRecord(
            "orderflow.test", logging.ERROR, "m.py", 1, "failed", (), sys.exc_info()
        )

    payload = json.loads(app_logging.JsonFormatter().format(record))
    assert "boom-value" in payload["exception"]
    assert payload["level"] == "ERROR"


async def test_setup_logging_is_idempotent() -> None:
    root = logging.getLogger()
    before = list(root.handlers)

    app_logging.setup_logging("INFO", json_output=True)
    assert list(root.handlers) == before

    app_logging.setup_logging("DEBUG", json_output=False)
    assert list(root.handlers) == before


async def test_plain_formatter_is_used_when_json_disabled() -> None:
    formatter = app_logging.build_formatter(json_output=False)
    record = logging.LogRecord("x", logging.WARNING, "f.py", 1, "careful", (), None)
    assert "careful" in formatter.format(record)


async def test_normalize_path_collapses_numeric_segments() -> None:
    assert normalize_path("/api/v1/products/42") == "/api/v1/products/{id}"
    assert normalize_path("/api/v1/orders/7/status") == "/api/v1/orders/{id}/status"
    assert normalize_path("/api/v1/products") == "/api/v1/products"
    assert normalize_path("") == "/"
