from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiofiles

if TYPE_CHECKING:
    from .config import OutputConfig
    from .models import RouteRecord

logger = logging.getLogger(__name__)


def get_current_date() -> str:
    return datetime.now().strftime("%Y%m%d")


def build_file_path(output_dir: Path, prefix: str, date: str, extension: str) -> Path:
    return output_dir / f"{prefix}_{date}{extension}"


class RotatingFileHandle:
    def __init__(self, output_dir: Path, prefix: str, extension: str = ".jsonl") -> None:
        self.output_dir = output_dir
        self.prefix = prefix
        self.extension = extension
        self._current_date: str = ""
        self._handle: aiofiles.threadpool.text.AsyncTextIOWrapper | None = None

    def _should_rotate(self) -> bool:
        return get_current_date() != self._current_date

    async def _rotate(self) -> None:
        if self._handle:
            await self._handle.close()

        self._current_date = get_current_date()
        file_path = build_file_path(
            self.output_dir, self.prefix, self._current_date, self.extension
        )

        self._handle = await aiofiles.open(file_path, "a", encoding="utf-8")
        logger.info(f"Writing to: {file_path}")

    async def write(self, data: dict[str, Any]) -> None:
        if self._should_rotate():
            await self._rotate()

        assert self._handle is not None
        await self._handle.write(json.dumps(data, ensure_ascii=False) + "\n")
        await self._handle.flush()

    async def close(self) -> None:
        if self._handle:
            await self._handle.close()
            self._handle = None


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

    async def write_routes(self, data: dict[str, Any]) -> None:
        await self._routes_file.write(data)

    async def write_positions(self, positions: list[dict[str, Any]]) -> None:
        if not positions:
            return

        record = {
            "collected_by": "Aleksieienko",
            "timestamp": datetime.now().isoformat(),
            "count": len(positions),
            "positions": positions,
        }
        await self._positions_file.write(record)

    async def write_route_record(self, record: RouteRecord) -> None:
        await self.write_routes(record.to_dict())

    async def close(self) -> None:
        await self._routes_file.close()
        await self._positions_file.close()
