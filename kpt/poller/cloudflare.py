from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from interceptor import EXTRACT_POSITIONS_JS, GET_WS_STATS_JS, WS_INTERCEPTOR_JS
from patchright.sync_api import BrowserContext, Page, Playwright, sync_playwright

if TYPE_CHECKING:
    from config import BrowserConfig

logger = logging.getLogger(__name__)

CLOUDFLARE_INDICATORS = frozenset(
    {
        "moment",
        "checking",
        "cloudflare",
        "security check",
        "verify",
        "зачекайте",
        "трохи",
        "перевірка",
        "רק רגע",
    }
)

TURNSTILE_SELECTORS = (
    ".cf-turnstile",
    "[data-turnstile-widget]",
    "iframe[src*='challenges.cloudflare']",
    "iframe[src*='turnstile']",
    "#turnstile-wrapper",
    "input[type='checkbox']",
)

STEALTH_BROWSER_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-features=DnsOverHttps,DnsHttpssvc",
    "--dns-over-https-mode=off",
    "--no-sandbox",
)


def is_cloudflare_challenge(title: str) -> bool:
    title_lower = title.lower()
    return any(indicator in title_lower for indicator in CLOUDFLARE_INDICATORS)


def clean_browser_locks(user_data_dir: Path) -> None:
    """Remove stale Chrome profile lock files."""
    """_summary_
    """

    lock_files = ("SingletonLock", "SingletonCookie", "SingletonSocket")
    for lock_name in lock_files:
        lock_path = user_data_dir / lock_name
        if lock_path.exists():
            logger.info(f"Removing stale lock: {lock_name}")
            lock_path.unlink(missing_ok=True)


@dataclass
class TurnstileSolver:
    """Handles Cloudflare Turnstile CAPTCHA solving."""

    page: Page
    max_attempts: int = 3
    _attempt_count: int = field(default=0, init=False)

    def attempt_solve(self) -> bool:
        if self._attempt_count >= self.max_attempts:
            return False

        self._attempt_count += 1

        strategies = [
            self._try_checkbox_click,
            self._try_frame_click,
            self._try_container_click,
            self._try_position_click,
        ]

        for strategy in strategies:
            if strategy():
                time.sleep(2)
                return True

        return False

    def _try_checkbox_click(self) -> bool:
        try:
            checkbox = self.page.locator("input[type='checkbox']")
            if checkbox.count() > 0:
                logger.info("Found checkbox, clicking...")
                checkbox.first.click(timeout=5000)
                return True
        except Exception as e:
            logger.debug(f"Checkbox click failed: {e}")
        return False

    def _try_frame_click(self) -> bool:
        try:
            for frame in self.page.frames:
                if "challenges.cloudflare" in frame.url or "turnstile" in frame.url:
                    logger.info(f"Found Cloudflare frame: {frame.url}")
                    checkbox = frame.locator(
                        "input[type='checkbox'], .ctp-checkbox-label, #cf-stage"
                    )
                    if checkbox.count() > 0:
                        checkbox.first.click(timeout=5000)
                        return True
        except Exception as e:
            logger.debug(f"Frame click failed: {e}")
        return False

    def _try_container_click(self) -> bool:
        container_selectors = [
            ".cf-turnstile",
            "[data-turnstile-widget]",
            "div[style*='width: 300px']",
        ]
        try:
            for selector in container_selectors:
                elem = self.page.locator(selector)
                if elem.count() > 0:
                    box = elem.first.bounding_box()
                    if box:
                        # Click on left side where checkbox typically is
                        click_x = box["x"] + 30
                        click_y = box["y"] + box["height"] / 2
                        logger.info(
                            f"Clicking turnstile container at ({click_x:.0f}, {click_y:.0f})"
                        )
                        self.page.mouse.click(click_x, click_y)
                        return True
        except Exception as e:
            logger.debug(f"Container click failed: {e}")
        return False

    def _try_position_click(self) -> bool:
        try:
            logger.info("Clicking at typical turnstile checkbox position...")
            self.page.mouse.click(120, 290)
            return True
        except Exception as e:
            logger.debug(f"Position click failed: {e}")
        return False

    def reset(self) -> None:
        """Reset attempt counter."""
        self._attempt_count = 0


class BrowserManager:
    """
    Manages Patchright browser lifecycle.

    Provides context manager interface for clean resource management.
    """

    def __init__(self, config: BrowserConfig) -> None:
        self.config = config
        self._playwright: Playwright | None = None
        self._browser: BrowserContext | None = None
        self._page: Page | None = None

    def start(self) -> None:
        if self._browser:
            return

        logger.info("Starting Patchright browser (undetected Chrome)...")
        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
        clean_browser_locks(self.config.user_data_dir)

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.config.user_data_dir),
            headless=self.config.headless,
            no_viewport=True,
            args=list(STEALTH_BROWSER_ARGS),
        )
        self._page = self._browser.new_page()
        logger.info("Patchright browser started with persistent profile")

    def stop(self) -> None:
        self._page = None

        if self._browser:
            try:
                self._browser.close()
            except Exception as e:
                logger.debug(f"Browser close error: {e}")
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception as e:
                logger.debug(f"Playwright stop error: {e}")
            self._playwright = None

        logger.info("Browser closed")

    @property
    def page(self) -> Page | None:
        return self._page

    @property
    def context(self) -> BrowserContext | None:
        return self._browser

    def inject_script(self, script: str) -> bool:
        if not self._browser:
            return False
        try:
            self._browser.add_init_script(script)
            logger.info("Script injected into browser context")
            return True
        except Exception as e:
            logger.error(f"Failed to inject script: {e}")
            return False


class CloudflareBypasser:
    """
    Handles Cloudflare bypass using Patchright
    """

    def __init__(self, config: BrowserConfig, kpt_url: str) -> None:
        self.config = config
        self.kpt_url = kpt_url
        self._browser_manager = BrowserManager(config)

    def start_browser(self) -> None:
        self._browser_manager.start()

    def stop_browser(self) -> None:
        self._browser_manager.stop()

    @property
    def page(self) -> Page | None:
        return self._browser_manager.page

    def bypass(self) -> bool:
        if not self._browser_manager.context:
            self.start_browser()

        page = self._browser_manager.page
        if not page:
            return False

        logger.info(f"Loading {self.kpt_url}")
        page.goto(
            self.kpt_url,
            wait_until="domcontentloaded",
            timeout=self.config.page_load_timeout,
        )

        logger.info("Waiting for Cloudflare JS challenge...")
        time.sleep(5)

        solver = TurnstileSolver(page, self.config.turnstile_max_attempts)
        return self._wait_for_bypass(page, solver)

    def _wait_for_bypass(self, page: Page, solver: TurnstileSolver) -> bool:
        start = time.time()

        while time.time() - start < self.config.cloudflare_timeout:
            title = page.title()
            url = page.url

            logger.info(f"Page title: '{title}', URL: {url}")

            if not is_cloudflare_challenge(title):
                logger.info(f"Cloudflare bypassed - page: {title}")
                return True

            self._log_turnstile_elements(page)

            if solver.attempt_solve():
                time.sleep(3)
                continue

            elapsed = int(time.time() - start)
            remaining = self.config.cloudflare_timeout - elapsed
            logger.info(f"[{elapsed}s] Waiting for Cloudflare... ({remaining}s left)")
            time.sleep(2)

        logger.error("Cloudflare bypass timeout")
        return False

    def _log_turnstile_elements(self, page: Page) -> None:
        turnstile_count = 0
        for selector in TURNSTILE_SELECTORS:
            count = page.locator(selector).count()
            if count > 0:
                logger.info(f"Found {count} elements with selector: {selector}")
                turnstile_count += count

        iframe_count = page.locator("iframe").count()
        logger.info(
            f"Total turnstile elements: {turnstile_count}, iframes: {iframe_count}"
        )

    def extract_cookies(self) -> dict[str, str]:
        page = self._browser_manager.page
        if not page:
            return {}

        cookies: dict[str, str] = {}
        for cookie in page.context.cookies():
            name = cookie.get("name")
            value = cookie.get("value")
            if name is not None and value is not None:
                cookies[name] = value

        cf_cookies = {k: v for k, v in cookies.items() if "cf" in k.lower()}
        logger.info(
            f"Extracted {len(cf_cookies)} Cloudflare cookies, {len(cookies)} total"
        )
        return cookies

    def get_user_agent(self) -> str:
        page = self._browser_manager.page
        if not page:
            return ""
        return page.evaluate("navigator.userAgent")

    def inject_ws_interceptor(self) -> bool:
        return self._browser_manager.inject_script(WS_INTERCEPTOR_JS)

    def navigate_and_wait_for_ws(self, timeout: int = 30) -> bool:
        page = self._browser_manager.page
        if not page:
            return False

        try:
            logger.info(f"Navigating to {self.kpt_url} for WebSocket capture...")
            page.goto(
                self.kpt_url,
                wait_until="domcontentloaded",
                timeout=self.config.page_load_timeout,
            )

            return self._wait_for_ws_data(page, timeout)

        except Exception as e:
            logger.error(f"Navigation error: {e}")
            return False

    def _wait_for_ws_data(self, page: Page, timeout: int) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            try:
                data = page.evaluate("window.kptData")
                if data and data.get("connectionCount", 0) > 0:
                    logger.info(
                        f"WebSocket connected (connections: {data['connectionCount']})"
                    )
                    if data.get("positions") or data.get("rawMessages"):
                        logger.info(
                            f"Initial WS data: {len(data.get('positions', []))} positions, "
                            f"{len(data.get('rawMessages', []))} raw messages"
                        )
                        return True
            except Exception:
                pass
            time.sleep(1)

        logger.warning("Timeout waiting for WebSocket data")
        return False

    def extract_ws_positions(self) -> list[dict[str, Any]]:
        page = self._browser_manager.page
        if not page:
            return []

        try:
            result = page.evaluate(EXTRACT_POSITIONS_JS)
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.debug(f"Failed to extract WS positions: {e}")
            return []

    def get_ws_stats(self) -> dict[str, Any]:
        page = self._browser_manager.page
        if not page:
            return {}

        try:
            return page.evaluate(GET_WS_STATS_JS)
        except Exception:
            return {}


@contextmanager
def cloudflare_bypasser_context(
    config: BrowserConfig, kpt_url: str
) -> Iterator[CloudflareBypasser]:
    bypasser = CloudflareBypasser(config, kpt_url)
    try:
        yield bypasser
    finally:
        bypasser.stop_browser()
