# Kyiv Traffic Data Pipelines

ETL pipelines for ingesting traffic-related data about Kyiv from multiple APIs.

## Structure

```
├── eway/           # Kyiv public transport API
│   ├── docs/       # API documentation
│   ├── pipeline/   # ETL modules (extract, transform, load)
│   └── data/       # Ingested data storage
├── weather/        # Weather API
│   ├── docs/
│   ├── pipeline/
│   └── data/
```

## Setup

```bash
# With uv (recommended)
uv sync

# Without uv
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and add your API keys:

```bash
cp .env.example .env
```
