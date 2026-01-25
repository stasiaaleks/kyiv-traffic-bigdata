from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from queue import Queue
from typing import TYPE_CHECKING, Any, Iterator

import requests
import websocket
from models import VehiclePosition, WebSocketStats
from parsers import MessageParser, parse_handshake_response

if TYPE_CHECKING:
    from config import KyivCoordinateBounds

logger = logging.getLogger(__name__)


@dataclass
class ConnectionContext:
    session_id: str
    ping_interval: int
    ws_url: str


class HTTPSessionManager:
    def __init__(self, cookies: dict[str, str], user_agent: str) -> None:
        self._session = requests.Session()
        self._update_session(cookies, user_agent)

    def _update_session(self, cookies: dict[str, str], user_agent: str) -> None:
        self._session.cookies.clear()
        for name, value in cookies.items():
            self._session.cookies.set(name, value)
        self._session.headers["User-Agent"] = user_agent

    def update_credentials(self, cookies: dict[str, str], user_agent: str) -> None:
        self._update_session(cookies, user_agent)

    def perform_handshake(self, base_url: str) -> ConnectionContext | None:
        handshake_url = f"{base_url}/socket.io/?EIO=3&transport=polling"

        try:
            logger.info(f"Socket.IO handshake: {handshake_url}")
            response = self._session.get(handshake_url, timeout=30)

            if response.status_code == 403:
                logger.warning("Handshake got 403 - cookies expired")
                return None

            if response.status_code != 200:
                logger.error(f"Handshake failed: HTTP {response.status_code}")
                return None

            session_id, ping_interval = parse_handshake_response(response.text)
            if not session_id:
                logger.error(f"Could not parse session ID from: {response.text[:100]}")
                return None

            ping_seconds = (ping_interval or 25000) // 1000
            logger.info(f"Got session ID: {session_id[:20]}... (ping: {ping_seconds}s)")

            ws_host = base_url.replace("https://", "").replace("http://", "")
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


@dataclass
class MessageHandler:
    position_queue: Queue[VehiclePosition]
    parser: MessageParser
    stats: WebSocketStats = field(default_factory=WebSocketStats)
    _upgrade_complete: bool = field(default=False, init=False)

    def handle_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        self.stats.message_count += 1

        if not message:
            return

        if self._handle_protocol_message(ws, message):
            return

        if self.stats.message_count <= 10:
            logger.info(f"WS message #{self.stats.message_count}: {message[:100]}")

        positions = self.parser.parse(message)
        for pos in positions:
            self.position_queue.put(pos)
            self.stats.position_count += 1

        if self.stats.message_count % 100 == 0:
            logger.debug(
                f"WS messages: {self.stats.message_count}, "
                f"positions: {self.stats.position_count}"
            )

    def _handle_protocol_message(
        self, ws: websocket.WebSocketApp, message: str
    ) -> bool:
        if message == "3probe":
            logger.info("Received probe response, completing upgrade...")
            ws.send("5")  # Upgrade packet
            ws.send("40")  # Connect to default namespace
            self._upgrade_complete = True
            self.stats.connected = True
            logger.info("WebSocket upgrade complete")
            return True

        packet_type = message[0] if message else ""

        if packet_type == "3":  # PONG
            logger.debug("Received PONG")
            return True

        if packet_type == "2":  # PING - respond with PONG
            ws.send("3")
            logger.debug("Sent PONG")
            return True

        return False

    @property
    def is_connected(self) -> bool:
        return self._upgrade_complete and self.stats.connected


class PingManager:
    def __init__(self, ping_interval: int = 25) -> None:
        self.ping_interval = ping_interval
        self._last_ping: float = 0.0

    def should_ping(self) -> bool:
        return time.time() - self._last_ping > self.ping_interval

    def send_ping(self, ws: websocket.WebSocketApp) -> bool:
        try:
            ws.send("2")
            self._last_ping = time.time()
            logger.debug("Sent PING")
            return True
        except Exception as e:
            logger.debug(f"Ping failed: {e}")
            return False


def build_cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


class WebSocketClient:
    def __init__(
        self,
        base_url: str,
        cookies: dict[str, str],
        user_agent: str,
        position_queue: Queue[VehiclePosition],
        bounds: KyivCoordinateBounds | None = None,
        ping_interval: int = 25,
        reconnect_delay: int = 5,
    ) -> None:
        self.base_url = base_url
        self._cookies = cookies
        self._user_agent = user_agent
        self.position_queue = position_queue
        self.ping_interval = ping_interval
        self.reconnect_delay = reconnect_delay

        self._http_session = HTTPSessionManager(cookies, user_agent)
        self._parser = MessageParser(bounds)
        self._handler = MessageHandler(position_queue, self._parser)
        self._ping_manager = PingManager(ping_interval)

        self._ws: websocket.WebSocketApp | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("WebSocket client started")

    def stop(self) -> None:
        self._running = False
        if self._ws:
            self._ws.close()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info(
            f"WebSocket client stopped (total positions: {self._handler.stats.position_count})"
        )

    def update_cookies(self, cookies: dict[str, str], user_agent: str) -> None:
        self._cookies = cookies
        self._user_agent = user_agent
        self._http_session.update_credentials(cookies, user_agent)

    @property
    def is_connected(self) -> bool:
        return self._handler.is_connected

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "connected": self._handler.stats.connected,
            "message_count": self._handler.stats.message_count,
            "position_count": self._handler.stats.position_count,
            "error_count": self._handler.stats.error_count,
        }

    def _create_websocket(self, context: ConnectionContext) -> websocket.WebSocketApp:
        return websocket.WebSocketApp(
            context.ws_url,
            on_message=self._handler.handle_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open,
            header={
                "Cookie": build_cookie_header(self._cookies),
                "User-Agent": self._user_agent,
                "Origin": self.base_url,
            },
        )

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.error(f"WebSocket error: {error}")
        self._handler.stats.error_count += 1

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        close_status_code: int | None,
        close_msg: str | None,
    ) -> None:
        logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
        self._handler.stats.connected = False

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("WebSocket connected, sending upgrade probe...")
        ws.send("2probe")

    def _connect(self) -> bool:
        context = self._http_session.perform_handshake(self.base_url)
        if not context:
            return False

        self._ping_manager.ping_interval = context.ping_interval
        logger.info(f"Connecting WebSocket: {context.ws_url[:80]}...")

        self._ws = self._create_websocket(context)
        return True

    def _run_loop(self) -> None:
        while self._running:
            try:
                if not self._connect():
                    logger.warning(
                        f"WS connection failed, retrying in {self.reconnect_delay}s"
                    )
                    time.sleep(self.reconnect_delay)
                    continue

                self._run_websocket_with_heartbeat()

                if not self._running:
                    break

                logger.info(
                    f"WS connection lost, reconnecting in {self.reconnect_delay}s"
                )
                time.sleep(self.reconnect_delay)

            except Exception as e:
                logger.error(f"WS client error: {e}")
                time.sleep(self.reconnect_delay)

    def _run_websocket_with_heartbeat(self) -> None:
        assert self._ws is not None

        ws_thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={"ping_interval": 0},
            daemon=True,
        )
        ws_thread.start()

        while self._running and ws_thread.is_alive():
            if self._handler.is_connected and self._ping_manager.should_ping():
                self._ping_manager.send_ping(self._ws)
            time.sleep(1)


@contextmanager
def websocket_client_context(
    base_url: str,
    cookies: dict[str, str],
    user_agent: str,
    position_queue: Queue[VehiclePosition],
    bounds: KyivCoordinateBounds | None = None,
) -> Iterator[WebSocketClient]:
    client = WebSocketClient(
        base_url=base_url,
        cookies=cookies,
        user_agent=user_agent,
        position_queue=position_queue,
        bounds=bounds,
    )
    client.start()
    try:
        yield client
    finally:
        client.stop()
