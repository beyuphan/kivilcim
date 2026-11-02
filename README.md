# Kıvılcım

Kıvılcım is a wildfire monitoring service built on NASA FIRMS satellite data. For a given location, it shows nearby active fire detections, when they were observed, and how fresh the data is.

> **Not an official warning system.** Kıvılcım shows satellite detections, which can be delayed and can include false positives. In an emergency, call **112** and contact **AFAD** in Turkey.

## Status

Early development. The first milestone is the backend: a scheduled FIRMS ingest job and a REST API.

## Planned features (MVP)

- Location-based query of recent active fire detections within a chosen radius
- Each detection shows observation time, confidence, and satellite source
- Simple weather summary for a location (wind, humidity, temperature)
- Experimental fire danger index: rule-based and weather-driven, shown around active fires on the map and available for any location in Turkey (not a prediction that a fire will happen)
- Visible data freshness on every response
- Web interface that works on mobile and can be installed as a PWA

See [PLAN.md](PLAN.md) for the full scope, architecture, and roadmap.

## Data sources

- [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/): active fire detections (free, API key required)
- [OpenStreetMap](https://www.openstreetmap.org/copyright): map tiles and province/district center points in `data/` (ODbL 1.0, attribution required, not official boundaries)
- [Open-Meteo](https://open-meteo.com/): weather data (free for non-commercial use, no key required; licensed CC BY 4.0, attribution required)

## Tech stack (planned)

- Python and FastAPI
- SQLite first, PostgreSQL with PostGIS later
- pytest for tests

## Getting started

Development setup (Python 3.11+):

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on Linux/macOS
pip install -e ".[dev]"
pytest
```

If Windows Application Control blocks SQLAlchemy's compiled extension (`DLL load failed ... _row_cy`), install it without the C extension:

```bash
DISABLE_SQLALCHEMY_CEXT=1 pip install --force-reinstall --no-deps --no-binary sqlalchemy sqlalchemy
```

Run the FIRMS ingest job (needs `FIRMS_MAP_KEY` in `.env`; run it from a scheduler every 15-30 minutes):

```bash
python -m kivilcim.ingest
```

Load the past months so the history page has something to show (FIRMS answers at most 5 days per request, so this runs in windows; the archive product `_SP` covers older dates and `_NRT` roughly the last three months):

```bash
python -m kivilcim.ingest --source VIIRS_SNPP_SP --from 2026-06-01 --to 2026-07-31
python -m kivilcim.ingest --source VIIRS_SNPP_NRT --from 2026-08-01 --to 2026-11-02
```

Start the server, then open http://127.0.0.1:8000/history for past fires, and http://127.0.0.1:8000/ for the map (click the map to choose a location) or http://127.0.0.1:8000/docs for the interactive API documentation:

```bash
uvicorn kivilcim.api:create_app --factory
```

Endpoints:

- `GET /health`: is the service up
- `GET /status`: last successful update and delay per source, plus `data_as_of`
- `GET /fires?lat=&lon=&radius_km=&since_hours=`: nearby detections with observation time, confidence and distance. Low-confidence detections are listed separately.
- `GET /events` and `GET /events/{id}`: big fires found in the satellite history, with the detections and a per-day count from days before to days after. Shown on the page at `/history`. These are grouped satellite detections, not confirmed incidents.
- `GET /weather?lat=&lon=`: current weather for a point in Turkey (temperature, humidity, wind, rain over 72 hours), cached per ~11 km.
- `GET /risk?lat=&lon=`: experimental fire danger level from that weather, with the factors behind it. It describes weather conditions, not the probability of a fire.

## Configuration

API keys are read from a `.env` file, which is never committed. Create one locally with:

```bash
FIRMS_MAP_KEY=your_key_here
```

## License

MIT. See [LICENSE](LICENSE).
