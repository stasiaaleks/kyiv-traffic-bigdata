from __future__ import annotations

import logging
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from queue import Empty, Queue
from typing import TYPE_CHECKING, Iterator

from config import PollerConfig
from models import PollerStats, RouteRecord, VehiclePosition
from session import CookieSession
from websocket_client import WebSocketClient
from writer import StreamWriter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def drain_queue(queue: Queue[VehiclePosition]) -> list[VehiclePosition]:
    positions: list[VehiclePosition] = []
    while True:
        try:
            pos = queue.get_nowait()
            positions.append(pos)
        except Empty:
            break
    return positions


@dataclass
class TimingController:
    poll_interval: float
    flush_interval: float
    _last_poll: float = field(default=0.0, init=False)
    _last_flush: float = field(default=0.0, init=False)

    def should_poll(self) -> bool:
        return time.time() - self._last_poll >= self.poll_interval

    def should_flush(self) -> bool:
        return time.time() - self._last_flush >= self.flush_interval

    def mark_polled(self) -> None:
        self._last_poll = time.time()

    def mark_flushed(self) -> None:
        self._last_flush = time.time()


class FailureTracker:
    def __init__(self, max_failures: int = 3) -> None:
        self.max_failures = max_failures
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def should_refresh(self) -> bool:
        return self._consecutive_failures >= self.max_failures

    def reset(self) -> None:
        self._consecutive_failures = 0


class KPTPoller:
    """
    - Cookie session management
    - WebSocket client for real-time positions
    - REST API polling for routes
    - Data persistence
    """

    def __init__(self, config: PollerConfig) -> None:
        self.config = config
        self._position_queue: Queue[VehiclePosition] = Queue()
        self._stats = PollerStats()
        self._running = False

    def run(self) -> int:
        self._log_config()
        self._running = True

        with ExitStack() as stack:
            session = stack.enter_context(_session_context(self))
            writer = stack.enter_context(_writer_context(self))

            if not session.refresh_cookies():
                logger.error("Initial cookie acquisition failed")
                return 1

            ws_client: WebSocketClient | None = None
            if self.config.websocket.enabled:
                ws_client = self._create_ws_client(session)
                ws_client.start()
                stack.callback(ws_client.stop)
                time.sleep(3)  # Give WebSocket time to connect

            return self._run_loop(session, writer, ws_client)

    def _log_config(self) -> None:
        logger.info("KPT Poller (REST API + WebSocket)")
        for key, value in self.config.to_dict().items():
            logger.info(f"  {key}: {value}")

    def _create_session(self) -> CookieSession:
        return CookieSession(self.config.browser, self.config.api.kpt_url)

    def _create_ws_client(self, session: CookieSession) -> WebSocketClient:
        return WebSocketClient(
            base_url=self.config.websocket.base_url,
            cookies=session.cookies,
            user_agent=session.user_agent,
            position_queue=self._position_queue,
            bounds=self.config.bounds,
            ping_interval=self.config.websocket.ping_interval,
            reconnect_delay=self.config.websocket.reconnect_delay,
        )

    def _create_writer(self) -> StreamWriter:
        return StreamWriter(self.config.output)

    def _flush_positions(self, writer: StreamWriter) -> int:
        positions = drain_queue(self._position_queue)
        if not positions:
            return 0

        self._stats.record_position_flush(len(positions))
        writer.write_positions([p.to_dict() for p in positions])
        logger.info(
            f"[WS #{self._stats.ws_flush_count}] Flushed {len(positions)} positions "
            f"(total: {self._stats.total_positions})"
        )
        return len(positions)

    def _poll_routes(
        self,
        session: CookieSession,
        writer: StreamWriter,
        failure_tracker: FailureTracker,
    ) -> bool:
        self._stats.poll_count += 1
        timestamp = datetime.now().isoformat()

        routes = session.fetch(self.config.api.routes_url)

        if routes is None:
            failure_tracker.record_failure()
            logger.warning(
                f"[Poll #{self._stats.poll_count}] Failed "
                f"({failure_tracker._consecutive_failures}/{failure_tracker.max_failures})"
            )
            return False

        failure_tracker.record_success()

        record = RouteRecord(
            timestamp=timestamp,
            poll_number=self._stats.poll_count,
            routes=routes if isinstance(routes, list) else [routes],
        )

        writer.write_route_record(record)
        logger.info(
            f"[Poll #{self._stats.poll_count}] Fetched {record.route_count} routes"
        )
        return True

    def _handle_refresh(
        self,
        session: CookieSession,
        ws_client: WebSocketClient | None,
        failure_tracker: FailureTracker,
    ) -> bool:
        logger.info("Too many failures, refreshing cookies...")

        if not session.refresh_cookies():
            logger.error("Cookie refresh failed")
            time.sleep(60)
            return False

        if ws_client:
            ws_client.update_cookies(session.cookies, session.user_agent)
            logger.info("WebSocket client cookies updated")

        failure_tracker.reset()
        return True

    def _run_loop(
        self,
        session: CookieSession,
        writer: StreamWriter,
        ws_client: WebSocketClient | None,
    ) -> int:
        timing = TimingController(
            poll_interval=self.config.api.poll_interval,
            flush_interval=self.config.websocket.flush_interval,
        )
        failure_tracker = FailureTracker(self.config.api.max_consecutive_failures)

        logger.info("Starting polling loop...")

        try:
            while self._running:
                if self.config.websocket.enabled and timing.should_flush():
                    self._flush_positions(writer)
                    self._log_ws_stats(ws_client)
                    timing.mark_flushed()

                if timing.should_poll():
                    if not self._poll_routes(session, writer, failure_tracker):
                        if failure_tracker.should_refresh():
                            self._handle_refresh(session, ws_client, failure_tracker)
                    timing.mark_polled()

                time.sleep(0.5)

        except KeyboardInterrupt:
            logger.info("Stopped by user")
            logger.info(
                f"Final stats: {self._stats.poll_count} route polls, "
                f"{self._stats.total_positions} positions"
            )
            return 0

        except Exception as e:
            logger.exception(f"Poller error: {e}")
            return 1

        return 0

    def _log_ws_stats(self, ws_client: WebSocketClient | None) -> None:
        if ws_client and self._stats.ws_flush_count % 10 == 0:
            stats = ws_client.stats
            logger.debug(f"WS stats: {stats}")

    def stop(self) -> None:
        self._running = False


@contextmanager
def _session_context(poller: KPTPoller) -> Iterator[CookieSession]:
    session = poller._create_session()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def _writer_context(poller: KPTPoller) -> Iterator[StreamWriter]:
    writer = poller._create_writer()
    try:
        yield writer
    finally:
        writer.close()


def run_poller(config: PollerConfig) -> int:
    poller = KPTPoller(config)
    return poller.run()
