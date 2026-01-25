import json
import logging
import math
import sys
from collections import defaultdict
from pathlib import Path
from string import Template

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371
MAX_TIMESTAMP_GAP_SECONDS = 300
MAX_REALISTIC_SPEED_KMH = 120
MIN_SAMPLES_FOR_ROUTE_STATS = 10
TOP_ROUTES_COUNT = 10
KYIV_CENTER_LAT = 50.45
KYIV_CENTER_LON = 30.52

ROUTE_TYPE_LABELS = {
    1: "Bus",
    2: "Trol",
    3: "Tram",
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def load_positions(filepath: Path) -> list[dict]:
    positions = []
    with open(filepath, "r") as f:
        for line in f:
            record = json.loads(line)
            positions.extend(record.get("positions", []))
    return positions


def load_routes(filepath: Path) -> dict:
    routes = {}
    with open(filepath, "r") as f:
        for line in f:
            record = json.loads(line)
            for route in record.get("routes", []):
                route_id = route.get("id")
                if route_id:
                    routes[route_id] = route
    return routes


def calculate_speeds(positions: list[dict]) -> dict[int, list[float]]:
    by_vehicle: dict[int, list[dict]] = defaultdict(list)
    for pos in positions:
        vehicle_id = pos.get("vehicle_id")
        if vehicle_id:
            by_vehicle[vehicle_id].append(pos)

    speeds: dict[int, list[float]] = defaultdict(list)
    for vehicle_id, vehicle_positions in by_vehicle.items():
        sorted_pos = sorted(vehicle_positions, key=lambda p: p.get("timestamp", 0))

        for i in range(1, len(sorted_pos)):
            prev = sorted_pos[i - 1]
            curr = sorted_pos[i]

            dt = curr["timestamp"] - prev["timestamp"]
            if dt <= 0 or dt > MAX_TIMESTAMP_GAP_SECONDS:
                continue

            dist_km = haversine_km(prev["lat"], prev["lon"], curr["lat"], curr["lon"])
            speed_kmh = (dist_km / dt) * 3600

            if speed_kmh < 0:
                logger.warning(f"Negative speed detected for vehicle {vehicle_id}")

            if 0 < speed_kmh < MAX_REALISTIC_SPEED_KMH:
                speeds[vehicle_id].append(speed_kmh)

    return speeds


def _get_route_label(routes: dict, route_id: int) -> str:
    info = routes.get(route_id, {})
    number = info.get("number", "")
    route_type = info.get("type", 0)
    prefix = ROUTE_TYPE_LABELS.get(route_type, "")
    return f"{prefix} {number}".strip() if number else f"#{route_id}"


def _log_route_stats(
    title: str,
    route_data: list[tuple[int, list[float]]],
    routes: dict,
    route_vehicles: dict[int, set[int]],
) -> None:
    logger.info(f"{title}:")
    for route_id, speeds in route_data:
        label = _get_route_label(routes, route_id)
        vehicle_count = len(route_vehicles.get(route_id, set()))
        avg_speed = sum(speeds) / len(speeds) if speeds else 0
        logger.info(
            f"  {label}: {avg_speed:.1f} km/h avg, {vehicle_count} vehicles, {len(speeds)} samples"
        )


def log_speed_stats(
    speeds: dict[int, list[float]], routes: dict, positions: list[dict]
) -> None:
    vehicle_route: dict[int, int] = {}
    route_vehicles: dict[int, set[int]] = defaultdict(set)

    for pos in positions:
        vehicle_id = pos.get("vehicle_id")
        route_id = pos.get("route_id")
        if vehicle_id and route_id:
            vehicle_route[vehicle_id] = route_id
            route_vehicles[route_id].add(vehicle_id)

    all_speeds = []
    route_speeds: dict[int, list[float]] = defaultdict(list)

    for vehicle_id, veh_speeds in speeds.items():
        all_speeds.extend(veh_speeds)
        route_id = vehicle_route.get(vehicle_id)
        if route_id:
            route_speeds[route_id].extend(veh_speeds)

    if not all_speeds:
        logger.warning("No speed data available")
        return

    avg_speed = sum(all_speeds) / len(all_speeds)
    logger.info(
        f"Speed stats: {len(speeds)} vehicles, {len(all_speeds)} samples, "
        f"avg={avg_speed:.1f} km/h, min={min(all_speeds):.1f} km/h, max={max(all_speeds):.1f} km/h"
    )

    sorted_by_samples = sorted(
        route_speeds.items(), key=lambda x: len(x[1]), reverse=True
    )[:TOP_ROUTES_COUNT]
    _log_route_stats("Top routes by samples", sorted_by_samples, routes, route_vehicles)

    routes_with_enough_data = [
        (route_id, rsp)
        for route_id, rsp in route_speeds.items()
        if len(rsp) >= MIN_SAMPLES_FOR_ROUTE_STATS
    ]

    slowest = sorted(routes_with_enough_data, key=lambda x: sum(x[1]) / len(x[1]))[
        :TOP_ROUTES_COUNT
    ]
    _log_route_stats("Slowest routes", slowest, routes, route_vehicles)

    fastest = sorted(
        routes_with_enough_data, key=lambda x: sum(x[1]) / len(x[1]), reverse=True
    )[:TOP_ROUTES_COUNT]
    _log_route_stats("Fastest routes", fastest, routes, route_vehicles)


TEMPLATE_DIR = Path(__file__).parent / "templates"


def generate_html_map(
    positions: list[dict], speeds: dict[int, list[float]], output_path: Path
) -> None:
    latest: dict[int, dict] = {}
    for pos in positions:
        vehicle_id = pos.get("vehicle_id")
        if vehicle_id:
            if (
                vehicle_id not in latest
                or pos["timestamp"] > latest[vehicle_id]["timestamp"]
            ):
                latest[vehicle_id] = pos

    for vehicle_id, pos in latest.items():
        veh_speeds = speeds.get(vehicle_id, [])
        pos["avg_speed"] = sum(veh_speeds) / len(veh_speeds) if veh_speeds else 0

    if latest:
        center_lat = sum(p["lat"] for p in latest.values()) / len(latest)
        center_lon = sum(p["lon"] for p in latest.values()) / len(latest)
    else:
        center_lat, center_lon = KYIV_CENTER_LAT, KYIV_CENTER_LON

    template_path = TEMPLATE_DIR / "vehicle_map.html"
    template = Template(template_path.read_text())
    html = template.substitute(
        center_lat=center_lat,
        center_lon=center_lon,
        positions_json=json.dumps(list(latest.values())),
    )

    output_path.write_text(html)
    logger.info(f"Map saved to {output_path}")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    data_dir = Path(__file__).parent / "data"

    position_files = sorted(data_dir.glob("kpt_positions_*.jsonl"))
    route_files = sorted(data_dir.glob("kpt_routes_*.jsonl"))

    if not position_files:
        logger.error(f"No position data files found in {data_dir}")
        sys.exit(1)

    positions = load_positions(position_files[-1])
    logger.info(f"Loaded {len(positions)} positions from {position_files[-1].name}")

    routes = {}
    if route_files:
        routes = load_routes(route_files[-1])
        logger.info(f"Loaded {len(routes)} routes from {route_files[-1].name}")

    if not positions:
        logger.error("No position data to analyze")
        sys.exit(1)

    speeds = calculate_speeds(positions)

    log_speed_stats(speeds, routes, positions)

    map_path = data_dir / "vehicle_map.html"
    generate_html_map(positions, speeds, map_path)


if __name__ == "__main__":
    main()
