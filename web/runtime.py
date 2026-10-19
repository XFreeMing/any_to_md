"""Resolve the common application runtime from Web configuration."""

from src.application.jobs import JobRuntime

from .config import settings


def get_runtime() -> JobRuntime:
    return JobRuntime(settings.DATA_DIR)
