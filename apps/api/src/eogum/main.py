import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from eogum.config import settings
from eogum.routes import credits, downloads, evaluations, health, projects, sources, upload, youtube

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from eogum.services.job_runner import (
        recover_stuck_final_previews,
        recover_stuck_projects,
        start_stuck_project_sweeper,
    )

    try:
        recovered = recover_stuck_projects(recover_running=True)
        if recovered:
            logger.info("Recovered %d stuck project(s) on startup", recovered)
    except Exception:
        logger.exception("Startup stuck project recovery failed")

    try:
        recovered_previews = recover_stuck_final_previews(recover_running=True)
        if recovered_previews:
            logger.info("Recovered %d stuck final-preview job(s) on startup", recovered_previews)
    except Exception:
        logger.exception("Startup final-preview recovery failed")

    sweeper_stop = start_stuck_project_sweeper(interval_seconds=60)
    try:
        yield
    finally:
        sweeper_stop.set()

app = FastAPI(
    title="어검 (eogum) API",
    description="Auto Video Edit Online Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://eogum.sudoremove.com",
        "https://eogum.vercel.app",
        "http://localhost:3000",
        "http://192.168.0.3:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(upload.router, prefix="/api/v1")
app.include_router(projects.router, prefix="/api/v1")
app.include_router(sources.router, prefix="/api/v1")
app.include_router(credits.router, prefix="/api/v1")
app.include_router(downloads.router, prefix="/api/v1")
app.include_router(evaluations.router, prefix="/api/v1")
app.include_router(youtube.router, prefix="/api/v1")


def run():
    uvicorn.run("eogum.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    run()
