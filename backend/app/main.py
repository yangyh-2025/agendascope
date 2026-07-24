"""FastAPI 应用工厂。"""
from fastapi import FastAPI

from app.api.routes import health
from app.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import TraceIdMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(debug=settings.app_debug)

    app = FastAPI(
        title="AgendaScope 观澜",
        version="0.1.0",
        docs_url="/docs" if settings.app_debug else None,
    )
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)

    from app.api.router import api_router

    app.include_router(api_router, prefix="/api/v1")

    from app.api.routes import internal

    app.include_router(internal.router)

    return app


app = create_app()
