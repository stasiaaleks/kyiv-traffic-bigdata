import json
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

KYIV_BOUNDS = (50.3, 50.6, 30.2, 30.8)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
REQUEST_TIMEOUT_SECONDS = 120
QUERY_TIMEOUT_SECONDS = 90
REQUEST_DELAY_SECONDS = 1.5
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "data"
OSM_XML_VERSION = 0.6

DEFAULT_HIGHWAY_TYPES = [
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "residential",
]

LANDUSE_TYPES = "residential|industrial|commercial|retail"


@dataclass
class OsmFilter:
    element: str
    tags: list[tuple[str, str | None]]

    def to_query(self) -> str:
        tag_parts = "".join(
            f'["{tag}"="{value}"]' if value else f'["{tag}"]'
            for tag, value in self.tags
        )
        return f"{self.element}{tag_parts}"

    @classmethod
    def with_pattern(cls, element: str, tag: str, pattern: str) -> str:
        return f'{element}["{tag}"~"^({pattern})$"]'


OSM_FILTERS = {
    "road_narrowing": [
        OsmFilter("way", [("narrow", "yes")]),
        OsmFilter("way", [("highway", "construction")]),
        OsmFilter("way", [("lanes:forward", None), ("lanes:backward", None)]),
        OsmFilter("node", [("traffic_calming", None)]),
        OsmFilter("node", [("barrier", "bollard")]),
    ],
    "elevation": [
        OsmFilter("way", [("incline", None)]),
        OsmFilter("node", [("ele", None)]),
        OsmFilter("way", [("highway", None), ("incline", None)]),
    ],
    "traffic_signals": [
        OsmFilter("node", [("highway", "traffic_signals")]),
        OsmFilter("node", [("crossing", "traffic_signals")]),
    ],
    "pedestrian": [
        OsmFilter("node", [("highway", "crossing")]),
        OsmFilter("way", [("highway", "footway"), ("footway", "crossing")]),
    ],
}


def build_query_body(filters: list[OsmFilter]) -> str:
    lines = [f.to_query() + ";" for f in filters]
    return "(\n" + "\n".join(lines) + "\n)"


@dataclass
class OSMResponse:
    elements: list[dict[str, Any]]
    timestamp: str
    version: float
    generator: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OverpassQueryBuilder:
    def __init__(self, bbox_str: str, timeout: int = QUERY_TIMEOUT_SECONDS):
        self.bbox_str = bbox_str
        self.timeout = timeout

    def build(self, body: str, include_geometry: bool = True) -> str:
        header = f"[out:json][timeout:{self.timeout}][bbox:{self.bbox_str}];"
        footer = "out body;\n>;\nout skel qt;" if include_geometry else "out body;"
        return f"{header}\n{body}\n{footer}"


class OSMImporter:
    def __init__(self, bbox: tuple[float, float, float, float] | None = None):
        self.bbox = bbox or KYIV_BOUNDS
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "KyivTrafficAnalysis/1.0 (research project)"}
        )
        self._last_request_time = 0.0
        self._query_builder = OverpassQueryBuilder(self._build_bbox_str())

    def _build_bbox_str(self) -> str:
        south, north, west, east = self.bbox
        return f"{south},{west},{north},{east}"

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)

    def _execute_query(self, overpass_query: str) -> OSMResponse:
        self._rate_limit()

        try:
            response = self.session.post(
                OVERPASS_URL,
                data={"data": overpass_query},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as err:
            logger.error(f"Overpass API request failed: {err}")
            raise

        self._last_request_time = time.time()
        data = response.json()

        return OSMResponse(
            elements=data.get("elements", []),
            timestamp=data.get("osm3s", {}).get("timestamp_osm_base", ""),
            version=data.get("version", 0.0),
            generator=data.get("generator", ""),
        )

    def fetch_road_network(self, highway_types: list[str] | None = None) -> OSMResponse:
        types = highway_types or DEFAULT_HIGHWAY_TYPES
        pattern = "|".join(types)
        body = OsmFilter.with_pattern("way", "highway", pattern)
        query = self._query_builder.build(body)
        return self._execute_query(query)

    def fetch_road_narrowing(self) -> OSMResponse:
        body = build_query_body(OSM_FILTERS["road_narrowing"])
        query = self._query_builder.build(body)
        return self._execute_query(query)

    def fetch_landuse_zones(self) -> OSMResponse:
        body = f"""(
                {OsmFilter.with_pattern("way", "landuse", LANDUSE_TYPES)};
                {OsmFilter.with_pattern("relation", "landuse", LANDUSE_TYPES)};
            )"""

        query = self._query_builder.build(body)
        return self._execute_query(query)

    def fetch_elevation_data(self) -> OSMResponse:
        body = build_query_body(OSM_FILTERS["elevation"])
        query = self._query_builder.build(body)
        return self._execute_query(query)

    def fetch_traffic_signals(self) -> OSMResponse:
        body = build_query_body(OSM_FILTERS["traffic_signals"])
        query = self._query_builder.build(body, include_geometry=False)
        return self._execute_query(query)

    def fetch_pedestrian_crossings(self) -> OSMResponse:
        body = build_query_body(OSM_FILTERS["pedestrian"])
        query = self._query_builder.build(body)
        return self._execute_query(query)

    def fetch_all(self) -> dict[str, OSMResponse]:
        return {
            "road_network": self.fetch_road_network(),
            "narrowing": self.fetch_road_narrowing(),
            "landuse": self.fetch_landuse_zones(),
            "elevation": self.fetch_elevation_data(),
            "traffic_signals": self.fetch_traffic_signals(),
            "pedestrian_crossings": self.fetch_pedestrian_crossings(),
        }

    def to_geojson(self, response: OSMResponse) -> dict[str, Any]:
        features = [
            self._node_to_geojson_feature(elem)
            for elem in response.elements
            if self._is_valid_node(elem)
        ]
        return {"type": "FeatureCollection", "features": features}

    def _is_valid_node(self, element: dict[str, Any]) -> bool:
        return element.get("type") == "node" and "lat" in element and "lon" in element

    def _node_to_geojson_feature(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "Feature",
            "id": node.get("id"),
            "geometry": {
                "type": "Point",
                "coordinates": [node["lon"], node["lat"]],
            },
            "properties": node.get("tags", {}),
        }

    def save_response(
        self,
        response: OSMResponse,
        name: str,
        output_dir: Path | str | None = None,
        output_format: str = "json",
    ) -> Path:
        directory = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
        directory.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.{output_format}"
        filepath = directory / filename

        data = (
            self.to_geojson(response)
            if output_format == "geojson"
            else response.to_dict()
        )

        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

        logger.info(f"Saved {filepath} ({len(response.elements)} elements)")
        return filepath

    def save_all(
        self,
        data: dict[str, OSMResponse],
        output_dir: Path | str | None = None,
    ) -> dict[str, Path]:
        return {
            name: self.save_response(response, name, output_dir)
            for name, response in data.items()
        }

    def fetch_and_save_all(
        self,
        output_dir: Path | str | None = None,
    ) -> dict[str, Path]:
        data = self.fetch_all()
        return self.save_all(data, output_dir)


def _parse_node(node: ET.Element) -> dict[str, Any]:
    return {
        "type": "node",
        "id": int(node.get("id", 0)),
        "lat": float(node.get("lat", 0)),
        "lon": float(node.get("lon", 0)),
        "tags": {tag.get("k"): tag.get("v") for tag in node.findall("tag")},
    }


def _parse_way(way: ET.Element) -> dict[str, Any]:
    return {
        "type": "way",
        "id": int(way.get("id", 0)),
        "nodes": [int(nd.get("ref", 0)) for nd in way.findall("nd")],
        "tags": {tag.get("k"): tag.get("v") for tag in way.findall("tag")},
    }


def _parse_relation(relation: ET.Element) -> dict[str, Any]:
    members = [
        {
            "type": member.get("type"),
            "ref": int(member.get("ref", 0)),
            "role": member.get("role", ""),
        }
        for member in relation.findall("member")
    ]
    return {
        "type": "relation",
        "id": int(relation.get("id", 0)),
        "members": members,
        "tags": {tag.get("k"): tag.get("v") for tag in relation.findall("tag")},
    }


def parse_xml_to_json(xml_content: str | bytes) -> dict[str, Any]:
    if isinstance(xml_content, bytes):
        xml_content = xml_content.decode("utf-8")

    root = ET.fromstring(xml_content)

    elements = []
    elements.extend(_parse_node(node) for node in root.findall(".//node"))
    elements.extend(_parse_way(way) for way in root.findall(".//way"))
    elements.extend(_parse_relation(rel) for rel in root.findall(".//relation"))

    return {
        "version": OSM_XML_VERSION,
        "generator": "xml_parser",
        "elements": elements,
    }


def save_to_file(data: dict[str, Any], filename: str) -> None:
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    importer = OSMImporter()

    logger.info("OSM Data Import for Kyiv")

    logger.info("Fetching traffic signals...")
    signals = importer.fetch_traffic_signals()
    importer.save_response(signals, "traffic_signals")
    importer.save_response(signals, "traffic_signals", output_format="geojson")

    logger.info("Fetching pedestrian crossings...")
    crossings = importer.fetch_pedestrian_crossings()
    importer.save_response(crossings, "pedestrian_crossings")
    importer.save_response(crossings, "pedestrian_crossings", output_format="geojson")

    logger.info("Fetching road narrowing...")
    narrowing = importer.fetch_road_narrowing()
    importer.save_response(narrowing, "road_narrowing")

    logger.info("Fetching landuse zones...")
    landuse = importer.fetch_landuse_zones()
    importer.save_response(landuse, "landuse_zones")

    logger.info("Fetching elevation data...")
    elevation = importer.fetch_elevation_data()
    importer.save_response(elevation, "elevation")

    logger.info("Fetching road network (this may take a while)...")
    roads = importer.fetch_road_network()
    importer.save_response(roads, "road_network")

    logger.info("Import complete")
    logger.info(f"Files saved to: {DEFAULT_OUTPUT_DIR}")
