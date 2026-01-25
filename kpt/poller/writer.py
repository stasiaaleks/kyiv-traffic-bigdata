from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, TextIO

if TYPE_CHECKING:
    from config import OutputConfig
    from models import PositionBatch, RouteRecord

logger = logging.getLogger(__name__)


def get_current_date() -> str:
    return datetime.now().strftime("%Y%m%d")


def build_file_path(output_dir: Path, prefix: str, date: str, extension: str) -> Path:
    return output_dir / f"{prefix}_{date}{extension}"


@dataclass
class RotatingFileHandle:
    output_dir: Path
    prefix: str
    extension: str = ".jsonl"
    _current_date: str = field(default="", init=False)
    _handle: TextIO | None = field(default=None, init=False)

    def _should_rotate(self) -> bool:
        return get_current_date() != self._current_date

    def _rotate(self) -> None:
        if self._handle:
            self._handle.close()

        self._current_date = get_current_date()
        file_path = build_file_path(
            self.output_dir, self.prefix, self._current_date, self.extension
        )

        self._handle = open(file_path, "a", encoding="utf-8")
        logger.info(f"Writing to: {file_path}")

    def write(self, data: dict[str, Any]) -> None:
        if self._should_rotate():
            self._rotate()

        assert self._handle is not None
        self._handle.write(json.dumps(data, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "RotatingFileHandle":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class StreamWriter:
    def __init__(self, config: OutputConfig) -> None:
        self.config = config
        config.output_dir.mkdir(parents=True, exist_ok=True)

        self._routes_file = RotatingFileHandle(
            output_dir=config.output_dir,
            prefix=config.routes_file_prefix,
            extension=config.file_extension,
        )
        self._positions_file = RotatingFileHandle(
            output_dir=config.output_dir,
            prefix=config.positions_file_prefix,
            extension=config.file_extension,
        )

    def write_routes(self, data: dict[str, Any]) -> None:
        self._routes_file.write(data)

    def write_positions(self, positions: list[dict[str, Any]]) -> None:
        if not positions:
            return

        record = {
            "timestamp": datetime.now().isoformat(),
            "count": len(positions),
            "positions": positions,
        }
        self._positions_file.write(record)

    def write_route_record(self, record: "RouteRecord") -> None:
        self.write_routes(record.to_dict())

    def write_position_batch(self, batch: "PositionBatch") -> None:
        if batch.count == 0:
            return
        self._positions_file.write(batch.to_dict())

    def close(self) -> None:
        self._routes_file.close()
        self._positions_file.close()

    def __enter__(self) -> "StreamWriter":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


@contextmanager
def stream_writer_context(config: OutputConfig) -> Iterator[StreamWriter]:
    writer = StreamWriter(config)
    try:
        yield writer
    finally:
        writer.close()
