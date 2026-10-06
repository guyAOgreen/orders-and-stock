from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from orders_stock.config import Settings, get_settings
from orders_stock.db import make_engine, make_session_factory
from orders_stock.orders.router import router as orders_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = make_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    try:
        yield
    finally:
        engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Orders and Stock", lifespan=_lifespan)
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(orders_router)
    return app
