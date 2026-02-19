import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from eogum.config import settings
from eogum.routes import credits, downloads, evaluations, health, projects, upload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Recover stuck projects on startup
    from eogum.services.database import get_db
    from eogum.services.job_runner import enqueue

    db = get_db()
    stuck = (
        db.table("projects")
        .select("id")
        .in_("status", ["queued", "processing"])
        .execute()
    )
    for p in stuck.data:
        logger.info("Recovering stuck project: %s", p["id"])
        db.table("projects").update({"status": "queued"}).eq("id", p["id"]).execute()
        enqueue(p["id"])

    if stuck.data:
        logger.info("Recovered %d stuck project(s)", len(stuck.data))

    yield

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
app.include_router(credits.router, prefix="/api/v1")
app.include_router(downloads.router, prefix="/api/v1")
app.include_router(evaluations.router, prefix="/api/v1")


def run():
    uvicorn.run("eogum.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    run()
