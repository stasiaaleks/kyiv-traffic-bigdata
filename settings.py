"""Centralized settings for Kyiv traffic data ingestion project."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent


# API keys (from environment)
EWAY_API_KEY: str | None = os.getenv("EWAY_API_KEY")
WEATHER_API_KEY: str | None = os.getenv("WEATHER_API_KEY")
GOOGLE_ROUTES_API_KEY: str | None = os.getenv("GOOGLE_ROUTES_API_KEY")


# API URLs
OPEN_METEO_API_URL = "https://archive-api.open-meteo.com/v1/archive"
OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
GOOGLE_ROUTES_API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
EWAY_WEBSOCKET_URL = "wss://ws.easyway.info/sub/gps/c380645ad829453d38e3976ccf95eabd"

KPT_URL = os.getenv("KPT_URL", "https://kpt.kyiv.ua/online")
KPT_API_ROUTES_URL = os.getenv(
    "KPT_API_ROUTES_URL", "https://online.kpt.kyiv.ua/api/route/list"
)


# Geo Coordinates


@dataclass(frozen=True)
class Coordinates:
    lat: float
    lon: float


@dataclass(frozen=True)
class BoundingBox:
    south: float
    north: float
    west: float
    east: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.south, self.north, self.west, self.east)


KYIV_CENTER = Coordinates(lat=50.4501, lon=30.5234)
KYIV_BOUNDS = BoundingBox(south=50.3, north=50.6, west=30.2, east=30.8)
UKRAINE_BOUNDS = BoundingBox(south=44.0, north=52.0, west=22.0, east=40.0)


# HTTP request timeouts (seconds)
DEFAULT_REQUEST_TIMEOUT = 30
OSM_REQUEST_TIMEOUT = 120
OSM_QUERY_TIMEOUT = 90

# Rate limiting delays (seconds)
OSM_REQUEST_DELAY = 1.5
GOOGLE_ROUTES_REQUEST_DELAY = 0.1

# WS settings
WS_MESSAGE_TIMEOUT = 10.0
EWAY_LISTEN_DURATION_MINUTES = 3

# KPT polling
KPT_POLL_INTERVAL = int(os.getenv("KPT_POLL_INTERVAL", "30"))
KPT_HEADLESS = os.getenv("KPT_HEADLESS", "true").lower() == "true"


# OpenStreetMap filters

OSM_HIGHWAY_TYPES = [
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "residential",
]

OSM_LANDUSE_TYPES = [
    "residential",
    "industrial",
    "commercial",
    "retail",
]
