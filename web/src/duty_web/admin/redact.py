"""Keeps API keys out of logs: only the ``cs_<8hex>`` prefix survives."""

from __future__ import annotations

import logging
import re

KEY_PATTERN = re.compile(r"cs_[0-9a-f]{8}_[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    return KEY_PATTERN.sub(lambda m: m.group(0)[:11] + "_…", text)


class KeyRedactor(logging.Filter):
    """Werkzeug's access log prints the request line, query string included."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def install(logger_name: str = "werkzeug") -> None:
    logger = logging.getLogger(logger_name)
    if not any(isinstance(f, KeyRedactor) for f in logger.filters):
        logger.addFilter(KeyRedactor())
