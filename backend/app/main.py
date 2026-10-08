"""FastAPI application.

    uvicorn backend.app.main:app            (development)
"""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from backend.app import auth
from backend.app.db import Base, make_engine, make_sessionmaker
from backend.app.models import Script
from backend.app.routes import admin as admin_routes
from backend.app.routes import auth as auth_routes
from backend.config import Settings, get_settings
from backend.logging_setup import cap_library_loggers
from worker.runner import Handler, InlineRunner

logger = logging.getLogger(__name__)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def default_job_handler(app: FastAPI) -> Handler:
    from worker.jobs import make_handler  # imports the AI pipeline only when needed

    return make_handler(app.state)


async def requeue_unfinished(app: FastAPI) -> None:
    """After a restart, queue scripts that were waiting or interrupted mid-job."""
    async with app.state.sessionmaker() as db:
        scripts = list(await db.scalars(
            select(Script).where(Script.status.in_(("queued", "processing")))))
        for script in scripts:
            script.status = "queued"
        await db.commit()
    for script in scripts:
        app.state.runner.enqueue(script.id)


def create_app(
    settings: Settings | None = None,
    *,
    create_tables: bool = True,
    extraction_factory: Callable | None = None,
    grader_factory: Callable | None = None,
    job_handler_factory: Callable[[FastAPI], Handler] | None = None,
) -> FastAPI:
    """Build the app. Tests inject settings, fake AI client factories or a job handler."""
    settings = settings or get_settings()
    cap_library_loggers()

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
        handler = (job_handler_factory or default_job_handler)(app)
        app.state.runner = InlineRunner(handler)
        await app.state.runner.start()
        await requeue_unfinished(app)
        try:
            yield
        finally:
            await app.state.runner.stop()
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
    app.include_router(admin_routes.router)
    return app


app = create_app()
