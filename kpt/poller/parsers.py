from __future__ import annotations

import functools
import json
import re
from typing import TYPE_CHECKING, Any

from .models import VehiclePosition

if TYPE_CHECKING:
    from .config import KyivCoordinateBounds

# Pre-compiled regex for Socket.IO event messages
SOCKET_IO_EVENT_PATTERN = re.compile(r'^42\["(\w+)",(.*)]\s*$', re.DOTALL)

# Expected number of fields in CSV position format
CSV_POSITION_FIELD_COUNT = 7


class PositionParseError(Exception):
    pass


def parse_csv_position(
    data: str, bounds: KyivCoordinateBounds | None = None
) -> VehiclePosition | None:
    """
    Parse CSV vehicle position string.
    Format: "vehicle_id,route_id,lat,lon,direction,flag,timestamp"
    Example: "12585093,12583358,50.50963,30.64338,0,0,1769342268"
    """
    parts = data.split(",")
    if len(parts) != CSV_POSITION_FIELD_COUNT:
        return None

    try:
        lat = float(parts[2])
        lon = float(parts[3])

        if bounds is not None and not bounds.contains(lat, lon):
            return None

        return VehiclePosition(
            vehicle_id=int(parts[0]),
            route_id=int(parts[1]),
            lat=lat,
            lon=lon,
            direction=int(parts[4]),
            flag=int(parts[5]),
            timestamp=int(parts[6]),
        )
    except (ValueError, IndexError):
        return None


def parse_socket_io_event(message: str) -> tuple[str, Any] | None:
    """
    Parse Socket.IO event message.
    Format: 42["event_name", payload]
    """

    match = SOCKET_IO_EVENT_PATTERN.match(message)
    if not match:
        return None

    try:
        event_name = match.group(1)
        payload = json.loads(match.group(2))
        return event_name, payload
    except json.JSONDecodeError:
        return None


def extract_positions_from_payload(
    payload: Any, bounds: KyivCoordinateBounds | None = None
) -> list[VehiclePosition]:
    positions: list[VehiclePosition] = []

    if isinstance(payload, list):
        for item in payload:
            position = _extract_single_position(item, bounds)
            if position:
                positions.append(position)
    elif isinstance(payload, str):
        position = parse_csv_position(payload, bounds)
        if position:
            positions.append(position)

    return positions


def _extract_single_position(
    item: Any, bounds: KyivCoordinateBounds | None
) -> VehiclePosition | None:
    if isinstance(item, str):
        return parse_csv_position(item, bounds)
    elif isinstance(item, dict) and "lat" in item:
        try:
            position = VehiclePosition.from_dict(item)
            if bounds is None or bounds.contains(position.lat, position.lon):
                return position
        except (KeyError, ValueError, TypeError):
            pass
    return None


POSITION_EVENT_NAMES = frozenset({"locations", "vehicles", "positions", "v"})


@functools.lru_cache(maxsize=128)
def is_position_event(event_name: str) -> bool:
    return event_name in POSITION_EVENT_NAMES


class MessageParser:
    def __init__(self, bounds: KyivCoordinateBounds | None = None) -> None:
        self.bounds = bounds

    def parse(self, message: str) -> list[VehiclePosition]:
        positions: list[VehiclePosition] = []

        # try CSV
        csv_position = parse_csv_position(message, self.bounds)
        if csv_position:
            return [csv_position]

        # try Socket.IO event
        event = parse_socket_io_event(message)
        if event:
            event_name, payload = event
            if is_position_event(event_name):
                positions = extract_positions_from_payload(payload, self.bounds)

        return positions


def parse_handshake_response(text: str) -> tuple[str | None, int | None]:
    """
    Parse Socket.IO handshake response to extract session ID
    Response: <length>:<packet_type><json>
    Example: 97:0{"sid":"abc123","upgrades":["websocket"],"pingInterval":25000}
    """

    if ":0{" not in text:
        return None, None

    try:
        json_start = text.index(":0{") + 2
        json_str = _extract_json_object(text[json_start:])
        data = json.loads(json_str)
        return data.get("sid"), data.get("pingInterval")
    except (ValueError, json.JSONDecodeError):
        return None, None


def _extract_json_object(text: str) -> str:
    brace_count = 0
    for i, char in enumerate(text):
        if char == "{":
            brace_count += 1
        elif char == "}":
            brace_count -= 1
            if brace_count == 0:
                return text[: i + 1]
    return text
