import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from eogum.config import settings
from eogum.routes import credits, downloads, health, projects, upload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="어검 (eogum) API",
    description="Auto Video Edit Online Service",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://eogum.sudoremove.com",
        "http://localhost:3000",
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


def run():
    uvicorn.run("eogum.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    run()
