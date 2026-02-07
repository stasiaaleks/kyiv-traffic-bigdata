from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp_socks import ProxyConnector

if TYPE_CHECKING:
    from .config import ProxyConfig

logger = logging.getLogger(__name__)


class HTTPError(Exception):
    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class CookiesExpiredError(HTTPError):
    def __init__(self) -> None:
        super().__init__(403, "Cookies expired")


class AsyncHTTPSession:
    def __init__(self, proxy_config: ProxyConfig) -> None:
        self._proxy_config = proxy_config
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    def _create_connector(self) -> aiohttp.BaseConnector:
        if self._proxy_config.socks_proxy:
            return ProxyConnector.from_url(self._proxy_config.socks_proxy)
        return aiohttp.TCPConnector()

    def _get_proxy_url(self) -> str | None:
        if self._proxy_config.http_proxy and not self._proxy_config.socks_proxy:
            return self._proxy_config.http_proxy
        return None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = self._create_connector()
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def get_json(self, url: str, timeout: int = 30) -> dict | list | None:
        session = await self._ensure_session()
        proxy = self._get_proxy_url()

        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout), proxy=proxy
            ) as response:
                if response.status == 403:
                    raise CookiesExpiredError()

                if response.status in (502, 503, 504):
                    logger.warning(f"Server error HTTP {response.status} from {url}")
                    return None

                if response.status != 200:
                    logger.warning(f"HTTP {response.status} from {url}")
                    return None

                return await response.json()

        except CookiesExpiredError:
            raise
        except aiohttp.ContentTypeError as e:
            logger.warning(f"Invalid JSON response from {url}: {e}")
            return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Connection error for {url}: {e}")
            return None

    async def get_text(self, url: str, timeout: int = 30) -> str | None:
        session = await self._ensure_session()
        proxy = self._get_proxy_url()

        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout), proxy=proxy
            ) as response:
                if response.status == 403:
                    raise CookiesExpiredError()

                if response.status != 200:
                    logger.warning(f"HTTP {response.status} from {url}")
                    return None

                return await response.text()

        except CookiesExpiredError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Connection error for {url}: {e}")
            return None

    async def refresh_session(self) -> None:
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None
            logger.info("Session refreshed")

    async def __aenter__(self) -> AsyncHTTPSession:
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
