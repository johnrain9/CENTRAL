from __future__ import annotations

import json
import logging
from typing import Any


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.info("%s", json.dumps({"event": event, **fields}, sort_keys=True))
