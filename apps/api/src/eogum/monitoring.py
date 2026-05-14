"""Sentry monitoring setup."""

from __future__ import annotations

import logging
import os

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def init_sentry(
    service_name: str = "eogum-api",
    *,
    dsn: str | None = None,
    environment: str | None = None,
    release: str | None = None,
    traces_sample_rate: float | None = None,
) -> None:
    """Initialize Sentry when SENTRY_DSN is configured."""
    dsn = dsn or os.getenv("SENTRY_DSN")
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=environment or os.getenv("SENTRY_ENVIRONMENT", "local"),
        release=release or os.getenv("SENTRY_RELEASE"),
        traces_sample_rate=traces_sample_rate
        if traces_sample_rate is not None
        else _float_env("SENTRY_TRACES_SAMPLE_RATE", 0.05),
        integrations=[
            FastApiIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", service_name)
