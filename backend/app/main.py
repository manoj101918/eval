"""FastAPI application.

    uvicorn backend.app.main:app            (development)
"""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.app import auth
from backend.app.db import Base, make_engine, make_sessionmaker
from backend.app.routes import auth as auth_routes
from backend.config import Settings, get_settings

logger = logging.getLogger(__name__)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(
    settings: Settings | None = None,
    *,
    create_tables: bool = True,
    extraction_factory: Callable | None = None,
    grader_factory: Callable | None = None,
) -> FastAPI:
    """Build the app. Tests inject settings and fake AI client factories."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        if create_tables:  # development/tests; production runs `alembic upgrade head`
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        settings.export_dir.mkdir(parents=True, exist_ok=True)
        app.state.extraction_factory = extraction_factory
        app.state.grader_factory = grader_factory
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="AI Answer Script Evaluator", lifespan=lifespan)

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        # Writes need a custom header, which a cross-site form or image cannot send.
        if (request.method in UNSAFE_METHODS and request.url.path.startswith("/api/")
                and request.headers.get(auth.CSRF_HEADER) != auth.CSRF_VALUE):
            return JSONResponse({"detail": "Missing request header."}, status_code=403)
        return await call_next(request)

    app.include_router(auth_routes.router)
    return app


app = create_app()
