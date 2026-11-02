# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project uses [semantic versioning](https://semver.org/).

## [0.10.0] - 2026-11-03

### Changed
- **The spread of a fire was redone.** Instead of colored dots, the satellite's 375 m pixels are drawn as squares,
  each once, on the day it was first seen (violet early, yellow late; the day's new area glows white), over the
  cloud-free picture. The panel shows the area seen so far, the gain of the day, a chart of the area and of the
  daily growth you can click, a player with 1x/2x/4x, the fastest day and the strongest detection. It plays once
  on opening. It counts pixels, not burned land, and says so (`static/js/spread.js`).
- **Searching a place on the past-fires page filters the fires** (a province, or 40 km around a district) and zooms
  the map there, instead of leaving for the place's own page. A chip clears it; the year bars follow it.
- **"Did it burn?" suggests fires from the whole archive.** It looked only 120 days back; now it lists the eight
  biggest fires the satellites saw within 15 km of the place since 2016, with their dates, to pick from. The 120
  days is only how long a fire may have lasted, and the texts say so.
- **Zoom stops where the picture stops being sharp:** 14 on the cloudless mosaic, 13 over 30 m fire images, 10 on
  yesterday's 250 m picture (17 on the street map).

### Added
- **A switch to hide the pins** (and the dots of a fire) to see the ground under them; remembered.
- A fire's detail now returns up to 40,000 detections (was 5,000, which cut the later days of a fire like Manavgat).

## [0.9.0] - 2026-11-03

### Changed
- **A new look.** The page is one night-time scene: the map fills the screen, the top bar and the panel float
  over it as glass, and heat is the one warm color. A hero at the top of the panel says how many zones are
  active and counts up; the zone list shows each zone's heat; the map legend is a heat ramp.
- **The map's satellite picture is cloud-free.** The default base is the Sentinel-2 cloudless mosaic (EOX);
  the street map and yesterday's real picture (with its clouds, to tell cloud from smoke) are one tap away.
  The choice is remembered.
- **Pins you can click.** Zones, past fires and the detections of a place are now real pins: they glow, pulse,
  can be reached with the keyboard and open a card with the details and a way in. Heat color and size tell how
  strong a zone is; the number is how many detections it has. Hovering a row or a card lights its pin.
- **Loading is visible.** Switching the base or the images of a fire shows a loading message with a spinner and
  a moving bar, keeps the old picture until the new one is ready, and fades the new one in.
- The date line of a before/after comparison starts in the middle of the part of the map you can see.

### Added
- **Fires of earlier years.** The past-fires page now covers 2016 to today. `scripts/backfill_history.py` loads
  years of VIIRS detections from FIRMS (paced for its 5,000 transactions per 10 minutes), events older than 120
  days are found once per year and stored in a `fire_events` table (`kivilcim.history`), and the thresholds follow
  the number of satellites that flew (Suomi NPP from 2012, NOAA-20 from April 2018, NOAA-21 from January 2023).
  `scripts/build_history.py` builds the table; the site tops it up after each ingest.
- A bar chart of the years on the past-fires page (a bar per year, as tall as the heat of its fires; tap to filter),
  a newest/biggest sort, and the biggest fires of all years on the home page. `GET /events` takes `sort=largest` and
  up to 500 results.

### Fixed
- A place counts as inside Turkey up to 33 km from a district center (was 30), so forest fires on peninsulas such as Marmaris are no longer left out of the past-fires list; Syria and Iraq begin beyond that.
- Fixed-source detection reads the last year of detections instead of all of them, so it stays fast as the
  database grows; SQLite waits up to a minute for another writer.

## [0.8.0] - 2026-11-03

### Added
- **Printable report** (`/report`): one page for a place check or a past fire, with the before/after picture,
  a table of dates, sources, resolution and cloud cover, what the images cannot tell, the source and a link;
  print or save as a PDF. A "Yazdırılabilir rapor" button on fires and place checks.
- **Deployment**: `docs/deploy.md` and `docker-compose.prod.yml` with a Caddy reverse proxy (automatic HTTPS)
  for one small server, with Telegram, backup and health-check notes. The compose file was checked with
  `docker compose config`; the image itself was not built on the machine used for this release.

### Fixed
- A stray "null" appeared in some panels when an optional button was absent.

## [0.7.0] - 2026-11-03

### Added
- **Telegram alerts.** A bot (set `TELEGRAM_BOT_TOKEN`) that people talk to: send a location or a district
  name (`/yer Çankaya, Ankara`), pick a radius (`/yaricap 1 25`), and get a message when a satellite sees a
  new fire inside it, with distance, direction, age and the wind relative to the fire. Commands `/liste`,
  `/durdur`, `/yardim`. After every ingest the new detections are matched against the places; weak, low
  confidence and always-burning sources are skipped and one fire is announced once in six hours. The bot
  reads its messages by polling Telegram, so it needs no public address.
- A "Telegram'dan haber ver" button on every place and a tile on the home page (only when the bot is set up;
  `GET /alerts/bot`), and an optional public channel that gets big new fires (`TELEGRAM_CHANNEL`).
- Privacy: only the chat number and a place rounded to a kilometer are stored; `/durdur`, blocking the bot
  or a year of silence deletes them. Written up on the methodology page.

## [0.6.0] - 2026-11-03

### Added
- **Share cards.** `GET /events/{id}/card.jpg` and `GET /imagery/place/card.jpg`: a 1200x630 picture with the
  satellite before and after side by side, the place, the dates, the source and the cloud cover. Built from
  the map's own tiles with Pillow, kept for six hours. The page of a fire, a place check or any place now
  carries its own title, description and preview tags (Open Graph, Twitter), so a link shows the picture
  when it is posted. A "Görseli indir" and a "Bağlantıyı paylaş" button on fires and place checks.
- **Province pages** (`/il/{slug}`, 81 of them) with a short text for search engines (the last day's
  detections for that province, its districts), shown until the app draws the map; `/sitemap.xml` and
  `/robots.txt`.
- **`/embed`**: a small map of the active zones that other sites can show in a frame (the only page without
  `frame-ancestors 'none'`).
- `PUBLIC_URL` setting for canonical addresses, previews and the sitemap.
- The Inter font (SIL Open Font License) for the cards, since the default font has no Turkish letters.

## [0.5.0] - 2026-11-03

### Added
- **Satellite schedule.** `GET /passes?lat&lon` and a "Uydu takvimi" card on every place: how many times
  Suomi NPP, NOAA-20, NOAA-21, Terra and Aqua scanned the place in the last 24 hours, the last and next scan
  and the longest gap with none, predicted with SGP4 from CelesTrak orbit data (kept in the database,
  refreshed twice a day, marked unreliable when more than two weeks old). "No detection" now says whether a
  satellite actually looked. The swath half widths were checked against 600 real detections
  (`scripts/check_passes.py`); the method is on the methodology page.

### Fixed
- The service worker no longer stores the answers of `/imagery/*` and `/passes`.

## [0.4.0] - 2026-11-03

### Added
- **Wind and fire.** On a place near a fire, the page says whether the wind blows from the nearest detection
  toward the place, across, or away, draws the line to the fire and lists the district centers within 25 km
  of it with the same check. My places show the wind verdict too. It is geometry on the wind now, not a
  forecast, and the page and the methodology say so.
- A share button on a place (the system share sheet, or a copied link).

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
