"""Shared execution contracts for user-visible acceptance scenarios."""

from __future__ import annotations

import sys
from pathlib import Path


class Blocked(Exception):
    """A required environment, independent sample, or review is unavailable."""


class VisualReviewRequired(Blocked):
    """All technical work passed; only review of these retained artifacts remains.

    Raise this only after completing every technical step and required combination.
    Other missing conditions must use Blocked, which cannot be finalized by review.
    """

    def __init__(self, reason: str, *, identity: dict, sources: list[Path],
                 exports: list[Path], required_checks: list[str],
                 technical_checks: list[str] | None = None):
        super().__init__(reason)
        self.identity = identity
        self.sources = sources
        self.exports = exports
        self.required_checks = required_checks
        self.technical_checks = technical_checks or []


def run_one(filename: str) -> int:
    """Run a directly invoked scenario through the same acceptance gate."""
    from tests.common.run import main

    return main([Path(filename).stem, *sys.argv[1:]])
