import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from settings import DEFAULT_REQUEST_TIMEOUT, KYIV_CENTER, OPEN_METEO_API_URL


@dataclass
class WeatherResponse:
    latitude: float
    longitude: float
    elevation: float
    timezone: str
    hourly_units: dict[str, str]
    hourly_data: dict[str, list[Any]]


def fetch_historical_weather(start_date: date, end_date: date) -> WeatherResponse:
    params = {
        "latitude": KYIV_CENTER.lat,
        "longitude": KYIV_CENTER.lon,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "rain",
                "snowfall",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
            ]
        ),
    }

    response = requests.get(
        OPEN_METEO_API_URL, params=params, timeout=DEFAULT_REQUEST_TIMEOUT
    )
    response.raise_for_status()

    data = response.json()

    return WeatherResponse(
        latitude=data["latitude"],
        longitude=data["longitude"],
        elevation=data["elevation"],
        timezone=data["timezone"],
        hourly_units=data["hourly_units"],
        hourly_data=data["hourly"],
    )
