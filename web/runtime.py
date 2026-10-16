"""Resolve the common application runtime from Web configuration."""

from any_to_md.application.jobs import JobRuntime

from .config import settings


def get_runtime() -> JobRuntime:
    return JobRuntime(settings.DATA_DIR)
