from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

import requests
from cloudflare import CloudflareBypasser

if TYPE_CHECKING:
    from config import BrowserConfig

logger = logging.getLogger(__name__)


class HTTPError(Exception):
    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class CookiesExpiredError(HTTPError):
    def __init__(self) -> None:
        super().__init__(403, "Cookies expired")


@dataclass
class SessionCredentials:
    cookies: dict[str, str] = field(default_factory=dict)
    user_agent: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.cookies and self.user_agent)


class RequestsSessionWrapper:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._credentials = SessionCredentials()

    def update_credentials(self, credentials: SessionCredentials) -> None:
        self._credentials = credentials
        self._session.cookies.clear()

        for name, value in credentials.cookies.items():
            self._session.cookies.set(name, value)

        self._session.headers.update({"User-Agent": credentials.user_agent})

    def get_json(self, url: str, timeout: int = 30) -> list[dict[str, Any]] | None:
        try:
            response = self._session.get(url, timeout=timeout)

            if response.status_code == 403:
                logger.warning("Got 403 - cookies expired, need refresh")
                raise CookiesExpiredError()

            if response.status_code != 200:
                logger.warning(f"HTTP {response.status_code} from {url}")
                return None

            return response.json()

        except requests.exceptions.JSONDecodeError as e:
            logger.warning(f"Invalid JSON: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {e}")
            return None

    @property
    def cookies(self) -> dict[str, str]:
        return self._credentials.cookies

    @property
    def user_agent(self) -> str:
        return self._credentials.user_agent


class CookieSession:
    def __init__(self, browser_config: BrowserConfig, kpt_url: str) -> None:
        self._browser_config = browser_config
        self._kpt_url = kpt_url
        self._session = RequestsSessionWrapper()
        self._bypasser: CloudflareBypasser | None = None

    def _get_bypasser(self) -> CloudflareBypasser:
        if self._bypasser is None:
            self._bypasser = CloudflareBypasser(self._browser_config, self._kpt_url)
        return self._bypasser

    def refresh_cookies(self) -> bool:
        logger.info("Refreshing Cloudflare cookies...")
        bypasser = self._get_bypasser()

        try:
            bypasser.start_browser()

            if not bypasser.bypass():
                return False

            credentials = SessionCredentials(
                cookies=bypasser.extract_cookies(),
                user_agent=bypasser.get_user_agent(),
            )

            self._session.update_credentials(credentials)
            logger.info("Cookie refresh complete")
            return True

        finally:
            bypasser.stop_browser()

    def fetch(self, url: str) -> list[dict[str, Any]] | None:
        try:
            return self._session.get_json(url)
        except CookiesExpiredError:
            return None

    @property
    def cookies(self) -> dict[str, str]:
        return self._session.cookies

    @property
    def user_agent(self) -> str:
        return self._session.user_agent

    def close(self) -> None:
        if self._bypasser:
            self._bypasser.stop_browser()
            self._bypasser = None


@contextmanager
def cookie_session_context(
    browser_config: BrowserConfig, kpt_url: str
) -> Iterator[CookieSession]:
    session = CookieSession(browser_config, kpt_url)
    try:
        yield session
    finally:
        session.close()
