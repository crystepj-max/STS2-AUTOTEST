"""Logging utilities for STS2-AUTOTEST."""

import logging


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name.

    Unconditional entry into common/ (allowed even if <3 references
    per architecture rule).
    """
    return logging.getLogger(f"sts2_autotest.{name}")
