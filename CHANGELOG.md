# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project uses [semantic versioning](https://semver.org/).

## [0.3.0] - 2026-11-03

### Added
- **"Did it burn?" (`/check`)**: the 30 m satellite image from before and after a fire at any place in
  Turkey, for dates the person enters. The dates are suggested from the strong detections near the place
  (`GET /imagery/suggest`); the images come from `GET /imagery/place`, which uses the same cloud-free
  search as the past fires, rounds the place to about 1 km and keeps the result for a month.
- **My places**: places saved in the browser only, shown on the home page with what was seen within 10 km
  in the last 24 hours.
- **Harita/Uydu switch**: yesterday's 250 m VIIRS picture of the region on the map, to tell cloud from
  smoke.
- Home page tiles for the two main questions, a "Yerim yandı mı?" button and "Yerlerime ekle" on a place.
- A "Who it is for, and why use it" section in the README.

### Changed
- The before/after panel is one shared component for past fires and place checks.

## [0.2.2] - 2026-11-03

### Fixed
- **Cloudy images are never shown.** Cloud was being mistaken for smoke. Images with more than 10 % cloud
  over the fire are now left out (before, up to 35 % was shown as "az bulutlu"), a clear image (under 3 %)
  is preferred even when it is up to 14 days farther away, and the search reaches 30 days before and 45
  days after. If no clear image exists a side is left empty instead of showing cloud.

## [0.2.1] - 2026-11-03

### Changed
- **The "after" image is taken once the fire is out.** It was taken the day after the last detection of the
  event, while fires often still burned. It now waits three days after the last strong detection (5 MW or
  more) near the center of the fire, looked at up to two weeks past the event. If that is too recent, the
  page says the image is not there yet and looks again every few hours.
- **More and more reliable images.** Each side offers up to five usable days as date buttons that swap the
  image on the map. When no 30 m image is clear, the best of four daily 250 m images (VIIRS Suomi NPP,
  VIIRS NOAA-20, MODIS Terra, MODIS Aqua) is used. Failed requests are tried again, and a result built from
  failed requests is searched again after 15 minutes instead of being kept.
- **Faster past fires.** The images of the biggest fires are searched in the background after every update,
  so the list opens with its thumbnails ready; both sides of a search run at the same time, and the
  imagery rate limit is 60 a minute because most answers are now stored.

## [0.2.0] - 2026-11-03

The whole front end was redone, and every past fire now shows satellite images from before and after.

### Added
- **Before and after satellite images** for every past fire, on one map with a draggable line. The
  server picks the nearest usable day on each side from NASA GIBS (30 m Sentinel-2 and Landsat, the
  250 m daily VIIRS image as a labeled fallback) by measuring cloud cover on real tiles, and stores the
  choice. New endpoint `GET /events/{id}/imagery`, with its own rate limit.
- **Past fires as part of the app**, not a link: featured fires with before/after thumbnails on the home
  panel, a list with a year filter and a map of all fires, and a page per fire (`/history/{id}`) with the
  image comparison and a play button that replays the spread.
- **A clearer "active zones" panel**: a sentence that says what was seen, three numbers, an expandable
  explanation of zones, detections and colors, level names next to colors, and small zones tucked away.
- `/risk/zones?weather=false` answers at once without the weather index.

### Changed
- **New design** (`docs/design-system.md`): one panel and one map on wide screens, a three-height sheet on
  phones, light and dark themes, measured text contrast, 44 px touch targets, keyboard and screen reader
  support, and reduced-motion support.
- **Faster map**: no third-party scripts (the map library is served from this site), markers drawn on one
  canvas, base tiles requested only when the map stops moving, zones drawn before the weather arrives,
  and weather fetched ahead of time after each update. In a desktop run, first paint went from 1.4 s to
  0.14 s and layout shift from 0.023 to 0.014.
- The pages are one app that swaps views without reloading; the content security policy now allows
  scripts from this site only.

### Fixed
- The test suite no longer imports one test module from another, which failed in continuous integration.

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
