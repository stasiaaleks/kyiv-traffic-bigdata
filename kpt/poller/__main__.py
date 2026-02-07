from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from .config import PollerConfig
from .poller import run_poller

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"


def setup_logging(config: PollerConfig) -> None:
    log_dir = config.output.output_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = TimedRotatingFileHandler(
        log_dir / "kpt_poller.log",
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout), file_handler],
    )


async def main() -> None:
    config = PollerConfig()
    setup_logging(config)
    await run_poller(config)


if __name__ == "__main__":
    asyncio.run(main())
