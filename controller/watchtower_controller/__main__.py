from __future__ import annotations

import uvicorn

from .app import create_app
from .logs import configure_logging
from .settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None,
                proxy_headers=True,
                forwarded_allow_ips=settings.forwarded_allow_ips)


if __name__ == "__main__":
    main()
