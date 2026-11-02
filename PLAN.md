# Kıvılcım: Tasarım ve Plan / Design and Plan

> Bu dosya iki dilli: önce İngilizce, sonra Türkçe. Her iki bölüm de aynı içeriği taşır; değişiklikler ikisinde birlikte yapılır.
> This file is bilingual: English first, then Turkish. Both sections carry the same content; changes are made in both together.
>
> Durum / Status: sürüm / version 0.7.0 (the plan below describes what was built and why, then what is left).

---

# English

## 1. Vision and honesty

Kıvılcım uses NASA FIRMS satellite data to answer, for a given place, **"is there a detected fire near me, and how fresh is the data?"**, and around active detections to say how favorable the weather is for fire.

It is **not an official warning system**. That is stated on every page and in every API response, and emergencies are directed to 112 and AFAD (Turkey). The service never says "burning right now" (a detection is an observation moment), never says "no fire" (no detection is not safety), and never shows a figure for a place that has no detection.

## 2. Users

- **Primary:** people who want to know whether a fire has been detected near a place.
- **Secondary:** developers and small organizations using the free, read-only API.

## 3. Scope

**Built (v0.1.0, front end redone in v0.2.0):**

1. Choose a place (province, district, map click, device location, shareable link). The backend takes only latitude and longitude.
2. Recent detections within a radius, on a map and in a list, each with observation time, confidence, satellite and distance.
3. A Turkey-wide map of **active zones** (the last day's detections grouped by place), each with the weather index at its center.
4. A weather card for the chosen place (temperature, humidity, wind, rain in the last 72 hours).
5. A responsibility note everywhere, and data freshness as two separate times.
6. Automatic, repeated ingest with a visible record; one command runs everything.
7. Always-on heat sources (factories, flares) flagged, never dropped.
8. **Past fires:** big fires found in the archive, each with the satellite image before and after (NASA GIBS, 30 m where a clear day exists) and a day-by-day spread map.
9. A methodology page with the formulas, thresholds, licenses, privacy, limits and the measured results of the index.
10. An installable app (PWA), a documented read-only API, and a hardened server.

**Deliberately out of scope:**

- Spread direction or speed forecasts, probability maps, machine-learning models. (The weather index is rule-based, experimental and not a forecast.)
- A nationwide risk grid. Risk is shown only around active detections.
- Satellite imagery or burn-scar analysis in the product (tried and dropped, see decision 12).
- Crowd reports, accounts, notifications.
- Response information (crews, helicopters): we cannot produce it, and following official sources is a separate decision.
- A native mobile app.

## 4. Web, not native

**Decision: a responsive web app that installs as a PWA.** One codebase, no store process, most of the value is in the data, and the core experience (map, place, list) works well in a browser. A native app is worth considering only if push notifications or background location become essential.

## 5. Architecture

```
NASA FIRMS ──► ingest (timer, every 30 min) ──► SQLite ◄── Open-Meteo (cached, batched)
                                                   │
              zones · events · fixed sources ──────┤
                                                   ▼
                                     FastAPI ──► map site (Leaflet) · /docs
```

- **Language and framework:** Python 3.11+, FastAPI, SQLAlchemy 2, SQLite. PostgreSQL later (the upsert is SQLite-specific, marked in the code).
- **One process, one command:** `python -m kivilcim` serves the site and runs the ingest timer. The ingest is also a standalone command (`python -m kivilcim.ingest`, with `--source/--from/--to` for backfills), so an external scheduler works equally well.
- **Modules:** `firms` (parse), `ingest` and `scheduler` (fetch, store, record, repeat, catch up), `weather` (cached, quota-aware), `risk` (index), `zones`, `events`, `fixed` (grouping), `api` with `security` and `ratelimit`, `validate` and `stats` (measurement), `districts` (place names), `static/` (pages, service worker, icons).
- **Time:** the API is always UTC; the pages show `Europe/Istanbul`.
- **Configuration:** environment variables or `.env`; secrets never enter the repository (`.env.example` lists every setting).

## 6. Data sources and licenses

| Source | Used for | Terms |
|---|---|---|
| NASA FIRMS (VIIRS Suomi NPP, NOAA-20, NOAA-21; MODIS) | Detections | Open data, free key. NRT is about 3 hours behind and kept for about 3 months; the archive (SP) covers older dates with a lag of a few months. At most 5 days per request, 5,000 requests per 10 minutes. |
| Open-Meteo | Weather (current, 72 h rain) | CC BY 4.0, attribution shown. **Free for non-commercial use only**; 10,000 calls a day, 5,000 an hour, 600 a minute. |
| OpenStreetMap | Map tiles; province and district center points | ODbL 1.0, attribution shown. The data files in `data/` are ODbL, apart from the MIT code. Public tile servers are for light use. |
| NASA GIBS | Tried for imagery, not used | Free, no key. |

## 7. Data model (as built)

- **fire_detections:** `source` (FIRMS product), `satellite`, `instrument`, `latitude`, `longitude`, `acquired_at` (UTC, from `acq_date` + `acq_time`), `confidence_raw`, `confidence_level` (normalized low / nominal / high; MODIS 0-29, 30-79, 80-100), `brightness`, `frp`, `ingested_at`. Unique on (`source`, `latitude`, `longitude`, `acquired_at`), so overlapping fetches are idempotent. UTC is enforced: a naive datetime is rejected.
- **ingest_runs:** one row per live fetch per source (status, count, error with the key redacted). Backfills are not recorded here, because this table feeds the freshness users see.
- **weather_snapshots:** the weather cache, keyed by coordinates rounded to 0.1° (about 11 km).
- **api_usage:** Open-Meteo calls per UTC day, to stay under the self-imposed quota.

## 8. API (as built)

`GET /health`, `/status`, `/fires`, `/weather`, `/risk`, `/risk/zones`, `/events`, `/events/{id}`; pages `/`, `/history`, `/methodology`; `/docs`. Every response has a `disclaimer`; time-dependent ones have `data_as_of`, which holds **two times**: `last_ingest_at` (is the service current?) and `latest_observation_at` (when did a satellite last look?). Freshness thresholds apply to the first: under 3 hours fresh, 3-6 hours warning, over 6 hours stale. `/weather` and `/risk` accept only points in a box around Turkey, so nobody can use the service to burn the weather quota. Read-only CORS is open.

## 9. The weather index and its measured value

**Formula (v2).** Dryness is the weighted average of humidity (0.35; 0 at 70%, 1 at 15%), wind (0.25; 0 at 0 km/h, 1 at 50) and a dry spell (0.20; 0 at 10 mm of rain in 72 hours, 1 at none). Temperature is a **multiplier**: 0 at 5 °C and below, 1 at 30 °C and above. Score = 100 × dryness × temperature. Levels: under 20 low, 40 moderate, 60 high, 80 very high, above that extreme. Every result keeps its inputs, so it can be explained. The weights are a reasoned starting point and were **not tuned** on any data.

**Why a multiplier.** The first version added temperature to the sum; on 2 November 2026, at 2-24 °C, 14 of 81 province centers read "high". As a multiplier, the same readings gave 67 low and 14 moderate.

**Where it is shown.** Only around active detections (zone centers, and the nearest zone for a chosen place). A place with no detection gets no figure, because a "fire risk" for a place that has had no fire for months misleads.

**Measured (first run, `kivilcim.validate`, 4 August to 27 October 2026, 323 fire cases and 323 control places, weather from Open-Meteo's forecast model for past days):**

- *Does a higher index at an active fire go with it continuing or growing the next day?* **No evidence.** Spearman ρ = -0.11 overall (95% interval -0.21 to -0.01), but +0.02 within each month, so any overall relation is the season, not the index. The terciles show no order (the fire was still there the next day in 71%, 63% and 69% of cases).
- *Does the index tell places with fire from places without on the same day?* **Weakly.** AUC 0.69 (0.65 to 0.73); mean index 53 at fire places and 38 at control places.

**Consequence.** The index is presented as "how favorable the weather is for fire", not as a spread or continuation indicator, and the methodology page generates its usage note from the result. If a later run, over a longer period or against reported incidents, finds a relation, the wording can change; until then it must not claim more. Caveats: one season, VIIRS only, noisy next-day heat power, cases from the same fire are not independent (thinned to one per place per 4 days), and no reported incident records were used.

## 10. Detections, zones, fixed sources and events

- **Detections** are observation moments. Low confidence is listed apart. MODIS (1 km pixels) is shown live but not used for events, because its heat power is on a different scale.
- **Fixed sources.** A 0.1° cell that detects on at least 25% of all days (with at least 60 days of history) is a likely factory, refinery, flare or landfill: flagged and listed apart, never dropped, and no index is shown for it. On real data this marked Ereğli, İskenderun, Karabük, the Aliağa area and Samsun industry, plus Iraqi oil fields.
- **Zones.** The last day's detections (not low confidence, not fixed) in adjacent 0.1° cells, inside Turkey (nearest district center within 30 km), strongest first, at most 60 per request, so the weather lookups stay bounded. By default zones under 30 MW and under 5 detections are hidden in the page (mostly small agricultural burns).
- **Events.** Grown from strong detections (at least 100 MW, which forest fires reach and crop burning rarely does), then gathering the weaker ones within 2 cells and from 2 days before to 3 days after. An event needs at least 45 detections and 1,500 MW in total (15 and 500 per satellite, times 3 VIIRS satellites) and at most 14 days. Always-on cells are removed first. Seeding with strong detections replaced "chain nearby detections": weeks of weak crop-burning detections bridged real fires into long blobs that were then dropped (the September 2026 Adana fire was lost that way). The area an event covers is shown; **no forest-versus-crop label is attempted**, because shape and strength measures did not separate them reliably.
- Thresholds are provisional, chosen from June to November 2026.

## 11. Honesty principles in the product

Observation wording only; freshness as two times; low confidence and fixed sources shown apart; no figure without a detection; "no zone" is never "safe"; disclaimers everywhere; attribution for every source; the experimental label stays until the index is shown to be useful; offline, stored data is never presented as current (the service worker stores only the page shell, never live data).

## 12. Design decisions and why

1. **PWA, not native.** One codebase, nothing to approve in a store (section 4).
2. **Two freshness times.** A satellite may not pass for 11 hours; that is not an outage, so only the ingest age drives the warning.
3. **Idempotent ingest and a per-satellite dedup key.** Overlapping windows are normal; the unique key plus `ON CONFLICT DO NOTHING` makes re-runs harmless.
4. **Catch-up after downtime.** The first run after a gap asks for a wider window, up to FIRMS's 5 days, and warns about older gaps.
5. **Three VIIRS satellites.** They pass at different times, so a fire is seen more often, and they cover a gap in the SNPP archive (11-15 July 2026). Event thresholds are set per satellite and multiplied.
6. **Flag, do not drop, fixed sources.** A wildfire can start next to a factory.
7. **Index v2 structure.** Temperature as a multiplier (decision from section 9); weights not tuned.
8. **No risk figure without a detection; no nationwide grid.** It would be many calls and would mislead.
9. **Weather: cache, batch, quota.** Rounded to 0.1° and kept 45 minutes; zones fetched in one request; a daily quota of 7,000 calls (a count, not money) that falls back to cached values; Turkey-only coordinates.
10. **Zones are limited to 60** and strongest first, so a short quota drops the weakest.
11. **Events from strong seeds** (section 10), with no automatic forest/crop label.
12. **Imagery dropped.** NASA GIBS true-color is about 250 m and mostly showed smoke, and swapping the layer on every day made the map flash blank. Sharper 30 m imagery exists only on the days a satellite passes a place (3 of 25 days in the Adana example), so it cannot be stepped daily. The spread map of the detections replaced it.
13. **Measure the index, state the result, change the wording** (section 9). The analysis was fixed before the results; the usage note is generated from them.
14. **One command, with a timer** (`python -m kivilcim`), because running two things by hand does not last.
15. **PWA caches the shell only.** Live data is never cached.
16. **Hardening for a public site.** Per-client rate limits (a stricter one for requests that can call the weather provider), security headers and a content security policy, gzip, a configurable tile provider whose settings cannot break out of the page script, and **no request log by default** because addresses carry coordinates.
17. **Separate licenses.** MIT code; ODbL data files kept apart with their notice; Open-Meteo used under its non-commercial terms, so the project stays free and ad-free.
18. **Tests as the specification.** The numbers on the methodology page are checked against the code; the install path is tested with a wheel in a clean environment; a test fails if a static file is missing from the package.
19. **Pure-Python icons.** No imaging dependency, drawn by a script that is committed with its output.

## 13. Testing and quality

About 400 tests, none needing the network (HTTP is mocked). They cover the parser (empty, malformed, error text, MODIS and VIIRS confidence), the ingest (duplicates, failures, key redaction, backfill, catch-up), the API (boundary values, freshness, validation, limits, headers), the index (monotonic, clamped, known values), the clustering, the weather cache and quota, the statistics helpers, the validation tool (including both a real relation and no relation), the PWA and the packaging. Lint is `ruff`; CI runs lint and tests on Python 3.11 and 3.12 and checks that the built wheel contains the static files.

## 14. Risks

| Risk | Mitigation |
|---|---|
| Users read old data as current | Two times, thresholds, no stored data presented as live, offline banner |
| False positives, crop burning | Confidence shown, fixed sources flagged, extent shown, no "burning now" wording |
| Index read as a forecast | Wording, experimental label, measured result on the methodology page, usage note generated from it |
| Weather quota or terms | Cache, batch, quota with fallback, Turkey-only points, rate limits; non-commercial use only |
| Misunderstood responsibility | Disclaimer and 112 / AFAD everywhere |
| Public hosting costs or abuse | Rate limits, gzip, short cache headers, tile provider setting |
| Scope creep | The out-of-scope list above |

## 15. Not done, and next

- **Validate against reported incidents** (official records), not only satellite data. The largest gap in the evidence.
- **Longer history** (several years, the archive product) to choose thresholds and index scaling from more than one season, and to try thresholds relative to the season. Re-run `kivilcim.validate` on the full June to October period when Open-Meteo's archive is available (it was overloaded when this was prepared).
- **Forest versus crop classification** using land cover.
- **Province and district boundaries** as polygons (the app uses approximate center points).
- **PostgreSQL / PostGIS** if the data grows, and rate limits shared between processes.
- **Build and publish the Docker image** and choose hosting (the image was not built on the machine this was prepared on).
- Notifications and accounts, and a native app, only if the need appears.

---

# Türkçe

## 1. Vizyon ve dürüstlük

Kıvılcım, NASA FIRMS uydu verisini kullanarak bir yer için **"yakınımda tespit edilmiş bir yangın var mı, veri ne kadar taze?"** sorusunu yanıtlar ve aktif tespitlerin çevresinde havanın yangın için ne kadar elverişli olduğunu söyler.

Kıvılcım **resmi bir uyarı sistemi değildir**. Bu her sayfada ve her API yanıtında belirtilir; acil durumda 112 ve AFAD'a yönlendirilir. Servis hiçbir zaman "şu an yanıyor" demez (tespit bir gözlem anıdır), hiçbir zaman "yangın yok" demez (tespit olmaması güvenlik değildir) ve tespiti olmayan bir yer için hiçbir değer göstermez.

## 2. Kullanıcılar

- **Birincil:** bir yerin yakınında yangın tespit edilip edilmediğini merak eden herkes.
- **İkincil:** ücretsiz, salt okunur API'yi kullanan geliştiriciler ve küçük kurumlar.

## 3. Kapsam

**Yapılanlar (v0.1.0, ön yüz v0.2.0'da yenilendi):**

1. Yer seçimi (il, ilçe, harita tıklaması, cihaz konumu, paylaşılabilir bağlantı). Backend yalnızca enlem ve boylam alır.
2. Yarıçap içindeki son tespitler, haritada ve listede; her biri gözlem zamanı, güven, uydu ve mesafe ile.
3. Türkiye geneli **aktif bölgeler** haritası (son günün tespitleri yere göre gruplanmış), her birinin merkezinde hava indeksi.
4. Seçilen yer için hava kartı (sıcaklık, nem, rüzgar, son 72 saat yağış).
5. Her yerde sorumluluk notu ve iki ayrı zaman olarak veri tazeliği.
6. Otomatik, tekrarlanan ve kaydı görünen ingest; tek komutla her şey çalışır.
7. Sürekli ateşli ısı kaynakları (fabrika, meşale) işaretlenir, silinmez.
8. **Geçmiş yangınlar:** arşivde bulunan büyük yangınlar; her biri için yangından önceki ve sonraki uydu görüntüsü (NASA GIBS, açık gün varsa 30 m) ve gün gün yayılma haritası.
9. Formülleri, eşikleri, lisansları, gizliliği, sınırları ve indeksin ölçülmüş sonuçlarını içeren bir yöntem sayfası.
10. Kurulabilir uygulama (PWA), belgelenmiş salt okunur API ve sağlamlaştırılmış sunucu.

**Bilinçli olarak dışarıda olanlar:**

- Yayılım yönü ya da hız tahmini, olasılık haritaları, makine öğrenmesi modelleri. (Hava indeksi kural tabanlıdır, deneyseldir ve tahmin değildir.)
- Ülke geneli risk grid'i. Risk yalnızca aktif tespitlerin çevresinde gösterilir.
- Üründe uydu görüntüsü ya da yanık alan analizi (denendi ve bırakıldı, bkz. karar 12).
- Kullanıcı bildirimleri (crowd), hesaplar, bildirimler.
- Müdahale bilgisi (ekipler, helikopterler): bunu biz üretemeyiz, resmi kaynak takibi ayrı bir karardır.
- Native mobil uygulama.

## 4. Native değil, web

**Karar: PWA olarak kurulabilen, mobil uyumlu bir web uygulaması.** Tek kod tabanı, mağaza süreci yok, değerin büyük kısmı veride ve ana deneyim (harita, yer, liste) tarayıcıda iyi çalışıyor. Native uygulama yalnızca push bildirim ya da arka planda konum vazgeçilmez olursa düşünülür.

## 5. Mimari

```
NASA FIRMS ──► ingest (zamanlayıcı, 30 dk) ──► SQLite ◄── Open-Meteo (önbellekli, toplu)
                                                  │
              bölgeler · olaylar · sabit kaynaklar ┤
                                                  ▼
                                    FastAPI ──► harita sitesi (Leaflet) · /docs
```

- **Dil ve çatı:** Python 3.11+, FastAPI, SQLAlchemy 2, SQLite. Sonra PostgreSQL (upsert SQLite'a özeldir, kodda işaretli).
- **Tek süreç, tek komut:** `python -m kivilcim` siteyi sunar ve ingest zamanlayıcısını çalıştırır. Ingest ayrıca bağımsız bir komuttur (`python -m kivilcim.ingest`, geri doldurma için `--source/--from/--to`), yani dışarıdan bir zamanlayıcı da aynı işi görür.
- **Modüller:** `firms` (ayrıştırma), `ingest` ve `scheduler` (çek, kaydet, kayıt tut, tekrarla, telafi et), `weather` (önbellekli, kotalı), `risk` (indeks), `zones`, `events`, `fixed` (gruplama), `security` ve `ratelimit` ile `api`, `validate` ve `stats` (ölçüm), `districts` (yer adları), `static/` (sayfalar, service worker, ikonlar).
- **Zaman:** API her zaman UTC'dir; sayfalar `Europe/Istanbul` gösterir.
- **Yapılandırma:** ortam değişkenleri ya da `.env`; sırlar depoya girmez (`.env.example` tüm ayarları listeler).

## 6. Veri kaynakları ve lisanslar

| Kaynak | Ne için | Koşullar |
|---|---|---|
| NASA FIRMS (VIIRS Suomi NPP, NOAA-20, NOAA-21; MODIS) | Tespitler | Açık veri, ücretsiz anahtar. NRT yaklaşık 3 saat gecikmeli ve yaklaşık 3 ay saklanır; arşiv (SP) eski tarihleri birkaç ay gecikmeyle kapsar. İstek başına en fazla 5 gün, 10 dakikada 5.000 istek. |
| Open-Meteo | Hava (güncel, 72 saat yağış) | CC BY 4.0, atıf gösterilir. **Yalnızca ticari olmayan kullanım için ücretsiz**; günde 10.000, saatte 5.000, dakikada 600 çağrı. |
| OpenStreetMap | Harita karoları; il ve ilçe merkez noktaları | ODbL 1.0, atıf gösterilir. `data/` altındaki veri dosyaları, MIT koddan ayrı olarak ODbL'dir. Genel karo sunucuları hafif kullanım içindir. |
| NASA GIBS | Görüntü için denendi, kullanılmıyor | Ücretsiz, anahtarsız. |

## 7. Veri modeli (inşa edildiği haliyle)

- **fire_detections:** `source` (FIRMS ürünü), `satellite`, `instrument`, `latitude`, `longitude`, `acquired_at` (UTC, `acq_date` + `acq_time`'dan), `confidence_raw`, `confidence_level` (normalize: low / nominal / high; MODIS 0-29, 30-79, 80-100), `brightness`, `frp`, `ingested_at`. (`source`, `latitude`, `longitude`, `acquired_at`) üzerinde unique; çakışan çekimler idempotenttir. UTC zorunludur: saat dilimsiz `datetime` reddedilir.
- **ingest_runs:** canlı çekim başına, kaynak başına bir satır (durum, sayı, anahtarı maskelenmiş hata). Geri doldurmalar buraya yazılmaz, çünkü bu tablo kullanıcıların gördüğü tazeliği besler.
- **weather_snapshots:** hava önbelleği, 0,1°'ye (yaklaşık 11 km) yuvarlanmış koordinatla.
- **api_usage:** kendi koyduğumuz kotanın altında kalmak için UTC günü başına Open-Meteo çağrıları.

## 8. API (inşa edildiği haliyle)

`GET /health`, `/status`, `/fires`, `/weather`, `/risk`, `/risk/zones`, `/events`, `/events/{id}`; sayfalar `/`, `/history`, `/methodology`; `/docs`. Her yanıtta `disclaimer` vardır; zamana bağlı olanlarda `data_as_of`, yani **iki zaman**: `last_ingest_at` (servis güncel mi?) ve `latest_observation_at` (uydu en son ne zaman baktı?). Tazelik eşikleri birincisine uygulanır: 3 saatin altı güncel, 3-6 saat uyarı, 6 saatten fazlası eski. `/weather` ve `/risk` yalnızca Türkiye çevresindeki bir kutunun içindeki noktaları kabul eder; böylece kimse servisi hava kotasını tüketmek için kullanamaz. Salt okunur CORS açıktır.

## 9. Hava indeksi ve ölçülen değeri

**Formül (v2).** Kuruluk, nem (0,35; %70'te 0, %15'te 1), rüzgar (0,25; 0 km/sa'te 0, 50'de 1) ve kurak dönemin (0,20; 72 saatte 10 mm yağışta 0, hiç yağışsızda 1) ağırlıklı ortalamasıdır. Sıcaklık bir **çarpandır**: 5 °C ve altında 0, 30 °C ve üstünde 1. Skor = 100 × kuruluk × sıcaklık. Seviyeler: 20'nin altı düşük, 40 orta, 60 yüksek, 80 çok yüksek, üstü aşırı. Her sonuç girdilerini taşır, böylece açıklanabilir. Ağırlıklar gerekçeli bir başlangıç noktasıdır ve hiçbir veriye göre **ayarlanmamıştır**.

**Neden çarpan.** İlk sürüm sıcaklığı toplama ekliyordu; 2 Kasım 2026'da 2-24 °C'de 81 il merkezinin 14'ü "yüksek" okundu. Çarpan olunca aynı değerler 67 düşük ve 14 orta verdi.

**Nerede gösterilir.** Yalnızca aktif tespitlerin çevresinde (bölge merkezleri ve seçilen yer için en yakın bölge). Tespiti olmayan yer için değer gösterilmez, çünkü aylardır yangın olmayan bir yer için "yangın riski" yanıltıcıdır.

**Ölçüm (ilk çalıştırma, `kivilcim.validate`, 4 Ağustos - 27 Ekim 2026, 323 yangın örneği ve 323 kontrol yeri, geçmiş günler için Open-Meteo'nun tahmin modelinden hava):**

- *Aktif bir yangında yüksek indeks, yangının ertesi gün sürmesi ya da büyümesiyle birlikte mi?* **Kanıt yok.** Spearman ρ genelde -0,11 (%95 aralık -0,21 ile -0,01), ama her ay içinde +0,02; yani genel bir ilişki varsa o mevsimdir, indeks değil. Üçte birlik dilimler bir düzen göstermiyor (yangın ertesi gün %71, %63 ve %69 oranında sürüyordu).
- *İndeks, aynı gün yangın olan yerleri olmayanlardan ayırıyor mu?* **Zayıf biçimde.** AUC 0,69 (0,65 ile 0,73); yangın olan yerlerde ortalama indeks 53, kontrol yerlerinde 38.

**Sonuç.** İndeks "havanın yangın için ne kadar elverişli olduğu" olarak sunulur, yayılma ya da süreklilik göstergesi olarak değil; yöntem sayfası kullanım notunu sonuçtan üretir. Daha uzun bir dönemde ya da bildirilen olaylarla yapılacak sonraki bir çalıştırma bir ilişki bulursa ifade değişebilir; o zamana kadar daha fazlasını iddia etmemelidir. Sınırlar: tek mevsim, yalnızca VIIRS, gürültülü ertesi gün ısı gücü, aynı yangının örnekleri bağımsız değildir (yer başına 4 günde bir örneğe seyreltildi) ve bildirilmiş olay kayıtları kullanılmadı.

## 10. Tespitler, bölgeler, sabit kaynaklar ve olaylar

- **Tespitler** gözlem anlarıdır. Düşük güven ayrı listelenir. MODIS (1 km piksel) canlı gösterilir ama olaylarda kullanılmaz, çünkü ısı gücü farklı bir ölçektedir.
- **Sabit kaynaklar.** Tüm günlerin en az %25'inde tespit veren 0,1°'lik hücre (en az 60 günlük geçmişle) olası fabrika, rafineri, meşale ya da çöplüktür: işaretlenir ve ayrı listelenir, silinmez, o hücre için indeks gösterilmez. Gerçek veride Ereğli, İskenderun, Karabük, Aliağa çevresi ve Samsun sanayisini, ayrıca Irak petrol sahalarını işaretledi.
- **Bölgeler.** Son günün tespitleri (düşük güven ve sabit olmayan), bitişik 0,1°'lik hücrelerde, Türkiye içinde (en yakın ilçe merkezi 30 km içinde), en güçlüsü önce, istek başına en fazla 60; böylece hava sorguları sınırlı kalır. Sayfada varsayılan olarak 30 MW altı ve 5 tespitten az bölgeler gizlenir (çoğu küçük tarla yakmasıdır).
- **Olaylar.** Güçlü tespitlerden (en az 100 MW; orman yangınları buna ulaşır, tarla yakması nadiren) büyür, sonra 2 hücre içindeki ve 2 gün öncesinden 3 gün sonrasına kadar olan zayıf tespitleri toplar. Bir olay en az 45 tespit ve toplam 1.500 MW ister (uydu başına 15 ve 500, 3 VIIRS uydusuyla çarpılmış) ve en fazla 14 gün sürer. Sürekli ateşli hücreler önce çıkarılır. Güçlü tespitle başlamak "yakın tespitleri zincirle" yaklaşımının yerini aldı: haftalarca süren zayıf anız tespitleri gerçek yangınları uzun kütlelere köprüledi ve bunlar elendi (Eylül 2026 Adana yangını böyle kaybolmuştu). Olayın kapladığı alan gösterilir; **orman-tarla etiketi denenmez**, çünkü şekil ve güç ölçüleri ikisini güvenilir biçimde ayırmadı.
- Eşikler geçicidir; Haziran-Kasım 2026'dan seçildi.

## 11. Üründeki dürüstlük ilkeleri

Yalnızca gözlem dili; tazelik iki zaman olarak; düşük güven ve sabit kaynaklar ayrı; tespitsiz yer için değer yok; "bölge yok" asla "güvenli" değildir; her yerde sorumluluk notu; her kaynak için atıf; indeksin işe yaradığı gösterilene kadar deneysel etiketi kalır; çevrimdışıyken saklı veri güncelmiş gibi sunulmaz (service worker yalnızca sayfa kabuğunu saklar, canlı veriyi asla).

## 12. Tasarım kararları ve gerekçeleri

1. **Native değil PWA.** Tek kod tabanı, mağazada onay yok (bölüm 4).
2. **İki tazelik zamanı.** Bir uydu 11 saat geçmeyebilir; bu arıza değildir, o yüzden uyarıyı yalnızca ingest yaşı belirler.
3. **Idempotent ingest ve uydu başına tekrar anahtarı.** Çakışan pencereler normaldir; unique anahtar ve `ON CONFLICT DO NOTHING` yeniden çalıştırmayı zararsız yapar.
4. **Kapalı kalınca telafi.** Bir boşluktan sonraki ilk çalıştırma daha geniş pencere ister (FIRMS'in 5 gününe kadar) ve daha eski boşlukları uyarır.
5. **Üç VIIRS uydusu.** Farklı saatlerde geçerler, yani yangın daha sık görülür ve SNPP arşivindeki boşluğu (11-15 Temmuz 2026) kapatırlar. Olay eşikleri uydu başınadır ve çarpılır.
6. **Sabit kaynakları silme, işaretle.** Bir orman yangını fabrikanın yanında çıkabilir.
7. **İndeks v2 yapısı.** Sıcaklık çarpan (bölüm 9 kararı); ağırlıklar ayarlanmadı.
8. **Tespitsiz yerde risk değeri yok; ülke geneli grid yok.** Çok çağrı demek ve yanıltır.
9. **Hava: önbellek, toplu çekim, kota.** 0,1°'ye yuvarlanır ve 45 dakika tutulur; bölgeler tek istekle çekilir; günde 7.000 çağrılık kota (bir sayıdır, para değil) önbelleğe düşer; yalnızca Türkiye koordinatları.
10. **Bölgeler 60 ile sınırlı** ve en güçlüsü önce; kota kısalırsa en zayıflar değersiz kalır.
11. **Güçlü tohumlardan olaylar** (bölüm 10), otomatik orman/tarla etiketi olmadan.
12. **Görüntü bırakıldı.** NASA GIBS gerçek renk yaklaşık 250 m'dir ve çoğunlukla duman gösterdi; katmanı her gün değiştirmek haritanın boş yanıp sönmesine yol açtı. Daha keskin 30 m görüntü yalnızca uydunun bir yerden geçtiği günlerde var (Adana örneğinde 25 günün 3'ü), yani gün gün gezdirilemez. Tespitlerin yayılma haritası onun yerini aldı.
13. **İndeksi ölç, sonucu söyle, ifadeyi değiştir** (bölüm 9). Analiz sonuçlardan önce sabitlendi; kullanım notu onlardan üretilir.
14. **Tek komut, zamanlayıcıyla** (`python -m kivilcim`), çünkü iki şeyi elle çalıştırmak uzun sürmez.
15. **PWA yalnızca kabuğu saklar.** Canlı veri asla saklanmaz.
16. **Genel site için sağlamlaştırma.** İstemci başına hız sınırı (hava sağlayıcısını çağırabilen istekler için daha sıkı), güvenlik başlıkları ve içerik güvenlik politikası, gzip, ayarları sayfa script'inden kaçamayan yapılandırılabilir karo sağlayıcı ve adresler koordinat taşıdığı için **varsayılan olarak istek günlüğü yok**.
17. **Ayrı lisanslar.** MIT kod; ODbL veri dosyaları notlarıyla ayrı; Open-Meteo ticari olmayan koşullarıyla kullanılır, yani proje ücretsiz ve reklamsız kalır.
18. **Spesifikasyon olarak testler.** Yöntem sayfasındaki sayılar koda karşı denetlenir; kurulum yolu temiz bir ortamda wheel ile test edilir; bir statik dosya pakette eksikse test başarısız olur.
19. **Saf Python ikonlar.** Görüntü işleme bağımlılığı yok; çıktısıyla birlikte commit'lenen bir betikle çizilir.

## 13. Test ve kalite

Ağa ihtiyaç duymayan yaklaşık 400 test (HTTP taklit edilir). Ayrıştırıcı (boş, bozuk, hata metni, MODIS ve VIIRS güveni), ingest (tekrar, hata, anahtar maskeleme, geri doldurma, telafi), API (sınır değerleri, tazelik, doğrulama, limitler, başlıklar), indeks (monoton, sınırlanmış, bilinen değerler), kümeleme, hava önbelleği ve kotası, istatistik yardımcıları, doğrulama aracı (hem gerçek ilişki hem ilişkisizlik), PWA ve paketleme kapsanır. Lint `ruff`'tur; CI, lint ve testleri Python 3.11 ve 3.12'de çalıştırır ve derlenen wheel'in statik dosyaları içerdiğini denetler.

## 14. Riskler

| Risk | Önlem |
|---|---|
| Kullanıcı eski veriyi güncel sanır | İki zaman, eşikler, saklı verinin canlı gibi sunulmaması, çevrimdışı bandı |
| Yanlış pozitifler, tarla yakması | Güven gösterilir, sabit kaynaklar işaretlenir, alan çapı gösterilir, "şu an yanıyor" dili yok |
| İndeksin tahmin sanılması | İfade, deneysel etiketi, yöntem sayfasındaki ölçülmüş sonuç, ondan üretilen kullanım notu |
| Hava kotası ya da koşulları | Önbellek, toplu çekim, düşüşlü kota, yalnızca Türkiye noktaları, hız limitleri; yalnızca ticari olmayan kullanım |
| Yanlış anlaşılan sorumluluk | Her yerde sorumluluk notu ve 112 / AFAD |
| Genel barındırmada maliyet ya da kötüye kullanım | Hız limitleri, gzip, kısa önbellek başlıkları, karo sağlayıcı ayarı |
| Kapsam büyümesi | Yukarıdaki kapsam dışı listesi |

## 15. Yapılmayanlar ve sonraki adımlar

- **Bildirilen olaylarla doğrulama** (resmi kayıtlar), yalnızca uydu verisiyle değil. Kanıttaki en büyük boşluk.
- **Daha uzun geçmiş** (birkaç yıl, arşiv ürünü): eşikleri ve indeks ölçeğini tek bir mevsimden fazlasından seçmek ve mevsime göreli eşikleri denemek için. Open-Meteo arşivi uygun olunca `kivilcim.validate` tam Haziran-Ekim dönemi için yeniden çalıştırılmalı (bu hazırlanırken aşırı yüklüydü).
- **Land cover ile orman-tarla ayrımı.**
- **İl ve ilçe sınırları poligon olarak** (uygulama yaklaşık merkez noktaları kullanıyor).
- **PostgreSQL / PostGIS**, veri büyürse; süreçler arası paylaşılan hız limitleri.
- **Docker imajını derlemek ve yayımlamak** ve barındırmayı seçmek (imaj, bu hazırlanan makinede derlenmedi).
- Bildirimler ve hesaplar ile native uygulama, yalnızca ihtiyaç doğarsa.
