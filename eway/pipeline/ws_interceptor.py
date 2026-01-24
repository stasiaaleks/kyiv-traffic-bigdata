import asyncio
import base64
import json
import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime

import websockets

logger = logging.getLogger(__name__)

# This interceptor was an attempt to parse e-way data dur to their missing response for API keys mail request.
# KPT WS interceptor works better

# Connection configuration
EASYWAY_WEBSOCKET_URL = "wss://ws.easyway.info/sub/gps/c380645ad829453d38e3976ccf95eabd"
MESSAGE_TIMEOUT_SECONDS = 10.0
DEFAULT_LISTEN_DURATION_MINUTES = 3

# GPS coordinate processing
GPS_COORDINATE_SCALE_FACTOR = 100_000_000
COORDINATE_STRUCT_SIZE = 8
MIN_BASE64_MESSAGE_LENGTH = 100

# Geographic bounds (latitude_min, latitude_max, longitude_min, longitude_max)
KYIV_BOUNDS = (50.3, 50.6, 30.2, 30.8)
UKRAINE_BOUNDS = (44.0, 52.0, 22.0, 40.0)

FOUND_REGIONS_BOUNDS = [
    ((35, 40), (35, 42), "Turkey/Syria"),
    ((15, 25), (30, 40), "Red Sea/Sudan"),
    ((30, 35), (15, 25), "Libya/Egypt"),
]


@dataclass
class GpsCoordinate:
    latitude: float
    longitude: float
    timestamp: str
    offset: int

    def is_in_bounds(self, bounds: tuple[float, float, float, float]) -> bool:
        lat_min, lat_max, lng_min, lng_max = bounds
        return (
            lat_min <= self.latitude <= lat_max and lng_min <= self.longitude <= lng_max
        )

    def is_in_kyiv(self) -> bool:
        return self.is_in_bounds(KYIV_BOUNDS)

    def is_in_ukraine(self) -> bool:
        return self.is_in_bounds(UKRAINE_BOUNDS)

    def to_dict(self) -> dict:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timestamp": self.timestamp,
            "offset": self.offset,
        }


@dataclass
class CollectionStats:
    messages: int = 0
    coordinates: int = 0
    kyiv_found: int = 0

    def to_dict(self) -> dict:
        return {
            "messages": self.messages,
            "coordinates": self.coordinates,
            "kyiv_found": self.kyiv_found,
        }


@dataclass
class CategorizedPositions:
    kyiv: list[dict] = field(default_factory=list)
    ukraine: list[dict] = field(default_factory=list)
    other: list[dict] = field(default_factory=list)


def is_valid_coordinate(latitude: float, longitude: float) -> bool:
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def identify_region(latitude: float, longitude: float) -> str:
    for (lat_min, lat_max), (lng_min, lng_max), region_name in FOUND_REGIONS_BOUNDS:
        if lat_min <= latitude <= lat_max and lng_min <= longitude <= lng_max:
            return region_name
    return "Unknown region"


def decode_base64_message(message: str) -> bytes | None:
    padding_needed = 4 - (len(message) % 4)
    if padding_needed != 4:
        message += "=" * padding_needed

    try:
        return base64.b64decode(message)
    except ValueError as error:
        logger.warning(f"Base64 decode error: {error}")
        return None


def extract_coordinates_from_binary(data: bytes) -> list[GpsCoordinate]:
    coordinates = []
    timestamp = datetime.now().isoformat()

    for offset in range(
        0, len(data) - (COORDINATE_STRUCT_SIZE - 1), COORDINATE_STRUCT_SIZE
    ):
        try:
            lng_raw, lat_raw = struct.unpack(
                "<II", data[offset : offset + COORDINATE_STRUCT_SIZE]
            )
        except struct.error:
            break

        longitude = lng_raw / GPS_COORDINATE_SCALE_FACTOR
        latitude = lat_raw / GPS_COORDINATE_SCALE_FACTOR

        if not is_valid_coordinate(latitude, longitude):
            continue

        coordinates.append(
            GpsCoordinate(
                latitude=latitude,
                longitude=longitude,
                timestamp=timestamp,
                offset=offset,
            )
        )

    return coordinates


def categorize_positions(positions: list[GpsCoordinate]) -> CategorizedPositions:
    result = CategorizedPositions()

    for position in positions:
        position_dict = position.to_dict()
        if position.is_in_kyiv():
            result.kyiv.append(position_dict)
        elif position.is_in_ukraine():
            result.ukraine.append(position_dict)
        else:
            result.other.append(position_dict)

    return result


class LiveEasyWayClient:
    def __init__(self):
        self.positions: list[GpsCoordinate] = []
        self.stats = CollectionStats()

    async def connect_and_listen(
        self, duration_minutes: int = DEFAULT_LISTEN_DURATION_MINUTES
    ):
        logger.info("Connecting to EasyWay WebSocket...")
        logger.info(f"Will listen for {duration_minutes} minutes")
        logger.debug(f"URL: {EASYWAY_WEBSOCKET_URL}")

        try:
            async with websockets.connect(EASYWAY_WEBSOCKET_URL) as websocket:
                await self._listen_for_messages(websocket, duration_minutes)
        except Exception as error:
            self._log_connection_error(error)

    async def _listen_for_messages(self, websocket, duration_minutes: int):
        logger.info("Connected successfully")
        end_time = datetime.now().timestamp() + (duration_minutes * 60)
        message_count = 0

        while datetime.now().timestamp() < end_time:
            try:
                message = await asyncio.wait_for(
                    websocket.recv(), timeout=MESSAGE_TIMEOUT_SECONDS
                )
                message_count += 1
                logger.info(f"Message #{message_count} received ({len(message)} chars)")
                self._process_message(message)

            except asyncio.TimeoutError:
                logger.warning(
                    f"No message in {MESSAGE_TIMEOUT_SECONDS:.0f} seconds..."
                )
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Connection closed by server")
                break

        logger.info(f"Finished after {duration_minutes} minutes")
        self.save_results()

    def _log_connection_error(self, error: Exception):
        logger.error(f"Connection error: {error}")
        logger.debug(f"Error msg: {str(error).lower()}")

    def _process_message(self, message):
        self.stats.messages += 1
        coordinates = self._extract_coordinates(message)

        if not coordinates:
            logger.debug("No valid coordinates found")
            return

        self.stats.coordinates += len(coordinates)
        logger.info(f"Found {len(coordinates)} GPS coordinates")
        self._log_coordinate_findings(coordinates)
        self.positions.extend(coordinates)

    def _extract_coordinates(self, message) -> list[GpsCoordinate]:
        if isinstance(message, bytes):
            logger.debug(f"Binary message received ({len(message)} bytes)")
            return extract_coordinates_from_binary(message)

        logger.debug(f"Text message: {message[:100]}...")

        if len(message) < MIN_BASE64_MESSAGE_LENGTH:
            return []

        decoded = decode_base64_message(message)
        if decoded is None:
            return []

        logger.debug(f"Decoded to {len(decoded)} bytes")
        return extract_coordinates_from_binary(decoded)

    def _log_coordinate_findings(self, coordinates: list[GpsCoordinate]):
        kyiv_coords = [c for c in coordinates if c.is_in_kyiv()]
        ukraine_coords = [
            c for c in coordinates if c.is_in_ukraine() and not c.is_in_kyiv()
        ]
        other_coords = [c for c in coordinates if not c.is_in_ukraine()]

        if kyiv_coords:
            self.stats.kyiv_found += len(kyiv_coords)
            logger.info(f"KYIV FOUND: {len(kyiv_coords)} vehicles!")
            for coord in kyiv_coords:
                logger.info(f"  {coord.latitude:.6f}N, {coord.longitude:.6f}E")

        elif ukraine_coords:
            logger.info(f"UKRAINE: {len(ukraine_coords)} vehicles found")
            for coord in ukraine_coords[:3]:
                logger.info(f"  {coord.latitude:.6f}N, {coord.longitude:.6f}E")

        if other_coords:
            logger.debug(f"Other regions: {len(other_coords)} coordinates")
            for coord in other_coords[:2]:
                region = identify_region(coord.latitude, coord.longitude)
                logger.debug(
                    f"  {coord.latitude:.5f}N, {coord.longitude:.5f}E ({region})"
                )

    def save_results(self):
        if not self.positions:
            logger.warning("No data collected")
            return

        categorized = categorize_positions(self.positions)
        filename = (
            f"kyiv_transport_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        output_data = self._build_output_data(categorized)

        with open(filename, "w") as file:
            json.dump(output_data, file, indent=2)

        self._log_summary(filename, categorized)

    def _build_output_data(self, categorized: CategorizedPositions) -> dict:
        return {
            "collection_info": {
                "timestamp": datetime.now().isoformat(),
                "statistics": self.stats.to_dict(),
            },
            "summary": {
                "total_positions": len(self.positions),
                "kyiv_positions": len(categorized.kyiv),
                "ukraine_positions": len(categorized.ukraine),
                "other_positions": len(categorized.other),
            },
            "kyiv_vehicles": categorized.kyiv,
            "ukraine_vehicles": categorized.ukraine,
            "other_vehicles": categorized.other[:100],
        }

    def _log_summary(self, filename: str, categorized: CategorizedPositions):
        logger.info(f"Results saved to: {filename}")
        logger.info(f"Messages received: {self.stats.messages}")
        logger.info(f"Total coordinates: {len(self.positions)}")
        logger.info(f"Kyiv vehicles: {len(categorized.kyiv)}")
        logger.info(f"Ukraine vehicles: {len(categorized.ukraine)}")
        logger.info(f"Other regions: {len(categorized.other)}")


async def main():
    client = LiveEasyWayClient()

    try:
        await client.connect_and_listen()
    except KeyboardInterrupt:
        client.save_results()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    websockets_version = getattr(websockets, "__version__", "unknown")
    logger.info(f"Using websockets version: {websockets_version}")
    asyncio.run(main())
