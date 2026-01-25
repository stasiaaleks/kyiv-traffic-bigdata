# OpenStreetMap Data Source

## Data Access

- **Overpass API**: <https://overpass-api.de/api/interpreter>
- **Bulk export**: <https://download.geofabrik.de> (.osm.pbf files)
- **Formats**: JSON (recommended), XML, GeoJSON

## Limits

- ~10,000 requests/day per IP
- 180s timeout, 1GB max response
- 1-2s pause between requests

## Relevant OSM Tags

**Road network**: `highway`, `lanes`, `maxspeed`, `oneway`, `surface`

**Bottlenecks**: `lanes:forward/backward`, `width`, `narrow=yes`, `highway=construction`

**Land use**: `landuse=residential/industrial/commercial`, `incline`, `ele`

**Traffic signals**: `highway=traffic_signals`, `traffic_signals:direction`

**Crossings**: `highway=crossing`, `crossing=zebra/traffic_signals/uncontrolled`
