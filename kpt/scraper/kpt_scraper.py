from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

logger = logging.getLogger(__name__)

_env = dotenv_values()
WS_INTERCEPTOR_JS_FILE = "ws_interceptor.js"


@dataclass
class ScraperConfig:
    kpt_url: str = field(
        default_factory=lambda: _env.get("KPT_URL") or "https://kpt.kyiv.ua/online"
    )
    api_url: str = field(
        default_factory=lambda: _env.get("KPT_API_URL")
        or "https://online.kpt.kyiv.ua/api/route/list"
    )
    selenium_url: str = field(
        default_factory=lambda: _env.get("SELENIUM_URL") or "http://chrome:4444/wd/hub"
    )
    listen_duration: int = field(
        default_factory=lambda: int(_env.get("KPT_LISTEN_DURATION") or "0")
    )
    interactive: bool = field(
        default_factory=lambda: (_env.get("KPT_INTERACTIVE") or "false").lower()
        == "true"
    )
    output_dir: Path = field(
        default_factory=lambda: Path(_env.get("KPT_OUTPUT_DIR") or "/app/data")
    )
    cloudflare_timeout: int = 300
    poll_interval: int = 2

    @property
    def continuous(self) -> bool:
        return self.listen_duration == 0


@dataclass
class CollectedData:
    messages: list[dict[str, Any]] = field(default_factory=list)
    vehicles: list[dict[str, Any]] = field(default_factory=list)
    routes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def vehicle_count(self) -> int:
        return len(self.vehicles)

    @property
    def message_count(self) -> int:
        return len(self.messages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": self.messages,
            "vehicles": self.vehicles,
            "routes": self.routes,
            "errors": self.errors,
        }


def load_interceptor_script() -> str:
    script_dir = Path(__file__).parent
    js_file = script_dir / WS_INTERCEPTOR_JS_FILE

    if js_file.exists():
        return js_file.read_text(encoding="utf-8")

    raise FileNotFoundError(f"WebSocket interceptor not found: {js_file}")


def create_chrome_options() -> Options:
    options = Options()

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")

    return options


def create_remote_driver(config: ScraperConfig) -> WebDriver:
    options = create_chrome_options()

    logger.info(f"Connecting to Selenium at {config.selenium_url}")
    driver = webdriver.Remote(command_executor=config.selenium_url, options=options)

    _apply_stealth_settings(driver)

    return driver


def _apply_stealth_settings(driver: WebDriver) -> None:
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """
        },
    )

    interceptor_script = load_interceptor_script()
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": interceptor_script},
    )
    logger.info("WebSocket interceptor registered for page load")


def is_cloudflare_challenge(title: str) -> bool:
    title_lower = title.lower()
    challenge_indicators = ["moment", "checking", "cloudflare", "security check"]
    return any(indicator in title_lower for indicator in challenge_indicators)


def wait_for_cloudflare_bypass(driver: WebDriver, config: ScraperConfig) -> bool:
    logger.info("Checking for Cloudflare robot challenge")

    start_time = time.time()
    check_interval = 5

    while time.time() - start_time < config.cloudflare_timeout:
        title = driver.title

        if not is_cloudflare_challenge(title):
            logger.info(f"Page loaded: {title}")
            return True

        elapsed = int(time.time() - start_time)
        remaining = config.cloudflare_timeout - elapsed

        if config.interactive:
            logger.info(
                f"[{elapsed}s] Cloudflare active. "
                f"Running at http://localhost:7900... ({remaining}s remaining)"
            )
        else:
            logger.info(
                f"[{elapsed}s] Waiting for Cloudflare... ({remaining}s remaining)"
            )

        time.sleep(check_interval)

    return False


def read_collected_data(driver: WebDriver) -> CollectedData:
    raw_data = driver.execute_script("return window.kptData || {}")

    return CollectedData(
        messages=raw_data.get("messages", []),
        vehicles=raw_data.get("vehicles", []),
        routes=raw_data.get("routes", []),
        errors=raw_data.get("errors", []),
    )


def stream_websocket_data(driver: WebDriver, config: ScraperConfig) -> None:
    logger.info("Streaming data")

    last_vehicles: list[dict[str, Any]] = []

    while True:
        try:
            data = read_collected_data(driver)

            if data.vehicles != last_vehicles and data.vehicles:
                output = {
                    "timestamp": datetime.now().isoformat(),
                    "vehicle_count": data.vehicle_count,
                    "vehicles": data.vehicles,
                }
                print(json.dumps(output, ensure_ascii=False), flush=True)
                last_vehicles = data.vehicles.copy()

            time.sleep(config.poll_interval)

        except Exception as e:
            logger.error(f"Error reading data: {e}")
            time.sleep(config.poll_interval * 2)


def collect_websocket_data(driver: WebDriver, config: ScraperConfig) -> CollectedData:
    logger.info(f"Collecting data for {config.listen_duration} seconds")

    start_time = time.time()
    last_message_count = 0
    last_vehicle_count = 0

    while time.time() - start_time < config.listen_duration:
        try:
            data = read_collected_data(driver)

            if (
                data.message_count != last_message_count
                or data.vehicle_count != last_vehicle_count
            ):
                elapsed = int(time.time() - start_time)
                logger.info(
                    f"[{elapsed}s] Messages: {data.message_count}, "
                    f"Vehicles: {data.vehicle_count}"
                )
                last_message_count = data.message_count
                last_vehicle_count = data.vehicle_count

            time.sleep(config.poll_interval)

        except Exception as e:
            logger.error(f"Error reading data: {e}")
            time.sleep(config.poll_interval * 2)

    return read_collected_data(driver)


def fetch_routes_from_api(driver: WebDriver, config: ScraperConfig) -> list | None:
    body = ""
    try:
        driver.execute_script(f"window.open('{config.api_url}', '_blank');")
        time.sleep(3)

        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(2)

        body = driver.find_element(By.TAG_NAME, "body").text

        driver.close()
        driver.switch_to.window(driver.window_handles[0])

        return json.loads(body)

    except json.JSONDecodeError:
        logger.warning(f"API response is not valid JSON: {body[:200]}")
        return None
    except Exception as e:
        logger.error(f"API fetch error: {e}")
        return None


def build_output_document(
    data: CollectedData, routes: list | None, config: ScraperConfig
) -> dict[str, Any]:
    return {
        "collected_at": datetime.now().isoformat(),
        "listen_duration_seconds": config.listen_duration,
        "statistics": {
            "total_messages": data.message_count,
            "vehicle_positions": data.vehicle_count,
            "routes": len(routes) if routes else len(data.routes),
            "errors": len(data.errors),
        },
        "messages": data.messages,
        "vehicles": data.vehicles,
        "routes": routes or data.routes,
        "errors": data.errors,
    }


def save_output(
    data: CollectedData, routes: list | None, config: ScraperConfig
) -> Path:
    config.output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = config.output_dir / f"kpt_data_{timestamp}.json"

    document = build_output_document(data, routes, config)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, ensure_ascii=False)

    logger.info(f"Data saved to: {output_file}")
    logger.info(f"Messages: {document['statistics']['total_messages']}")
    logger.info(f"Vehicles: {document['statistics']['vehicle_positions']}")
    logger.info(f"Routes: {document['statistics']['routes']}")

    return output_file


def log_startup(config: ScraperConfig) -> None:
    logger.info("=" * 60)
    logger.info("KPT Kyiv Public Transport Data Collector")
    logger.info("=" * 60)
    logger.info(f"URL: {config.kpt_url}")
    logger.info(f"Selenium: {config.selenium_url}")
    logger.info(
        f"Mode: {'continuous streaming' if config.continuous else f'{config.listen_duration}s collection'}"
    )
    logger.info(f"Interactive mode: {config.interactive}")

    if config.interactive:
        logger.info(
            "If Cloudflare challenge appears, solve it via: http://localhost:7900"
        )


def run_scraper(config: ScraperConfig) -> int:
    log_startup(config)

    driver = None

    try:
        driver = create_remote_driver(config)
        logger.info("Browser connected")

        logger.info(f"Navigating to {config.kpt_url}")
        driver.get(config.kpt_url)

        if not wait_for_cloudflare_bypass(driver, config):
            logger.error("Cloudflare challenge not solved within timeout")
            return 1

        logger.info("Refreshing page to capture WebSocket with interceptor")
        driver.refresh()
        time.sleep(5)

        wait_for_cloudflare_bypass(
            driver, ScraperConfig(cloudflare_timeout=60, interactive=config.interactive)
        )

        if config.continuous:
            stream_websocket_data(driver, config)
            return 0

        data = collect_websocket_data(driver, config)

        logger.info("Fetching routes from API")
        routes = fetch_routes_from_api(driver, config)

        save_output(data, routes, config)

        logger.info("Collection complete")
        return 0

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as e:
        logger.exception(f"Scraper error: {e}")
        return 1
    finally:
        if driver:
            driver.quit()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    configure_logging()
    config = ScraperConfig()
    exit_code = run_scraper(config)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
