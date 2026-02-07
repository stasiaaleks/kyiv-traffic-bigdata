from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime

from .config import PollerConfig
from .models import PollerStats, RouteRecord
from .session import AsyncHTTPSession, CookiesExpiredError
from .websocket_client import (
    AsyncWebSocketClient,
    ConcurrentFileQueue,
    DeduplicationFilter,
)
from .writer import StreamWriter

logger = logging.getLogger(__name__)


class KPTPoller:
    def __init__(self, config: PollerConfig) -> None:
        self.config = config
        self._stats = PollerStats()
        self._start_time = time.monotonic()
        self._running = False

    async def run(self) -> None:
        self._log_config()
        self._running = True
        backoff_delay = self.config.retry.base_delay

        while self._running:
            try:
                async with AsyncHTTPSession(self.config.proxy) as session:
                    await self._run_session(session)

                backoff_delay = self.config.retry.base_delay

            except asyncio.CancelledError:
                logger.info("Poller cancelled")
                break
            except Exception as e:
                logger.exception(f"Poller error: {e}")
                logger.info(f"Restarting in {backoff_delay}s...")
                await asyncio.sleep(backoff_delay)
                backoff_delay = min(backoff_delay * 2, self.config.retry.max_delay)

    async def _run_session(self, session: AsyncHTTPSession) -> None:
        writer = StreamWriter(self.config.output)
        position_queue = ConcurrentFileQueue(
            self.config.output.output_dir, self.config.queue
        )
        dedup = DeduplicationFilter()

        await self._recover_buffered_positions(position_queue, writer)

        tasks, ws_client = await self._create_tasks(
            session, writer, position_queue, dedup
        )

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            raise
        finally:
            await self._shutdown_tasks(tasks, ws_client, position_queue, writer)

    async def _recover_buffered_positions(
        self, queue: ConcurrentFileQueue, writer: StreamWriter
    ) -> None:
        recovered = await queue.recover()
        if recovered:
            await writer.write_positions([p.to_dict() for p in recovered])
            logger.info(f"Wrote {len(recovered)} recovered positions")

    async def _create_tasks(
        self,
        session: AsyncHTTPSession,
        writer: StreamWriter,
        position_queue: ConcurrentFileQueue,
        dedup: DeduplicationFilter,
    ) -> tuple[list[asyncio.Task[None]], AsyncWebSocketClient | None]:
        tasks: list[asyncio.Task[None]] = []
        ws_client: AsyncWebSocketClient | None = None

        tasks.append(asyncio.create_task(self._poll_routes_loop(session, writer)))

        if self.config.websocket.enabled:
            ws_client = AsyncWebSocketClient(
                http_session=session,
                ws_config=self.config.websocket,
                queue=position_queue,
                dedup=dedup,
                bounds=self.config.bounds,
            )
            await ws_client.start()

            tasks.append(
                asyncio.create_task(self._flush_positions_loop(position_queue, writer))
            )

        tasks.append(asyncio.create_task(self._stats_loop(position_queue, dedup)))

        return tasks, ws_client

    async def _shutdown_tasks(
        self,
        tasks: list[asyncio.Task[None]],
        ws_client: AsyncWebSocketClient | None,
        position_queue: ConcurrentFileQueue,
        writer: StreamWriter,
    ) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        if ws_client is not None:
            await ws_client.stop()

        remaining = await position_queue.flush()
        if remaining:
            await writer.write_positions([p.to_dict() for p in remaining])
            await position_queue.confirm_flush()
            logger.info(f"Flushed {len(remaining)} remaining positions on shutdown")

        await writer.close()

    def _log_config(self) -> None:
        logger.info("KPT Poller (async, REST API + WebSocket)")
        for key, value in self.config.to_dict().items():
            logger.info(f"  {key}: {value}")

    async def _poll_routes_loop(
        self, session: AsyncHTTPSession, writer: StreamWriter
    ) -> None:
        consecutive_failures = 0
        max_failures = self.config.api.max_consecutive_failures

        logger.info("Starting routes polling loop...")

        while self._running:
            self._stats.poll_count += 1
            timestamp = datetime.now().isoformat()
            url = self.config.api.routes_url

            try:
                routes = await session.get_json(url)

                if routes is None:
                    consecutive_failures += 1
                    self._stats.record_poll_failure()
                    logger.warning(
                        f"[Poll #{self._stats.poll_count}] Failed "
                        f"({consecutive_failures}/{max_failures})"
                    )
                else:
                    consecutive_failures = 0
                    self._stats.record_poll_success()

                    record = RouteRecord(
                        timestamp=timestamp,
                        poll_number=self._stats.poll_count,
                        routes=routes if isinstance(routes, list) else [routes],
                    )
                    await writer.write_route_record(record)
                    logger.info(
                        f"[Poll #{self._stats.poll_count}] Fetched {record.route_count} routes"
                    )

            except CookiesExpiredError:
                consecutive_failures += 1
                self._stats.record_poll_failure()
                logger.warning("Got 403 - refreshing session")
                await session.refresh_session()

            except Exception as e:
                consecutive_failures += 1
                self._stats.record_poll_failure()
                logger.error(f"Poll error: {e}")

            if consecutive_failures >= max_failures:
                logger.warning("Too many consecutive failures, refreshing session...")
                await session.refresh_session()
                consecutive_failures = 0

            await asyncio.sleep(self.config.api.poll_interval)

    async def _flush_positions_loop(
        self, queue: ConcurrentFileQueue, writer: StreamWriter
    ) -> None:
        while self._running:
            await asyncio.sleep(self.config.websocket.flush_interval)

            positions = await queue.flush()
            if not positions:
                continue

            try:
                await writer.write_positions([p.to_dict() for p in positions])
                await queue.confirm_flush()
                self._stats.record_position_flush(len(positions))

                logger.info(
                    f"[WS #{self._stats.ws_flush_count}] Flushed {len(positions)} positions "
                    f"(total: {self._stats.total_positions})"
                )
            except Exception as e:
                logger.error(f"Failed to write positions: {e}")

    async def _stats_loop(
        self, queue: ConcurrentFileQueue, dedup: DeduplicationFilter
    ) -> None:
        while self._running:
            await asyncio.sleep(self.config.stats.log_interval)

            uptime = round(time.monotonic() - self._start_time, 1)
            logger.info(
                f"Stats: polls={self._stats.poll_count} "
                f"failed={self._stats.polls_failed} "
                f"positions={self._stats.total_positions} "
                f"flushes={self._stats.ws_flush_count} "
                f"queue={queue.size} dedup={dedup.tracked_count} "
                f"uptime={uptime}s"
            )

    def stop(self) -> None:
        self._running = False


async def run_poller(config: PollerConfig) -> None:
    poller = KPTPoller(config)

    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("Received shutdown signal")
        poller.stop()

    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig:
            loop.add_signal_handler(sig, _signal_handler)

    try:
        await poller.run()
    finally:
        stats = poller._stats
        logger.info(
            f"Final: polls={stats.poll_count} failed={stats.polls_failed} "
            f"positions={stats.total_positions} flushes={stats.ws_flush_count}"
        )
