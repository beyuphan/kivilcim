# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project uses [semantic versioning](https://semver.org/).

## [0.1.0] - 2026-11-03

First complete version.

### Added
- **Satellite detections** from NASA FIRMS: three VIIRS satellites (Suomi NPP, NOAA-20, NOAA-21) and
  MODIS, fetched every 30 minutes, validated, stored without duplicates, with a record of every run.
- **Map site** (`/`): a Turkey-wide map of active detection zones, nearby detections for any place
  (province and district pickers, device location, shareable links), a weather card, data freshness
  shown as two separate times, and a permanent disclaimer. Works on phones and installs as an app.
- **Weather index** (experimental): temperature, humidity, wind and a dry spell from Open-Meteo,
  combined by a transparent rule-based formula (v2), shown only around active detections.
- **Always-on heat sources** (factories, refineries, flares, landfills) are flagged, never dropped.
- **Past fires** (`/history`): big fires found in the satellite archive, with a day-by-day map of how
  the detections spread.
- **Methodology page** (`/methodology`) with the data, formulas, thresholds, licenses, privacy and
  limits, and the results of measuring the index against past detections.
- **API**: `/health`, `/status`, `/fires`, `/weather`, `/risk`, `/risk/zones`, `/events`,
  `/events/{id}`, with generated documentation at `/docs`.
- **One-command run** (`python -m kivilcim`) with its own ingest timer that catches up after downtime;
  `python -m kivilcim.ingest` for a single run or a backfill by date.
- **Server hardening**: per-client rate limits, security headers and a content security policy, gzip,
  CORS for the read-only API, a daily weather call quota, no request log by default.
- **Packaging**: Dockerfile and Compose file, continuous integration, ruff configuration.

### Known limits
- The weather index did not show a relation with whether a fire continues or grows the next day in
  the first measurement (see the methodology page), so it is presented only as how favorable the
  weather is for fire, not as a prediction.
- Events are grouped satellite detections, not confirmed incidents, and some are crop burning.
- The Docker image was not built on the machine this version was prepared on (the engine was not
  running); the installed-package path it relies on was tested in a clean virtual environment.
- History is VIIRS only and starts in June 2026.
