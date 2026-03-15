"""Structlog bootstrap for the orc orchestrator.

Call ``setup()`` once at process start (done automatically via the Typer
app callback in ``main.py``).  Every other module in ``.orc/`` then does::

    import structlog
    logger = structlog.get_logger(__name__)

Configuration via environment variables (also settable in ``.env``):

    ORC_LOG_LEVEL   – Minimum log level.  Default: ``INFO``.
    ORC_LOG_FORMAT  – ``console`` (human-readable) or ``json``.  Default: ``console``.
    ORC_LOG_FILE    – Path to the log file.  When running via the CLI the
                      default is ``.orc/logs/orc.log`` (derived from
                      ``orc-log-dir`` in ``config.yaml``).  When
                      ``ORC_LOG_FILE`` and ``ORC_LOG_DIR`` are both unset and
                      no ``default_log_file`` is supplied, falls back to
                      ``~/.cache/orc/orc.log``.
                      Set to an empty string to disable file logging.
    ORC_LOG_DIR     – Override the log folder.  Sets ``ORC_LOG_FILE`` to
                      ``$ORC_LOG_DIR/orc.log`` when ``ORC_LOG_FILE`` is not set.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal, cast

import structlog

_Format = Literal["console", "json"]

_CACHE_DIR = Path.home() / ".cache" / "orc"
_DEFAULT_LOG_FILE = _CACHE_DIR / "orc.log"
_DEFAULT_LEVEL = "INFO"
_DEFAULT_FORMAT: _Format = "console"

# Sentinel to distinguish "caller did not pass log_file" from an explicit None.
_UNSET: object = object()


def setup(
    log_level: str | None = None,
    log_format: _Format | None = None,
    log_file: Path | None | object = _UNSET,
    default_log_file: Path | None = None,
) -> None:
    """Configure structlog and the stdlib logging bridge for orc.

    Reads ``ORC_LOG_LEVEL``, ``ORC_LOG_FORMAT``, and ``ORC_LOG_FILE`` from
    the environment when the corresponding parameter is not explicitly supplied.

    *default_log_file* sets the final fallback path (before ``_DEFAULT_LOG_FILE``)
    so callers can inject a config-derived path without overriding env vars.
    """
    resolved_level: str = log_level or os.environ.get("ORC_LOG_LEVEL", _DEFAULT_LEVEL)
    resolved_format: _Format = cast(
        _Format, log_format or os.environ.get("ORC_LOG_FORMAT", _DEFAULT_FORMAT)
    )

    # Resolve log file:
    # explicit arg > ORC_LOG_FILE > ORC_LOG_DIR > default_log_file > _DEFAULT_LOG_FILE
    if log_file is _UNSET:
        env_val = os.environ.get("ORC_LOG_FILE")
        if env_val is not None:
            resolved_log_file: Path | None = Path(env_val) if env_val else None
        else:
            log_dir_val = os.environ.get("ORC_LOG_DIR")
            if log_dir_val:
                resolved_log_file = Path(log_dir_val) / "orc.log"
            else:
                resolved_log_file = (
                    default_log_file if default_log_file is not None else _DEFAULT_LOG_FILE
                )
    else:
        resolved_log_file = log_file  # type: ignore[assignment]

    numeric_level = getattr(logging, resolved_level.upper(), logging.INFO)

    renderer: structlog.types.Processor
    if resolved_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if resolved_log_file is not None:
        # Route through stdlib so a single FileHandler captures all records.
        resolved_log_file.parent.mkdir(parents=True, exist_ok=True)
        logger_factory: structlog.stdlib.LoggerFactory | structlog.PrintLoggerFactory = (
            structlog.stdlib.LoggerFactory()
        )
        handler: logging.Handler = logging.FileHandler(resolved_log_file, encoding="utf-8")
    else:
        logger_factory = structlog.PrintLoggerFactory()
        handler = logging.StreamHandler()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionPrettyPrinter(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=logger_factory,
        cache_logger_on_first_use=True,
    )

    stdlib_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler.setFormatter(stdlib_formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)
