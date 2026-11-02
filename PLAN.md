# Kıvılcım: Proje Planı / Project Plan

> Bu dosya iki dilli: önce İngilizce, sonra Türkçe. Her iki bölüm de aynı içeriği taşır.
> This file is bilingual: English first, then Turkish. Both sections contain the same content.

---

# English

## 1. Vision

Kıvılcım is a service that uses NASA FIRMS satellite data to give an honest, understandable answer to one question for a given location: **"Is there an active fire near me, how fresh is the data, and how dangerous is today's fire weather in my area?"**

The last part is answered by an experimental, rule-based, weather-driven fire danger index: it shows how favorable the weather is for fires, around active fires on the map and for any location a user looks up. It is not a prediction that a fire will happen.

Kıvılcım is not an official warning system. This is stated clearly on every screen and in every API response, and emergencies are directed to 112 and AFAD (Turkey).

## 2. Target users

- **Primary:** General users who want to know whether there is a fire in their area.
- **Secondary (later stage):** Developers and small organizations who want API access to the data.

## 3. MVP scope

Included in the MVP:

1. The user picks a location. The backend only accepts latitude/longitude; the frontend converts a province/district selection into coordinates.
2. Recent active fire detections within a chosen radius are listed and shown on a map.
3. Each detection shows its **observation time, confidence value and satellite source**.
4. A simple weather summary for the location (wind, humidity, temperature).
5. A responsibility note on every page (not an official warning; data may be delayed).
6. The data ingestion job runs regularly, and the last successful update time is visible in the API.
7. A **spread-favorability index around active fires**: a rule-based index computed from the current weather at an active fire detection. It says how favorable the weather is for the fire to continue and spread, with the reasons behind it. It is shown only where there is an active detection, never as a general "fire risk" for a place without one. It is not a spread forecast or a probability, and it is labeled experimental until validated (see section 9).

Deliberately **out of scope** for the MVP:

- Spread direction and speed estimation (next stage; not shown before backtesting)
- Machine-learning risk or ignition-probability models, and risk based on vegetation, terrain or drought history (the MVP risk index is weather-only and rule-based)
- A nationwide risk grid. The MVP computes risk only around active fires and for locations users look up; a grid can be added later if needed
- Satellite imagery and burn scar analysis
- User input (crowd reports)
- Intervention information (helicopters, crews, etc.): we cannot produce this; tracking official sources is a separate decision
- A native mobile app
- User accounts and notifications (in the MVP, locations are stored in the browser)

## 4. Mobile or web?

**Decision: The MVP is web-based and mobile-friendly (responsive, installable as a PWA).**

Reasons:
- One codebase, no App Store / Play Store process.
- Most of the value is in the backend and data; the frontend can change later.
- The core feature (map + location + list) works well in a browser.

A native app should be considered only if push notifications become a critical need, or if background location tracking is required.

## 5. Architecture

```
[FIRMS API] ──► [Ingest job (scheduled)] ──► [Database]
[Open-Meteo] ──► [Risk zones job (scheduled)] ──┘    │
                                                      ▼
                                              [REST API (FastAPI)]
                                                      │
                                                      ▼
                                          [Web frontend (later)]
```

Technology choices (recommended for the MVP):
- **Language and framework:** Python, FastAPI
- **Database:** Start with SQLite, move to PostgreSQL + PostGIS as data grows
- **Scheduling:** The ingest job is a standalone command (`python -m kivilcim.ingest`) invoked by an external scheduler (OS task scheduler, cron, later a container). It is kept separate from the API process so it is easy to test and restart.
- **Ingest frequency:** Start with every 15-30 minutes, fetching a recent time window and relying on deduplication. Verified with a real call: the FIRMS limit is 5,000 transactions per 10 minutes, so it does not constrain the interval. The `day_range` maximum is 5 days per request (6 or more returns HTTP 400). Older data needs a start-date parameter, to be verified when it is needed.
- **FIRMS sources:** Start with `VIIRS_SNPP_NRT` and `MODIS_NRT`. Add `VIIRS_NOAA20_NRT` and `VIIRS_NOAA21_NRT` early, because each satellite passes at different times and together they give more frequent observations.
- **Risk refresh:** A standalone command (`python -m kivilcim.risk`), run hourly after ingest, clusters recent detections and computes the risk for each cluster. Point queries (`/weather`, `/risk`) call Open-Meteo on demand through a cache keyed by coordinates rounded to ~0.1° (about 11 km), with a 30-60 minute TTL. Both paths share the same cached weather fetch.
- **Time zones:** The API always uses UTC. The frontend displays times in `Europe/Istanbul`.
- **Tests:** pytest, with fixed fixtures for FIRMS responses
- **Configuration:** API keys live in a `.env` file and never enter the repo

## 6. Data sources

| Source | Used for | Access | Note |
|---|---|---|---|
| NASA FIRMS | Active fire detections | Free, API key | Delayed, has a confidence value |
| Open-Meteo | Wind, humidity, temperature, precipitation (point weather, fire zones and point risk) | Free, no key needed | Free tier is non-commercial only (no ads, no subscriptions) and requires CC BY 4.0 attribution. Limits per IP: 600 per minute, 5,000 per hour, 10,000 per day, 300,000 per month. A request with several locations counts about one call per location (up to 1,000 locations per request; verify with the first real call) |
| Sentinel-2 (Copernicus) | Validation, burned area | Free, registration required | Later stage |

## 7. Data model (draft)

**fire_detections**: `id`, `source` (FIRMS product, e.g. `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `MODIS_NRT`), `satellite`, `instrument` (MODIS / VIIRS), `latitude`, `longitude`, `acquired_at` (satellite observation time, UTC; built from FIRMS `acq_date` + `acq_time`), `confidence_raw` (as delivered), `confidence_level` (normalized: low / nominal / high), `brightness`, `frp` (fire radiative power), `ingested_at`

Notes:
- MODIS delivers confidence as a number (0-100), VIIRS as a category (l / n / h). Both are stored raw and normalized to `confidence_level`.
- Unique constraint on (`source`, `latitude`, `longitude`, `acquired_at`). Ingest windows overlap, so writes are idempotent upserts.

**weather_snapshots** (the cache shared by `/weather` and `/risk`): `id`, `latitude`, `longitude` (rounded to ~0.1°), `fetched_at`, `wind_speed`, `wind_direction`, `humidity`, `temperature`, `precip_recent`. Unique on (`latitude`, `longitude`); a new fetch replaces the old row.

**risk_scores**: `id`, `kind` (`fire_zone` / `point`), `latitude`, `longitude` (rounded), `valid_date`, `computed_at`, `score` (0-100), `level` (low / moderate / high / very_high / extreme), `temperature`, `humidity`, `wind_speed`, `precip_recent` (the inputs, stored next to the score so every result is explainable), `detection_count` (fire zones only), `index_version`. Unique on (`latitude`, `longitude`, `valid_date`, `index_version`); writes are upserts.

**ingest_runs** (health tracking): `id`, `source` (FIRMS products, weather, risk), `started_at`, `finished_at`, `status`, `record_count`, `error`

## 8. API draft (MVP)

- `GET /health`: Is the service up
- `GET /status`: Last successful update time and delay for each source
- `GET /fires?lat=&lon=&radius_km=&since_hours=`: Nearby fire detections with observation time and confidence
- `GET /weather?lat=&lon=`: Simple weather summary for a location
- `GET /risk?lat=&lon=`: Fire danger level for a location (coordinates inside the Turkey bounding box only), with `score`, `level`, the input drivers, `valid_date`, `computed_at` and an `experimental` flag. Computed on demand from cached weather.
- `GET /risk/zones`: Active fire zones with their levels as GeoJSON, for the map layer

Every response includes a `data_as_of` field and a short `disclaimer` field (not an official warning; call 112 / AFAD in an emergency). The frontend shows `data_as_of` to the user.

`data_as_of` carries two separate times, because they answer different questions:
- `last_ingest_at`: when the service last successfully fetched data from FIRMS ("is the service up to date?").
- `latest_observation_at`: the newest satellite observation in the data ("when did a satellite last see the area?"). A real call showed this can be about 11 hours old simply because of satellite pass times, which is normal and not a service failure.

`/fires` implementation note: SQLite has no spatial support, so the query first filters by a bounding box (using an index on `latitude`, `longitude`, `acquired_at`) and then computes exact haversine distance. This is sufficient at the scale of Turkey.

## 9. Risk map (MVP)

**What it is:** a weather-based fire danger index. It answers "how favorable is the current weather for fires here?", not "will a fire happen here?". It is computed only where it is needed, not as a nationwide grid.

**Approach (decided):** rule-based and transparent. No machine learning in the MVP.
- **Two paths, one formula:**
  - **Fire zones (map layer):** recent active detections are clustered by proximity (provisional: detections within ~10 km of each other form one cluster; the zone is drawn as a circle of ~50 km around the cluster center, both to be tuned). One Open-Meteo point per cluster is fetched, in a single multi-location request. The result is shown as a colored zone on the map with its level.
  - **Point query (`/risk`):** the weather for a point (rounded to ~0.1°) is fetched on demand and cached for 30-60 minutes. This is a building block: the web page calls it only for the nearest active detection of the selected area. For a place with no detection the page shows no risk at all, because there is nothing for a fire to spread from, and a general "fire risk" figure for such a place would mislead (decided after testing: a place with no fire for months should not read as a risk percentage).
- **Inputs (Open-Meteo):** temperature, relative humidity, wind speed, recent precipitation.
- **Score:** the inputs are combined into a 0-100 score and a level (low / moderate / high / very high / extreme). The formula is documented in the repo and versioned (`index_version`). Each result keeps its inputs, so the UI can say why ("high: hot, dry, windy, no rain for N days"). Decided: index `v2` is a simplified, transparent formula. Dryness is a weighted average of three factors scaled 0 to 1 with linear ramps: humidity 0.35 (70% to 15%), wind 0.25 (0 to 50 km/h) and dry spell 0.20 (10 mm to 0 mm of rain in the last 72 hours). Temperature is a multiplier, 0 at 5 °C and below and 1 at 30 °C and above, so cold weather keeps the score low even when the air is dry and windy. Levels: under 20 low, 40 moderate, 60 high, 80 very high, above that extreme. The weights and ramps are a reasoned starting point, not calibrated; Stage 4 checks them against past fires. What the first checks showed: with `v1`, which added temperature to the sum, 14 of 81 province centers were "high" on 2 November 2026 at 2-24 °C, so `v2` made temperature a multiplier; `v2` gave 67 low and 14 moderate that day. A sanity check on 2021 weather scored the days of the Manavgat, Marmaris and Milas/Bodrum fires 73-81 (very high or extreme) and winter and spring control days 10-19 (low). It only separates summer from winter, though: an ordinary August midday in Ankara also scored 68.5. In the dry season the index will often be very high, so levels may saturate and thresholds may need to be relative; Stage 4 checks how often high levels coincide with fires. The full Canadian Fire Weather Index (FWI) system is left for after that validation, since it needs daily weather and a few weeks of spin-up history.
- **Refresh:** the zones command (`python -m kivilcim.risk`) runs hourly after ingest, separate from the API. Cluster count is capped per run (provisional: 200).
- **Call quota:** the free tier allows 10,000 calls per day. Worst case for zones is 200 clusters x 24 runs = 4,800 calls per day, and typical use is far lower. Point queries add one call per distinct rounded location per cache window. The code keeps a daily call counter with a safety quota (provisional: 7,000 calls per day; this is a call count, not money, and the free tier costs nothing). When the quota is nearly spent, only cached values are served and the response says so, so the service never gets blocked by Open-Meteo.
- **Abuse protection:** `/risk` and `/weather` reject coordinates outside the Turkey bounding box, so nobody can use the service to scan the world and burn the call quota.

**Honesty rules for the risk index:**
- Labeled "experimental, not yet validated" in the UI and with an `experimental` flag in the API, until the Stage 4 backtest results are published.
- Always shows `valid_date`, `computed_at` and the drivers behind the level.
- Wording is "fire danger level", never "probability of fire". Around an active fire it describes weather conditions, not where or how fast the fire will spread (spread estimation is out of scope until backtested).
- States what it leaves out: vegetation and fuel type, terrain, drought history, ignition sources. Weather model resolution is coarse and local conditions can differ.
- Areas without an active detection show no risk value and no colored zone. That does not mean the area is safe; it only means there is no detection to describe. The text says so.
- If a refresh fails, the last good values are shown and marked stale, same as fire data.
- Open-Meteo is credited in the UI and in the README (CC BY 4.0).

## 10. Data delay and honesty principles

- Each detection is shown with "observed at [time]", never "burning right now".
- The older the data, the more prominent the warning in the interface. Provisional thresholds (to be confirmed), applied to `last_ingest_at`: under 3 hours normal, 3-6 hours yellow warning, over 6 hours red warning. The age of `latest_observation_at` is shown separately as information ("last satellite observation: N hours ago"), since long gaps between satellite passes are normal. "No detections" is always worded together with that time, never as "no fire".
- For false positives, the first MVP release shows low-confidence detections separately (based on `confidence_level`). Flagging recurring fixed hot spots (factories, refineries, etc.) needs history, so it comes in Stage 1.5: a detection is "recurring" if the same ~1 km cell has N or more detections within the last X days (N and X to be decided with real data).
- Model training does not start until enough labeled data has accumulated.

## 11. Backend stages

**Stage 0: Foundation (first goal)**
- Project skeleton, `.gitignore`, `.env` handling, `.env.example`
- FIRMS ingest job: fetch, validate, deduplicate, store
- Record runs in `ingest_runs`
- Tests (with fixed data)

**Stage 1: API**
- `/health`, `/status`, `/fires`
- Query by location and radius
- Tests for error and timestamp behavior

**Stage 1.5: Recurring hot spot flagging**
- Flag fixed heat sources (factories, refineries) using accumulated detection history
- Shown separately in `/fires`, never silently dropped
- Status: flagging recurring hot spots inside `/fires` is still postponed. The history it needs is now available: `python -m kivilcim.ingest --source ... --from ... --to ...` backfills by date (see "Past fires" under Stage 3), and the event finder already removes always-on cells before grouping. N and X for the `/fires` flag are chosen from real data

**Stage 2: Weather and risk index**
- Decide simplified formula vs full Canadian FWI (see section 9)
- Open-Meteo client (multi-location batches, error and rate-limit handling), `weather_snapshots` cache with coordinate rounding and TTL, daily call counter with a safety quota
- `/weather` (Turkey bounding box check)
- Index calculation (versioned), `risk_scores`, `/risk`
- Fire clustering, scheduled zones command (`python -m kivilcim.risk`), `/risk/zones`
- Unit tests with fixed inputs (see section 12)
- Status: the point path is done (`/weather`, `/risk`, cache, daily call quota, the index, tests, shown as a card on the map page). Still to do: fire clustering, the zones command, `/risk/zones` and the map layer. The `risk_scores` table is deferred until zones need to store results; point queries compute the score from the cached weather on demand

**Stage 3: Frontend**
- Map (Leaflet), location input, list view
- Risk zones layer with legend, level and the drivers behind it; "experimental" label; note that no zone does not mean safe
- Open-Meteo attribution
- Responsibility note and data time on every screen
- Status: first slice done (map at `/`, click or device location, radius and time filters, detections and low-confidence ones drawn differently, data freshness banner, permanent disclaimer, Istanbul time). Province and district selection is also done, using approximate center points of 81 provinces and 973 districts from OpenStreetMap data (ODbL, see `data/README.md`). Still to do: risk layer, PWA install
- **Past fires** (`/history`, API `/events` and `/events/{id}`): the satellite archive already shows the big fires of 2026, so the page lists them and shows how one spread. A map of Turkey with a circle per event is the starting view. Choosing an event draws all its detections once, colored by day from yellow (first day) to dark red (last day) and sized by heat power, with a slider, play button and a per-day bar chart; moving the slider only restyles the points, so nothing reloads. A link like `/history#<event id>` opens one directly. A first version showed NASA GIBS true-color imagery under the points. It was dropped: at about 250 m the imagery mostly showed smoke, and swapping the imagery layer on every day made the map flash blank. Sharper 30 m imagery (HLS) exists but only on the days a satellite passes a spot (3 of 25 days in the September Adana example, one of them cloudy), so it cannot be stepped day by day; it stays an option for a separate before and after pair.
  - How events are found (`events.py`): they grow from strong detections (at least 100 MW, which forest fires reach and crop burning rarely does) and then gather the weaker ones within 2 cells (~0.2°) and from 2 days before to 3 days after. Always-on cells (detecting on at least 25% of all days, only when there are 60 or more days of history) are removed first. An event needs at least 15 detections, 500 MW in total and at most 14 days. These thresholds are provisional and came from a scan of June to November 2026. Seeding with strong detections replaced an earlier "chain nearby detections" version, because weeks of weak crop-burning detections bridged real fires into long blobs that were then dropped (the September 2026 Adana fire was lost that way).
  - What it finds: 12 events inside Turkey for June to November 2026, among them Seydikemer/Muğla and Kaş/Antalya, Gömeç/Balıkesir (late July and late August) and the 7-10 September fire in Adana (İmamoğlu, 168 MW). Some June events in the Şanlıurfa and Mardin plains are probably crop fires, not forest fires; the page says so. Place names are the nearest district center and approximate.
  - Data: VIIRS SNPP only for now. Archive product `VIIRS_SNPP_SP` covers older dates (the newest few months are not processed yet) and `VIIRS_SNPP_NRT` keeps roughly the last three months, so the two together cover the period without a gap. The SNPP archive has no data for 11-15 July 2026 (NOAA-20 and MODIS do), so that window is missing; adding `VIIRS_NOAA20_*` would fill it. Backfills are not written to `ingest_runs`, so they cannot affect the freshness shown for the live ingest.
  - Events are computed on each request (about 1 second for 45,000 detections); caching comes later if needed.
  - Not done: the event list and its thresholds are not validated against reported fires. The point is a showcase of what the data catches, with the limits stated on the page.

**Stage 4: Validation and analysis**
- Backtesting on past fires (FIRMS archive + Sentinel-2)
- Risk validation: on past fires, check whether higher index levels coincide with fires that lasted longer or produced more detections in the following days, and whether the index looks sensible on dates and places without fires; tune thresholds and publish the result. The exact method (and which Open-Meteo historical data is available on the free tier) is decided at the start of this stage
- Publishing metrics on a public page
- Spread direction (from consecutive FIRMS detections), only after backtest results exist

**Stage 5: Release**
- Deployment, documentation, public API
- Native app decision, if needed

## 12. Testing and quality

- Ingest job: unit tests with fixed FIRMS responses, plus empty and malformed response cases
- API: boundary values for `/fires` (radius 0, very large radius, invalid coordinates)
- Risk index: unit tests with fixed inputs (hot/dry/windy gives a high level; rain lowers it; zero wind, 0% and 100% humidity, missing values), monotonicity checks (a worse input never lowers the score), fire clustering (no detections, one detection, two close clusters, a cluster cap), the call quota guard (serves cached values when spent), cache TTL and coordinate rounding, and `/risk` and `/weather` for a point outside the Turkey bounding box (rejected)
- Timestamps are always UTC and clearly labeled in responses
- On failure, the last successful data is returned and `data_as_of` shows it is stale

## 13. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Risk index read as a prediction, or "no zone" read as "safe" | False sense of safety or panic | "Fire danger level" wording, `experimental` label until validated, drivers and limits shown, explicit note that no zone does not mean safe |
| Unvalidated risk index | Wrong levels erode trust | Stage 4 backtest, versioned formula, published results |
| FIRMS delay | Users think old data is current | `data_as_of` in every response, clear warning in the UI |
| False positives | Loss of trust | Fixed-source filter, confidence value, separate flagging |
| Misunderstood responsibility | Legal and ethical risk | Clear warning on every screen, 112/AFAD directions |
| API rate limits (FIRMS, Open-Meteo) | Service outage | Caching with coordinate rounding, reasonable intervals, daily call quota with a cached-only fallback, Turkey bounding box check on point queries |
| Open-Meteo free tier is non-commercial only | Forced to pay or lose weather data if ads, subscriptions or a paid API are added | Keep the service free and ad-free for now; a paid plan would be needed before any monetization; attribution (CC BY 4.0) is shown |
| Scope creep (the risk map grew the MVP) | Project never finishes | Stick to the out-of-scope list in this file; ship Stage 0-1 before Stage 2 |

## 14. Decisions

- Target area: all of Turkey. Ingest uses a Turkey bounding box with no polygon filtering, so detections just across the border may appear (known and accepted for the MVP). A `region` field can be added later if the scope grows.
- Location input: the backend API takes only latitude/longitude. The province/district to coordinate mapping is handled in the frontend (Stage 3) with static data.
- Risk index: part of the MVP. Rule-based, weather-only. Shown only around active detections (the page asks for the nearest one; clustered zones come later), never as a general risk for a place without a detection. No nationwide grid and no machine learning in the MVP. Labeled experimental until the Stage 4 backtest.
- Open-Meteo: used under the free non-commercial terms with attribution; the project stays free and ad-free for now.
- Place data: province/district center points come from OpenStreetMap (ODbL 1.0), kept in `data/` and `static/ilceler.json` with attribution. The code stays MIT; the ODbL data files are separate. They are approximate centers, not official boundaries.
- License: MIT (see `LICENSE`).
- Documentation: this file is bilingual; any change must be made in both the English and Turkish sections together.

## 15. Next step

Stage 0 is done (skeleton, models, FIRMS parser, ingest job with deduplication and run tracking, tests). Stage 1 is done (`/health`, `/status`, `/fires` with radius and time filters, freshness levels, tests). Next is Stage 1.5 (recurring hot spots) or, if the risk index is prioritized, Stage 2. The risk index (Stage 2) follows the working fire API (Stages 0-1).

---

# Türkçe

## 1. Vizyon

Kıvılcım, NASA FIRMS uydu verisini kullanarak bir konum için **"yakınımda aktif yangın var mı, veri ne kadar taze, bugün bölgemde yangın havası ne kadar tehlikeli"** sorusuna dürüst ve anlaşılır bir cevap veren bir servistir.

Son kısım, deneysel, kural tabanlı ve hava durumuna dayalı bir yangın tehlike indeksiyle yanıtlanır: havanın yangınlar için ne kadar elverişli olduğunu, haritada aktif yangınların çevresinde ve kullanıcının sorguladığı her konum için gösterir. Yangın çıkacağının tahmini değildir.

Kıvılcım bir resmi uyarı sistemi değildir. Her ekranda ve her API yanıtında bu açıkça belirtilir ve acil durumda 112 ile AFAD'a yönlendirilir.

## 2. Hedef kullanıcı

- **Birincil:** Bulunduğu bölgede yangın olup olmadığını merak eden genel kullanıcı.
- **İkincil (ilerleyen aşama):** Veriye API üzerinden erişmek isteyen geliştiriciler ve küçük kurumlar.

## 3. MVP kapsamı

MVP'de yer alanlar:

1. Kullanıcı bir konum seçer. Backend yalnızca enlem/boylam alır; il/ilçe seçimi arayüzde koordinata çevrilir.
2. Belirli yarıçap içinde son gözlemlenen aktif yangın noktaları listelenir ve haritada gösterilir.
3. Her noktanın yanında **gözlem zamanı, güven değeri ve uydu kaynağı** yazar.
4. Konum için basit bir hava özeti gösterilir (rüzgar, nem, sıcaklık).
5. Her sayfada sorumluluk notu bulunur (resmi uyarı değildir, veri gecikmeli olabilir).
6. Veri çekme işi düzenli çalışır ve son başarılı güncelleme zamanı API'de görünür.
7. **Aktif yangınların çevresinde yayılma elverişlilik indeksi**: aktif bir yangın tespitindeki güncel hava durumundan hesaplanan kural tabanlı bir indeks. Havanın yangının sürmesi ve yayılması için ne kadar elverişli olduğunu nedenleriyle söyler. Yalnızca aktif tespit olan yerde gösterilir; tespiti olmayan bir yer için genel bir "yangın riski" olarak asla gösterilmez. Yayılma tahmini ya da olasılık değildir ve doğrulanana kadar deneysel olarak etiketlenir (bkz. bölüm 9).

MVP'de **olmayanlar** (bilinçli olarak dışarıda):

- Yayılım yönü ve hız tahmini (sonraki aşama, geri test yapılmadan gösterilmez)
- Makine öğrenmesi tabanlı risk ya da çıkış olasılığı modelleri; bitki örtüsü, arazi ya da kuraklık geçmişine dayalı risk (MVP risk indeksi yalnızca hava verisine dayanır ve kural tabanlıdır)
- Ülke geneli risk grid'i. MVP riski yalnızca aktif yangınların çevresi ve kullanıcının sorguladığı konumlar için hesaplar; gerekirse grid sonradan eklenebilir
- Uydu görüntüsü ve yanmış alan analizi
- Kullanıcı girdisi (crowd bildirimleri)
- Müdahale bilgisi (helikopter, ekip vb.): bunu biz üretemeyiz, resmi kaynak takibi ayrı bir karardır
- Native mobil uygulama
- Kullanıcı hesabı ve bildirimler (MVP'de konumlar tarayıcıda saklanır)

## 4. Mobil mi, web mi?

**Karar: MVP web tabanlı ve mobil uyumlu olacak (responsive, PWA olarak kurulabilir).**

Gerekçeler:
- Tek kod tabanı, App Store / Play Store süreci yok.
- Değerin büyük kısmı backend ve veri tarafında; arayüz sonradan değiştirilebilir.
- Ana özellik (harita + konum + liste) tarayıcıda iyi çalışır.

Native uygulama şu durumlarda düşünülür: push bildirimlerin ciddi bir ihtiyaç haline gelmesi ya da arka planda konum takibi gerekmesi.

## 5. Mimari

```
[FIRMS API] ──► [Ingest işi (zamanlanmış)] ──► [Veritabanı]
[Open-Meteo] ──► [Risk bölgeleri işi (zamanlanmış)] ──┘    │
                                                      ▼
                                              [REST API (FastAPI)]
                                                      │
                                                      ▼
                                          [Web arayüzü (sonra)]
```

Teknoloji seçimi (MVP için önerilen):
- **Dil ve çatı:** Python, FastAPI
- **Veritabanı:** SQLite ile başla, veri büyüyünce PostgreSQL + PostGIS'e geç
- **Zamanlama:** Ingest işi bağımsız bir komuttur (`python -m kivilcim.ingest`) ve dışarıdan bir zamanlayıcı (işletim sistemi görev zamanlayıcısı, cron, ileride container) tarafından çağrılır. API sürecinden ayrı tutulur; test etmek ve yeniden başlatmak kolay olur.
- **Ingest sıklığı:** 15-30 dakikada bir ile başla, yakın zaman penceresini çek ve tekrar kaydı dedup ile engelle. Gerçek çağrıyla doğrulandı: FIRMS limiti 10 dakikada 5.000 işlemdir, bu yüzden aralığı kısıtlamaz. `day_range` üst sınırı istek başına 5 gündür (6 ve üstü HTTP 400 döner). Daha eski veri için başlangıç tarihi parametresi gerekir; ihtiyaç olunca doğrulanacak.
- **FIRMS kaynakları:** `VIIRS_SNPP_NRT` ve `MODIS_NRT` ile başla. `VIIRS_NOAA20_NRT` ve `VIIRS_NOAA21_NRT` erken eklenmeli; her uydu farklı saatlerde geçer ve birlikte daha sık gözlem sağlarlar.
- **Risk güncelleme:** Bağımsız bir komut (`python -m kivilcim.risk`) ingest'ten sonra saatlik çalışır, son tespitleri kümeler ve her küme için riski hesaplar. Nokta sorguları (`/weather`, `/risk`) Open-Meteo'yu istek anında, ~0,1°'ye (yaklaşık 11 km) yuvarlanmış koordinat anahtarlı ve 30-60 dakika TTL'li bir önbellek üzerinden çağırır. İki yol aynı önbellekteki hava verisini paylaşır.
- **Zaman dilimi:** API her zaman UTC kullanır. Arayüz saatleri `Europe/Istanbul` olarak gösterir.
- **Testler:** pytest, FIRMS yanıtları için sabit (fixture) veriler
- **Konfigürasyon:** API anahtarı `.env` dosyasında, repo'ya girmez

## 6. Veri kaynakları

| Kaynak | Ne için | Erişim | Not |
|---|---|---|---|
| NASA FIRMS | Aktif yangın noktaları | Ücretsiz, API anahtarı | Gecikmeli, güven değeri var |
| Open-Meteo | Rüzgar, nem, sıcaklık, yağış (nokta hava durumu, yangın bölgeleri ve nokta riski) | Ücretsiz, anahtar gerekmez | Ücretsiz sürüm yalnızca ticari olmayan kullanım içindir (reklam ve abonelik yok) ve CC BY 4.0 atfı ister. IP başına limitler: dakikada 600, saatte 5.000, günde 10.000, ayda 300.000. Birden çok konumlu istek konum başına yaklaşık 1 çağrı sayılır (istek başına en fazla 1.000 konum; ilk gerçek çağrıda doğrula) |
| Sentinel-2 (Copernicus) | Doğrulama, yanmış alan | Ücretsiz, kayıt gerekir | Sonraki aşama |

## 7. Veri modeli (taslak)

**fire_detections**: `id`, `source` (FIRMS ürünü, ör. `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `MODIS_NRT`), `satellite`, `instrument` (MODIS / VIIRS), `latitude`, `longitude`, `acquired_at` (uydu gözlem zamanı, UTC; FIRMS `acq_date` + `acq_time` alanlarından üretilir), `confidence_raw` (geldiği haliyle), `confidence_level` (normalize: low / nominal / high), `brightness`, `frp` (radyatif güç), `ingested_at`

Notlar:
- MODIS güveni sayı (0-100), VIIRS kategori (l / n / h) olarak verir. İkisi de ham haliyle saklanır ve `confidence_level`'a normalize edilir.
- (`source`, `latitude`, `longitude`, `acquired_at`) üzerinde unique kısıt vardır. Ingest pencereleri çakışacağı için yazma işlemi idempotent upsert'tir.

**weather_snapshots** (`/weather` ve `/risk`'in paylaştığı önbellek): `id`, `latitude`, `longitude` (~0,1°'ye yuvarlanmış), `fetched_at`, `wind_speed`, `wind_direction`, `humidity`, `temperature`, `precip_recent`. (`latitude`, `longitude`) üzerinde unique; yeni çekim eski satırın yerini alır.

**risk_scores**: `id`, `kind` (`fire_zone` / `point`), `latitude`, `longitude` (yuvarlanmış), `valid_date`, `computed_at`, `score` (0-100), `level` (low / moderate / high / very_high / extreme), `temperature`, `humidity`, `wind_speed`, `precip_recent` (girdiler skorun yanında saklanır, böylece her sonuç açıklanabilir), `detection_count` (yalnızca yangın bölgeleri), `index_version`. (`latitude`, `longitude`, `valid_date`, `index_version`) üzerinde unique; yazmalar upsert'tir.

**ingest_runs** (sağlık takibi): `id`, `source` (FIRMS ürünleri, hava, risk), `started_at`, `finished_at`, `status`, `record_count`, `error`

## 8. API taslağı (MVP)

- `GET /health`: Servis ayakta mı
- `GET /status`: Her kaynak için son başarılı güncelleme zamanı ve gecikme
- `GET /fires?lat=&lon=&radius_km=&since_hours=`: Yakın yangın noktaları, gözlem zamanı ve güven değeriyle
- `GET /weather?lat=&lon=`: Konum için basit hava özeti
- `GET /risk?lat=&lon=`: Bir konum için yangın tehlike seviyesi (yalnızca Türkiye bounding box'ı içindeki koordinatlar); `score`, `level`, girdi etkenleri, `valid_date`, `computed_at` ve `experimental` bayrağı ile. Önbellekteki hava verisinden istek anında hesaplanır.
- `GET /risk/zones`: Harita katmanı için aktif yangın bölgeleri ve seviyeleri, GeoJSON olarak

Her yanıtta `data_as_of` alanı ve kısa bir `disclaimer` alanı bulunur (resmi uyarı değildir; acil durumda 112 / AFAD). Arayüz `data_as_of` alanını kullanıcıya gösterir.

`data_as_of` iki ayrı zaman taşır, çünkü farklı sorulara cevap verirler:
- `last_ingest_at`: servisin FIRMS'ten en son ne zaman başarıyla veri çektiği ("servis güncel mi?").
- `latest_observation_at`: verideki en yeni uydu gözlemi ("bir uydu bölgeyi en son ne zaman gördü?"). Gerçek bir çağrıda bunun sadece uydu geçiş saatleri yüzünden yaklaşık 11 saat eski olabildiği görüldü; bu normaldir, servis arızası değildir.

`/fires` uygulama notu: SQLite'ta mekansal destek yoktur. Sorgu önce bounding box ile (`latitude`, `longitude`, `acquired_at` indeksiyle) süzer, sonra kesin haversine mesafesini hesaplar. Türkiye ölçeğinde bu yeterlidir.

## 9. Risk indeksi (MVP)

**Nedir:** Hava durumuna dayalı bir yangın tehlike indeksi. "Güncel hava burada yangınlar için ne kadar elverişli?" sorusunu yanıtlar; "burada yangın çıkacak mı?" sorusunu yanıtlamaz. Ülke geneli bir grid olarak değil, yalnızca gerektiği yerde hesaplanır.

**Yaklaşım (karar verildi):** kural tabanlı ve şeffaf. MVP'de makine öğrenmesi yok.
- **İki yol, tek formül:**
  - **Yangın bölgeleri (harita katmanı):** son aktif tespitler yakınlığa göre kümelenir (geçici: birbirine ~10 km içindeki tespitler tek küme olur; bölge, küme merkezi çevresinde ~50 km'lik daire olarak çizilir, ikisi de ayarlanacak). Her küme için bir Open-Meteo noktası, tek bir çok konumlu istekle çekilir. Sonuç haritada seviyesiyle renkli bölge olarak gösterilir.
  - **Nokta sorgusu (`/risk`):** bir noktanın (~0,1°'ye yuvarlanmış) hava verisi istek anında çekilir ve 30-60 dakika önbelleğe alınır. Bu bir yapı taşıdır: web sayfası bunu yalnızca seçilen alandaki en yakın aktif tespit için çağırır. Tespiti olmayan bir yer için sayfa hiç risk göstermez, çünkü yangının yayılacağı bir kaynak yoktur ve böyle bir yer için genel bir "yangın riski" değeri yanıltıcı olur (testten sonra karar verildi: aylardır yangın olmayan bir yer yüzde olarak risk gibi okunmamalı).
- **Girdiler (Open-Meteo):** sıcaklık, bağıl nem, rüzgar hızı, yakın zamandaki yağış.
- **Skor:** Girdiler 0-100 arası bir skora ve bir seviyeye (low / moderate / high / very high / extreme) dönüştürülür. Formül repoda belgelenir ve sürümlenir (`index_version`). Her sonuç girdileriyle birlikte saklanır; arayüz nedenini söyleyebilir ("yüksek: sıcak, kuru, rüzgarlı, N gündür yağış yok"). Karar: `v2` indeksi sadeleştirilmiş, şeffaf bir formüldür. Kuruluk, doğrusal rampalarla 0-1 arasına ölçeklenen üç etkenin ağırlıklı ortalamasıdır: nem 0,35 (%70'ten %15'e), rüzgar 0,25 (0-50 km/sa) ve kurak dönem 0,20 (son 72 saatte 10 mm'den 0 mm'ye yağış). Sıcaklık bir çarpandır: 5 °C ve altında 0, 30 °C ve üstünde 1; böylece hava kuru ve rüzgarlı olsa bile soğuk hava skoru düşük tutar. Seviyeler: 20'nin altı low, 40 moderate, 60 high, 80 very high, üstü extreme. Ağırlıklar ve rampalar gerekçeli bir başlangıç noktasıdır, kalibre edilmemiştir; Aşama 4 bunları geçmiş yangınlarla kontrol eder. İlk kontrollerin gösterdikleri: sıcaklığı toplama ekleyen `v1` ile 2 Kasım 2026'da 2-24 °C'de 81 il merkezinin 14'ü "yüksek" çıktı, bu yüzden `v2`'de sıcaklık çarpan oldu; `v2` o gün 67 düşük ve 14 orta verdi. 2021 hava verisiyle yapılan mantık kontrolü, Manavgat, Marmaris ve Milas/Bodrum yangın günlerini 73-81 (çok yüksek ya da aşırı), kış ve ilkbahar kontrol günlerini 10-19 (düşük) puanladı. Ama bu yalnızca yazı kıştan ayırıyor: Ankara'da sıradan bir Ağustos öğlesi de 68,5 aldı. Kurak mevsimde indeks sık sık çok yüksek olacak; seviyeler doyabilir ve eşiklerin göreli olması gerekebilir. Aşama 4, yüksek seviyelerin ne sıklıkla yangınla örtüştüğünü kontrol eder. Tam Kanada Yangın Hava İndeksi (FWI) sistemi, günlük hava verisi ve birkaç haftalık ısınma verisi istediği için bu doğrulamadan sonraya bırakıldı.
- **Güncelleme:** Bölgeler komutu (`python -m kivilcim.risk`) ingest'ten sonra saatlik çalışır, API'den ayrıdır. Çalıştırma başına küme sayısı sınırlanır (geçici: 200).
- **Çağrı kotası:** Ücretsiz sürüm günde 10.000 çağrıya izin verir. Bölgeler için en kötü durum 200 küme x 24 çalıştırma = günde 4.800 çağrıdır; tipik kullanım çok daha düşüktür. Nokta sorguları, önbellek penceresi başına her ayrı yuvarlanmış konum için bir çağrı ekler. Kod günlük bir çağrı sayacı ve güvenlik kotası tutar (geçici: günde 7.000 çağrı; bu bir çağrı sayısıdır, para değil, ücretsiz sürüm hiçbir şeye mal olmaz). Kota dolmaya yaklaşınca yalnızca önbellekteki değerler sunulur ve yanıt bunu belirtir; böylece servis Open-Meteo tarafından engellenmez.
- **Kötüye kullanım koruması:** `/risk` ve `/weather` Türkiye bounding box'ı dışındaki koordinatları reddeder; kimse servisi dünyayı taramak ve çağrı kotasını tüketmek için kullanamaz.

**Risk indeksi için dürüstlük kuralları:**
- Aşama 4 geri test sonuçları yayımlanana kadar arayüzde "deneysel, henüz doğrulanmadı" olarak etiketlenir; API'de `experimental` bayrağı vardır.
- Her zaman `valid_date`, `computed_at` ve seviyenin arkasındaki etkenler gösterilir.
- İfade "yangın tehlike seviyesi"dir, hiçbir zaman "yangın olasılığı" değildir. Aktif bir yangının çevresinde hava koşullarını anlatır; yangının nereye ve ne hızla yayılacağını anlatmaz (yayılım tahmini geri test yapılana kadar kapsam dışıdır).
- Neleri kapsamadığı söylenir: bitki örtüsü ve yakıt tipi, arazi, kuraklık geçmişi, tutuşturucu kaynaklar. Hava modeli çözünürlüğü kabadır, yerel koşullar farklı olabilir.
- Aktif tespit olmayan yerlerde risk değeri ve renkli bölge gösterilmez. Bu, bölgenin güvenli olduğu anlamına gelmez; yalnızca anlatılacak bir tespit olmadığı anlamına gelir. Metin bunu söyler.
- Güncelleme başarısız olursa son iyi değerler gösterilir ve eski olarak işaretlenir (yangın verisindeki gibi).
- Open-Meteo arayüzde ve README'de anılır (CC BY 4.0).

## 10. Veri gecikmesi ve dürüstlük ilkeleri

- Her nokta "şu saatte gözlemlendi" bilgisiyle gösterilir, "şu an yanıyor" denmez.
- Veri ne kadar eskiyse arayüzde o kadar belirgin uyarı verilir. Geçici eşikler (onaylanacak), `last_ingest_at`'e uygulanır: 3 saatten az normal, 3-6 saat sarı uyarı, 6 saatten fazla kırmızı uyarı. `latest_observation_at`'in yaşı ayrıca bilgi olarak gösterilir ("son uydu gözlemi: N saat önce"), çünkü uydu geçişleri arasındaki uzun boşluklar normaldir. "Tespit yok" her zaman bu zamanla birlikte yazılır, hiçbir zaman "yangın yok" denmez.
- Yanlış pozitif riski için MVP'nin ilk sürümünde düşük güvenli tespitler (`confidence_level`'a göre) ayrı gösterilir. Tekrarlayan sabit sıcak noktaların (fabrika, rafineri vb.) işaretlenmesi geçmiş veri gerektirdiği için Aşama 1.5'te yapılır: aynı ~1 km'lik hücrede son X gün içinde N ve üzeri tespit varsa "tekrarlayan" sayılır (N ve X gerçek veriyle belirlenir).
- Model eğitimi, yeterli etiketli veri birikmeden yapılmaz.

## 11. Backend aşamaları

**Aşama 0: Temel (ilk hedef)**
- Proje iskeleti, `.gitignore`, `.env` yönetimi, `.env.example`
- FIRMS ingest işi: çek, doğrula, tekrar kaydı engelle, kaydet
- `ingest_runs` ile kayıt altına al
- Testler (sabit veriyle)

**Aşama 1: API**
- `/health`, `/status`, `/fires`
- Konuma ve yarıçapa göre sorgu
- Hata ve zaman damgası davranışının testleri

**Aşama 1.5: Tekrarlayan sıcak nokta işaretleme**
- Biriken tespit geçmişiyle sabit ısı kaynaklarını (fabrika, rafineri) işaretle
- `/fires` içinde ayrı gösterilir, sessizce silinmez
- Durum: `/fires` içinde tekrarlayan sıcak noktaları işaretleme hâlâ ertelendi. İhtiyaç duyduğu geçmiş artık mevcut: `python -m kivilcim.ingest --source ... --from ... --to ...` tarihe göre geri doldurur (bkz. Aşama 3 altındaki "Geçmiş yangınlar") ve olay bulucu, gruplamadan önce sürekli ateşli hücreleri zaten çıkarıyor. `/fires` işareti için N ve X gerçek veriden seçilir

**Aşama 2: Hava ve risk indeksi**
- Sadeleştirilmiş formül mü tam Kanada FWI mı karara bağla (bkz. bölüm 9)
- Open-Meteo istemcisi (çok konumlu partiler, hata ve limit yönetimi), koordinat yuvarlama ve TTL'li `weather_snapshots` önbelleği, güvenlik kotalı günlük çağrı sayacı
- `/weather` (Türkiye bounding box kontrolüyle)
- İndeks hesabı (sürümlü), `risk_scores`, `/risk`
- Yangın kümeleme, zamanlanmış bölgeler komutu (`python -m kivilcim.risk`), `/risk/zones`
- Sabit girdilerle birim testleri (bkz. bölüm 12)
- Durum: nokta yolu tamam (`/weather`, `/risk`, önbellek, günlük çağrı kotası, indeks, testler; harita sayfasında kart olarak gösteriliyor). Kalanlar: yangın kümeleme, bölgeler komutu, `/risk/zones` ve harita katmanı. `risk_scores` tablosu, bölgelerin sonuç saklaması gerekene kadar ertelendi; nokta sorguları skoru önbellekteki havadan istek anında hesaplar

**Aşama 3: Arayüz**
- Harita (Leaflet), konum girişi, liste görünümü
- Lejandlı risk bölgeleri katmanı, seviye ve arkasındaki etkenler; "deneysel" etiketi; bölge yok demek güvenli demek değildir notu
- Open-Meteo atfı
- Sorumluluk notu ve veri zamanı her ekranda
- Durum: ilk dilim tamam (`/` adresinde harita, tıklayarak ya da cihaz konumuyla seçim, yarıçap ve zaman filtreleri, normal ve düşük güvenli tespitlerin farklı çizimi, veri tazelik bandı, sabit sorumluluk notu, İstanbul saati). İl ve ilçe seçimi de tamam; OpenStreetMap verisinden 81 il ve 973 ilçenin yaklaşık merkez noktaları kullanılıyor (ODbL, bkz. `data/README.md`). Kalanlar: risk katmanı, PWA kurulumu
- **Geçmiş yangınlar** (`/history`, API `/events` ve `/events/{id}`): uydu arşivi 2026'nın büyük yangınlarını zaten gösteriyor; sayfa bunları listeler ve birinin nasıl yayıldığını gösterir. Başlangıç görünümü, her olay için bir daire olan Türkiye haritasıdır. Bir olay seçilince tüm tespitleri bir kez çizilir; gün gün sarıdan (ilk gün) koyu kırmızıya (son gün) renklenir, büyüklükleri ısı gücüne göredir; kaydırıcı, oynat düğmesi ve günlük tespit çubuk grafiği vardır. Kaydırıcı yalnızca noktaların stilini değiştirir, hiçbir şey yeniden yüklenmez. `/history#<olay kodu>` gibi bir bağlantı doğrudan bir olayı açar. İlk sürüm noktaların altında NASA GIBS gerçek renk uydu görüntüsü gösteriyordu. Bu kaldırıldı: yaklaşık 250 m'de görüntü çoğunlukla duman gösterdi ve her gün görüntü katmanını değiştirmek haritanın boş yanıp sönmesine yol açtı. Daha keskin 30 m görüntü (HLS) var ama yalnızca uydunun bir noktadan geçtiği günlerde (Eylül Adana örneğinde 25 günün 3'ü, biri bulutlu); bu yüzden gün gün gezdirilemez, ayrı bir öncesi ve sonrası çifti için seçenek olarak kalır.
  - Olaylar nasıl bulunuyor (`events.py`): güçlü tespitlerden (en az 100 MW; orman yangınları buna ulaşır, anız yakma nadiren) büyürler, sonra 2 hücre (~0,2°) içindeki ve 2 gün öncesinden 3 gün sonrasına kadar olan zayıf tespitleri toplarlar. Sürekli ateşli hücreler (tüm günlerin en az %25'inde tespit veren; yalnızca 60 ya da daha fazla günlük geçmiş varsa) önce çıkarılır. Bir olay en az 15 tespit, toplam 500 MW ve en fazla 14 gün ister. Bu eşikler geçicidir ve Haziran-Kasım 2026 taramasından geldi. Güçlü tespitlerle başlamak, önceki "yakın tespitleri zincirle" sürümünün yerini aldı; çünkü haftalarca süren zayıf anız tespitleri gerçek yangınları uzun kütlelere köprüledi ve bunlar elendi (Eylül 2026 Adana yangını böyle kaybolmuştu).
  - Ne buluyor: Haziran-Kasım 2026 için Türkiye içinde 12 olay; aralarında Seydikemer/Muğla ve Kaş/Antalya, Gömeç/Balıkesir (Temmuz sonu ve Ağustos sonu) ve Adana'da 7-10 Eylül yangını (İmamoğlu, 168 MW). Şanlıurfa ve Mardin ovalarındaki bazı Haziran olayları büyük olasılıkla orman değil tarla yangınıdır; sayfa bunu söylüyor. Yer adları en yakın ilçe merkezidir ve yaklaşıktır.
  - Veri: şimdilik yalnızca VIIRS SNPP. Arşiv ürünü `VIIRS_SNPP_SP` eski tarihleri kapsar (en yeni birkaç ay henüz işlenmemiştir), `VIIRS_SNPP_NRT` yaklaşık son üç ayı tutar; ikisi birlikte dönemi boşluksuz kapsar. SNPP arşivinde 11-15 Temmuz 2026 için veri yok (NOAA-20 ve MODIS'te var), bu pencere eksik; `VIIRS_NOAA20_*` eklemek bunu doldurur. Geri doldurmalar `ingest_runs`'a yazılmaz, böylece canlı ingest için gösterilen tazeliği etkileyemezler.
  - Olaylar her istekte hesaplanır (45.000 tespitte yaklaşık 1 saniye); gerekirse önbellek sonra gelir.
  - Yapılmayan: olay listesi ve eşikleri bildirilen yangınlarla doğrulanmadı. Amaç, verinin neyi yakaladığının bir vitrini; sınırlar sayfada yazıyor.

**Aşama 4: Doğrulama ve analiz**
- Geçmiş yangınlarla geri test (FIRMS arşivi + Sentinel-2)
- Risk doğrulaması: geçmiş yangınlarda yüksek indeks seviyelerinin daha uzun süren ya da sonraki günlerde daha çok tespit üreten yangınlarla örtüşüp örtüşmediğine, ayrıca yangın olmayan tarih ve yerlerde indeksin mantıklı görünüp görünmediğine bak; eşikleri ayarla ve sonucu yayımla. Kesin yöntem (ve ücretsiz sürümde hangi Open-Meteo geçmiş verisinin kullanılabildiği) bu aşamanın başında belirlenir
- Metrikleri herkese açık bir sayfada yayımlama
- Yayılım yönü (ardışık FIRMS geçişlerinden), yalnızca geri test sonuçları olduktan sonra

**Aşama 5: Yayın**
- Deploy, dokümantasyon, public API
- Gerekirse native uygulama kararı

## 12. Test ve kalite

- Ingest işi: sabit FIRMS yanıtıyla birim testi, boş ve hatalı yanıt senaryoları
- API: `/fires` için sınır değerleri (yarıçap 0, çok büyük yarıçap, geçersiz koordinat)
- Risk indeksi: sabit girdilerle birim testleri (sıcak/kuru/rüzgarlı yüksek seviye verir; yağış düşürür; sıfır rüzgar, %0 ve %100 nem, eksik değerler), monotonluk kontrolleri (kötüleşen bir girdi skoru asla düşürmez), yangın kümeleme (tespit yok, tek tespit, iki yakın küme, küme sınırı), çağrı kotası koruması (kota dolunca önbellekten sunar), önbellek TTL'i ve koordinat yuvarlama, ve Türkiye bounding box'ı dışındaki bir nokta için `/risk` ve `/weather` (reddedilir)
- Zaman damgaları her zaman UTC, yanıtta açıkça belirtilir
- Hata durumunda son başarılı veri döner ve `data_as_of` eski olduğunu gösterir

## 13. Riskler

| Risk | Etki | Önlem |
|---|---|---|
| Risk indeksinin tahmin sanılması ya da "bölge yok"un "güvenli" sanılması | Yanlış güvenlik hissi ya da panik | "Yangın tehlike seviyesi" ifadesi, doğrulanana kadar `experimental` etiketi, etkenlerin ve sınırların gösterilmesi, bölge yok demek güvenli demek değildir notu |
| Doğrulanmamış risk indeksi | Yanlış seviyeler güveni zedeler | Aşama 4 geri testi, sürümlü formül, yayımlanan sonuçlar |
| FIRMS gecikmesi | Kullanıcı eski veriyi güncel sanar | `data_as_of` her yanıtta, arayüzde belirgin uyarı |
| Yanlış pozitifler | Güven kaybı | Sabit kaynak filtresi, güven değeri, ayrı işaretleme |
| Yanlış anlaşılan sorumluluk | Hukuki ve etik risk | Her ekranda net uyarı, 112/AFAD yönlendirmesi |
| API kullanım limiti (FIRMS, Open-Meteo) | Servis durması | Koordinat yuvarlamalı önbellek, makul aralıklar, önbellekten sunma yedekli günlük çağrı kotası, nokta sorgularında Türkiye bounding box kontrolü |
| Open-Meteo ücretsiz sürümü yalnızca ticari olmayan kullanım içindir | Reklam, abonelik ya da ücretli API eklenirse ödeme zorunluluğu ya da hava verisini kaybetme | Servisi şimdilik ücretsiz ve reklamsız tut; para kazanmadan önce ücretli plan gerekir; atıf (CC BY 4.0) gösterilir |
| Kapsam büyümesi (risk indeksi MVP'yi büyüttü) | Proje bitmez | Bu dosyadaki MVP dışı listesine sadık kalmak; Aşama 2'den önce Aşama 0-1'i bitirmek |

## 14. Kararlar

- Hedef bölge: Türkiye tamamı. Ingest Türkiye bounding box'ı ile yapılır, poligon filtresi yoktur; sınırın hemen ötesindeki tespitler görünebilir (MVP için bilinen ve kabul edilen kusur). Kapsam büyürse sonradan `region` alanı eklenebilir.
- Konum girişi: Backend API yalnızca enlem/boylam alır. İl/ilçe → koordinat eşlemesi arayüzde (Aşama 3) statik veriyle çözülür.
- Risk indeksi: MVP'nin parçası. Kural tabanlı, yalnızca hava verisine dayanır. Yalnızca aktif tespitlerin çevresinde gösterilir (sayfa en yakın olanı sorar; kümelenmiş bölgeler sonra gelecek), tespiti olmayan bir yer için genel risk olarak asla gösterilmez. MVP'de ülke geneli grid ve makine öğrenmesi yok. Aşama 4 geri testine kadar deneysel olarak etiketlenir.
- Open-Meteo: ücretsiz ticari olmayan koşullarla ve atıfla kullanılır; proje şimdilik ücretsiz ve reklamsız kalır.
- Yer verisi: il/ilçe merkez noktaları OpenStreetMap'ten (ODbL 1.0) gelir, atıfla birlikte `data/` ve `static/ilceler.json` içinde tutulur. Kod MIT kalır; ODbL veri dosyaları ayrıdır. Bunlar yaklaşık merkezlerdir, resmi sınır değildir.
- Lisans: MIT (bkz. `LICENSE`).
- Dokümantasyon: Bu dosya iki dillidir; her değişiklik İngilizce ve Türkçe bölümlerde birlikte yapılır.

## 15. Sonraki adım

Aşama 0 tamamlandı (iskelet, modeller, FIRMS ayrıştırıcı, tekrarsız yazan ve çalıştırmaları kaydeden ingest işi, testler). Aşama 1 tamamlandı (`/health`, `/status`, `/fires`; yarıçap ve zaman filtreleri, tazelik seviyeleri, testler). Sıradaki Aşama 1.5 (tekrarlayan sıcak noktalar) ya da risk indeksi öncelikliyse Aşama 2. Risk indeksi (Aşama 2), çalışan yangın API'sinden (Aşama 0-1) sonra gelir.
