from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from .models import VehiclePosition, WebSocketStats
from .parsers import MessageParser, parse_handshake_response

if TYPE_CHECKING:
    from .config import KyivCoordinateBounds, QueueConfig, WebSocketConfig
    from .session import AsyncHTTPSession

logger = logging.getLogger(__name__)


class ConcurrentFileQueue:
    def __init__(self, buffer_dir: Path, config: QueueConfig) -> None:
        self._buffer_path = buffer_dir / config.buffer_file
        self._processing_path = buffer_dir / (config.buffer_file + ".processing")
        self._max_size = config.max_size
        self._queue: deque[VehiclePosition] = deque(maxlen=config.max_size)
        self._lock = asyncio.Lock()

    async def recover(self) -> list[VehiclePosition]:
        recovered: list[VehiclePosition] = []
        for path in (self._processing_path, self._buffer_path):
            if not path.exists():
                continue

            try:
                with open(path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        data = json.loads(line)
                        recovered.append(VehiclePosition.from_dict(data))

                os.remove(path)
                logger.info(f"Recovered {len(recovered)} positions from {path.name}")
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning(f"Error recovering from {path}: {e}")

        return recovered

    async def append(self, position: VehiclePosition) -> None:
        async with self._lock:
            if len(self._queue) >= self._max_size:
                logger.warning(
                    f"Queue full ({self._max_size}), dropping oldest position"
                )
            self._queue.append(position)

            try:
                with open(self._buffer_path, "a") as f:
                    f.write(json.dumps(position.to_dict()) + "\n")
            except OSError as e:
                logger.error(f"Failed to write to buffer file: {e}")

    async def flush(self) -> list[VehiclePosition]:
        async with self._lock:
            if not self._queue:
                return []

            positions = list(self._queue)
            self._queue.clear()

            try:
                if self._buffer_path.exists():
                    self._buffer_path.rename(self._processing_path)
            except OSError as e:
                logger.warning(f"Failed to rename buffer file: {e}")

            return positions

    async def confirm_flush(self) -> None:
        try:
            if self._processing_path.exists():
                os.remove(self._processing_path)
        except OSError as e:
            logger.warning(f"Failed to remove processing file: {e}")

    @property
    def size(self) -> int:
        return len(self._queue)


class DeduplicationFilter:
    def __init__(self, ttl: float = 60.0) -> None:
        self._ttl = ttl
        self._seen: dict[tuple[int, int], float] = {}
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 30.0

    def is_duplicate(self, position: VehiclePosition) -> bool:
        now = time.monotonic()
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup(now)

        key = (position.vehicle_id, position.timestamp)
        if key in self._seen:
            return True

        self._seen[key] = now + self._ttl
        return False

    def _cleanup(self, now: float) -> None:
        expired = [k for k, expiry in self._seen.items() if expiry <= now]
        for k in expired:
            del self._seen[k]
        self._last_cleanup = now

    @property
    def tracked_count(self) -> int:
        return len(self._seen)


@dataclass
class ConnectionContext:
    session_id: str
    ping_interval: int
    ws_url: str


class AsyncWebSocketClient:
    def __init__(
        self,
        http_session: AsyncHTTPSession,
        ws_config: WebSocketConfig,
        queue: ConcurrentFileQueue,
        dedup: DeduplicationFilter,
        bounds: KyivCoordinateBounds | None = None,
    ) -> None:
        self._http_session = http_session
        self._config = ws_config
        self._queue = queue
        self._dedup = dedup
        self._parser = MessageParser(bounds)
        self._stats = WebSocketStats()

        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("WebSocket client started")

    async def stop(self) -> None:
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info(
            f"WebSocket client stopped (total positions: {self._stats.position_count})"
        )

    @property
    def is_connected(self) -> bool:
        return self._stats.connected

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "connected": self._stats.connected,
            "message_count": self._stats.message_count,
            "position_count": self._stats.position_count,
            "connection_count": self._stats.connection_count,
            "error_count": self._stats.error_count,
        }

    async def _perform_handshake(self) -> ConnectionContext | None:
        handshake_url = f"{self._config.base_url}/socket.io/?EIO=3&transport=polling"

        try:
            logger.info(f"Socket.IO handshake: {handshake_url}")
            text = await self._http_session.get_text(handshake_url)
            if text is None:
                logger.error("Handshake failed: no response")
                return None

            session_id, ping_interval = parse_handshake_response(text)
            if not session_id:
                logger.error(f"Could not parse session ID from: {text[:100]}")
                return None

            ping_seconds = (ping_interval or 25000) // 1000
            logger.info(f"Got session ID: {session_id[:20]}... (ping: {ping_seconds}s)")

            ws_host = self._config.base_url.replace("https://", "").replace(
                "http://", ""
            )
            ws_url = (
                f"wss://{ws_host}/socket.io/?EIO=3&transport=websocket&sid={session_id}"
            )

            return ConnectionContext(
                session_id=session_id,
                ping_interval=ping_seconds,
                ws_url=ws_url,
            )

        except Exception as e:
            logger.error(f"Handshake error: {e}")
            return None

    async def _connect_websocket(self, context: ConnectionContext) -> bool:
        session = await self._http_session._ensure_session()
        proxy = self._http_session._get_proxy_url()

        try:
            self._ws = await session.ws_connect(
                context.ws_url,
                proxy=proxy,
                headers={
                    "Origin": self._config.base_url,
                },
            )
            return True
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")
            return False

    async def _handle_protocol_message(self, message: str) -> bool:
        assert self._ws is not None

        if message == "3probe":
            logger.info("Received probe response, completing upgrade...")
            await self._ws.send_str("5")
            await self._ws.send_str("40")
            self._stats.connected = True
            logger.info("WebSocket upgrade complete")
            return True

        if not message:
            return False

        packet_type = message[0]

        if packet_type == "3":  # PONG
            logger.debug("Received PONG")
            return True

        if packet_type == "2":  # PING from server
            await self._ws.send_str("3")
            logger.debug("Sent PONG")
            return True

        return False

    async def _handle_message(self, message: str) -> None:
        self._stats.message_count += 1

        if not message:
            return

        if await self._handle_protocol_message(message):
            return

        if self._stats.message_count <= 10:
            logger.info(f"WS message #{self._stats.message_count}: {message[:100]}")

        positions = self._parser.parse(message)
        for pos in positions:
            if not self._dedup.is_duplicate(pos):
                await self._queue.append(pos)
                self._stats.position_count += 1

        if self._stats.message_count % 100 == 0:
            logger.debug(
                f"WS messages: {self._stats.message_count}, "
                f"positions: {self._stats.position_count}"
            )

    async def _receive_loop(self, ping_interval: int) -> None:
        assert self._ws is not None
        last_ping = time.monotonic()

        while self._running and not self._ws.closed:
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=ping_interval)
            except asyncio.TimeoutError:
                if self._stats.connected:
                    try:
                        await self._ws.send_str("2")
                        last_ping = time.monotonic()
                        logger.debug("Sent PING")
                    except Exception:
                        break
                continue

            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle_message(msg.data)
                self._stats.last_update = time.time()
            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                logger.info(f"WebSocket closed: {msg.type}")
                break

            now = time.monotonic()
            if self._stats.connected and now - last_ping > ping_interval:
                try:
                    await self._ws.send_str("2")
                    last_ping = now
                    logger.debug("Sent PING")
                except Exception:
                    break

        self._stats.connected = False

    async def _run_loop(self) -> None:
        delay = self._config.reconnect_delay

        while self._running:
            try:
                context = await self._perform_handshake()
                if not context:
                    logger.warning(f"WS connection failed, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 300)
                    continue

                if not await self._connect_websocket(context):
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 300)
                    continue

                delay = self._config.reconnect_delay
                self._stats.connection_count += 1
                logger.info("WebSocket connected, sending upgrade probe...")
                assert self._ws is not None
                await self._ws.send_str("2probe")

                await self._receive_loop(context.ping_interval)

                if not self._running:
                    break

                logger.info(
                    f"WS connection lost, reconnecting in {self._config.reconnect_delay}s"
                )
                await asyncio.sleep(self._config.reconnect_delay)

            except asyncio.CancelledError:
                break

            except Exception as e:
                logger.error(f"WS client error: {e}")
                self._stats.error_count += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)
