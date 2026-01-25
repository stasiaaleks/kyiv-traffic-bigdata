from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values


@functools.lru_cache(maxsize=1)
def load_environment() -> Mapping[str, str | None]:
    return dotenv_values()


def get_env(key: str, default: str) -> str:
    env = load_environment()
    return env.get(key) or default


def get_env_int(key: str, default: int) -> int:
    return int(get_env(key, str(default)))


def get_env_bool(key: str, default: bool) -> bool:
    return get_env(key, str(default).lower()).lower() == "true"


def get_env_path(key: str, default: str) -> Path:
    return Path(get_env(key, default))


@dataclass(frozen=True, slots=True)
class KyivCoordinateBounds:
    lat_min: float = 50.2
    lat_max: float = 50.7
    lon_min: float = 30.2
    lon_max: float = 31.0

    def contains(self, lat: float, lon: float) -> bool:
        return (
            self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max
        )


@dataclass(frozen=True, slots=True)
class WebSocketConfig:
    base_url: str = field(
        default_factory=lambda: get_env("KPT_WS_BASE_URL", "https://online.kpt.kyiv.ua")
    )
    flush_interval: int = field(
        default_factory=lambda: get_env_int("KPT_WS_FLUSH_INTERVAL", 5)
    )
    ping_interval: int = 25
    reconnect_delay: int = 5
    enabled: bool = field(
        default_factory=lambda: get_env_bool("KPT_ENABLE_WEBSOCKET", True)
    )


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    user_data_dir: Path = field(
        default_factory=lambda: get_env_path("KPT_USER_DATA_DIR", "/tmp/patchright_kpt")
    )
    headless: bool = field(default_factory=lambda: get_env_bool("KPT_HEADLESS", False))
    cloudflare_timeout: int = 120
    page_load_timeout: int = 60000
    turnstile_max_attempts: int = 3


@dataclass(frozen=True, slots=True)
class APIConfig:
    kpt_url: str = field(
        default_factory=lambda: get_env("KPT_URL", "https://kpt.kyiv.ua/online")
    )
    routes_url: str = field(
        default_factory=lambda: get_env(
            "KPT_API_ROUTES_URL", "https://online.kpt.kyiv.ua/api/route/list"
        )
    )
    poll_interval: int = field(
        default_factory=lambda: get_env_int("KPT_POLL_INTERVAL", 30)
    )
    max_consecutive_failures: int = 3


@dataclass(frozen=True, slots=True)
class OutputConfig:
    output_dir: Path = field(
        default_factory=lambda: get_env_path("KPT_OUTPUT_DIR", "/app/data")
    )
    routes_file_prefix: str = "kpt_routes"
    positions_file_prefix: str = "kpt_positions"
    file_extension: str = ".jsonl"


@dataclass(slots=True)
class PollerConfig:
    api: APIConfig = field(default_factory=APIConfig)
    websocket: WebSocketConfig = field(default_factory=WebSocketConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    bounds: KyivCoordinateBounds = field(default_factory=KyivCoordinateBounds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_url": self.api.routes_url,
            "kpt_url": self.api.kpt_url,
            "ws_url": self.websocket.base_url,
            "poll_interval": self.api.poll_interval,
            "ws_flush_interval": self.websocket.flush_interval,
            "ws_enabled": self.websocket.enabled,
            "output_dir": str(self.output.output_dir),
            "headless": self.browser.headless,
        }
