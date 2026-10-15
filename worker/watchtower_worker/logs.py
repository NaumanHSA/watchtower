from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json"
                         else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers[:] = []
        uv.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # probes every few seconds
