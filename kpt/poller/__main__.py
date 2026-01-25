from __future__ import annotations

import logging
import sys

from config import PollerConfig

from poller import run_poller


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    setup_logging()
    config = PollerConfig()
    sys.exit(run_poller(config))


if __name__ == "__main__":
    main()
