"""Entry point for the API process."""

import uvicorn

from orders_stock.api.app import create_app
from orders_stock.config import get_settings
from orders_stock.logging_config import configure_logging

# Module-level application for `fastapi dev` / `uvicorn orders_stock.api.main:app`.
app = create_app()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "orders_stock.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
