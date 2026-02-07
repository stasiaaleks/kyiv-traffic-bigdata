from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self


@dataclass(frozen=True, slots=True)
class VehiclePosition:
    vehicle_id: int
    route_id: int
    lat: float
    lon: float
    direction: int
    flag: int
    timestamp: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id,
            "route_id": self.route_id,
            "lat": self.lat,
            "lon": self.lon,
            "direction": self.direction,
            "flag": self.flag,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            vehicle_id=int(data.get("vehicle_id") or data.get("id", 0)),
            route_id=int(data.get("route_id") or data.get("routeId", 0)),
            lat=float(data["lat"]),
            lon=float(data["lon"]),
            direction=int(data.get("direction", 0)),
            flag=int(data.get("flag", 0)),
            timestamp=int(data.get("timestamp") or int(datetime.now().timestamp())),
        )


@dataclass(slots=True)
class RouteRecord:
    timestamp: str
    poll_number: int
    routes: list[dict[str, Any]]

    @property
    def route_count(self) -> int:
        return len(self.routes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected_by": "Aleksieienko",
            "timestamp": self.timestamp,
            "poll_number": self.poll_number,
            "route_count": self.route_count,
            "routes": self.routes,
        }


@dataclass(slots=True)
class WebSocketStats:
    connected: bool = False
    message_count: int = 0
    position_count: int = 0
    connection_count: int = 0
    last_update: float | None = None
    error_count: int = 0


@dataclass(slots=True)
class PollerStats:
    poll_count: int = 0
    polls_failed: int = 0
    total_positions: int = 0
    ws_flush_count: int = 0

    def record_poll_success(self) -> None:
        self.poll_count += 1

    def record_poll_failure(self) -> None:
        self.polls_failed += 1

    def record_position_flush(self, count: int) -> None:
        self.ws_flush_count += 1
        self.total_positions += count
