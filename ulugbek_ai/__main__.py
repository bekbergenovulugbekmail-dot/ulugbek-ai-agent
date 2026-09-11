"""``python -m ulugbek_ai`` — run the development server."""

from __future__ import annotations

import uvicorn

from ulugbek_ai.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "ulugbek_ai.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
        log_config=None,  # logging is configured in create_app
    )


if __name__ == "__main__":
    main()
