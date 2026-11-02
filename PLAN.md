# Kıvılcım: Proje Planı / Project Plan

> Bu dosya iki dilli: önce Türkçe, sonra İngilizce. Her iki bölüm de aynı içeriği taşır.
> This file is bilingual: Turkish first, then English. Both sections contain the same content.

---

# English

## 1. Vision

Kıvılcım is a service that uses NASA FIRMS satellite data to give an honest, understandable answer to one question for a given location: **"Is there an active fire near me, how fresh is the data, and how risky is my area today?"**

Kıvılcım is not an official warning system. This is stated clearly on every screen, and emergencies are directed to 112 and AFAD (Turkey).

## 2. Target users

- **Primary:** General users who want to know whether there is a fire in their area.
- **Secondary (later stage):** Developers and small organizations who want API access to the data.

## 3. MVP scope

Included in the MVP:

1. The user enters a location (coordinates, or a province/district selection).
2. Recent active fire detections within a chosen radius are listed and shown on a map.
3. Each detection shows its **observation time, confidence value and satellite source**.
4. A simple weather summary for the location (wind, humidity, temperature).
5. A responsibility note on every page (not an official warning; data may be delayed).
6. The data ingestion job runs regularly, and the last successful update time is visible in the API.

Deliberately **out of scope** for the MVP:

- Spread direction and speed estimation (next stage; not shown before backtesting)
- Probability zones and risk maps (weather integration comes after the MVP)
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
[Open-Meteo] ──► [Weather job (scheduled)] ──┘        │
                                                      ▼
                                              [REST API (FastAPI)]
                                                      │
                                                      ▼
                                          [Web frontend (later)]
```

Technology choices (recommended for the MVP):
- **Language and framework:** Python, FastAPI
- **Database:** Start with SQLite, move to PostgreSQL + PostGIS as data grows
- **Scheduling:** A simple scheduler first (APScheduler, or the OS task scheduler)
- **Tests:** pytest, with fixed fixtures for FIRMS responses
- **Configuration:** API keys live in a `.env` file and never enter the repo

## 6. Data sources

| Source | Used for | Access | Note |
|---|---|---|---|
| NASA FIRMS | Active fire detections | Free, API key | Delayed, has a confidence value |
| Open-Meteo | Wind, humidity, temperature | Free, no key needed | Check commercial use terms |
| Sentinel-2 (Copernicus) | Validation, burned area | Free, registration required | Later stage |

## 7. Data model (draft)

**fire_detections**: `id`, `source` (MODIS / VIIRS), `latitude`, `longitude`, `acquired_at` (satellite observation time, UTC), `confidence`, `brightness`, `frp` (fire radiative power), `ingested_at`

**weather_snapshots** (optional cache in the MVP): `id`, `latitude`, `longitude`, `fetched_at`, `wind_speed`, `wind_direction`, `humidity`, `temperature`

**ingest_runs** (health tracking): `id`, `source`, `started_at`, `finished_at`, `status`, `record_count`, `error`

## 8. API draft (MVP)

- `GET /health`: Is the service up
- `GET /status`: Last successful update time and delay for each source
- `GET /fires?lat=&lon=&radius_km=&since_hours=`: Nearby fire detections with observation time and confidence
- `GET /weather?lat=&lon=`: Simple weather summary for a location

Every response includes a `data_as_of` field. The frontend shows it to the user.

## 9. Data delay and honesty principles

- Each detection is shown with "observed at [time]", never "burning right now".
- The older the data, the more prominent the warning in the interface.
- For false positives, the MVP uses rules: recurring fixed hot spots (factories, refineries, etc.) are flagged separately, and low-confidence detections are shown separately.
- Model training does not start until enough labeled data has accumulated.

## 10. Backend stages

**Stage 0: Foundation (first goal)**
- Project skeleton, `.gitignore`, `.env` handling
- FIRMS ingest job: fetch, validate, store
- Record runs in `ingest_runs`
- Tests (with fixed data)

**Stage 1: API**
- `/health`, `/status`, `/fires`
- Query by location and radius
- Tests for error and timestamp behavior

**Stage 2: Weather integration**
- Open-Meteo ingest and `/weather`
- Cache (avoid repeated requests for the same location)

**Stage 3: Frontend**
- Map (Leaflet), location input, list view
- Responsibility note and data time on every screen

**Stage 4: Validation and analysis**
- Backtesting on past fires (FIRMS archive + Sentinel-2)
- Publishing metrics on a public page
- Spread direction (from consecutive FIRMS detections), only after backtest results exist

**Stage 5: Release**
- Deployment, documentation, public API
- Native app decision, if needed

## 11. Testing and quality

- Ingest job: unit tests with fixed FIRMS responses, plus empty and malformed response cases
- API: boundary values for `/fires` (radius 0, very large radius, invalid coordinates)
- Timestamps are always UTC and clearly labeled in responses
- On failure, the last successful data is returned and `data_as_of` shows it is stale

## 12. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| FIRMS delay | Users think old data is current | `data_as_of` in every response, clear warning in the UI |
| False positives | Loss of trust | Fixed-source filter, confidence value, separate flagging |
| Misunderstood responsibility | Legal and ethical risk | Clear warning on every screen, 112/AFAD directions |
| API rate limits | Service outage | Caching, reasonable ingest intervals |
| Scope creep | Project never finishes | Stick to the out-of-scope list in this file |

## 13. Decisions

- Target area: all of Turkey. Queries are stored per region so the scope can grow later.
- Location input: province/district selection in the MVP. Coordinate input comes later.
- License: MIT (see `LICENSE`).

## 14. Next step

Start Stage 0: set up the project skeleton and write the FIRMS ingest job. Begin once the FIRMS API key is available.

---

# Türkçe

## 1. Vizyon

Kıvılcım, NASA FIRMS uydu verisini kullanarak bir konum için **"yakınımda aktif yangın var mı, veri ne kadar taze, bugün bölgem ne kadar riskli"** sorusuna dürüst ve anlaşılır bir cevap veren bir servistir.

Kıvılcım bir resmi uyarı sistemi değildir. Her ekranda bu açıkça belirtilir ve acil durumda 112 ile AFAD'a yönlendirilir.

## 2. Hedef kullanıcı

- **Birincil:** Bulunduğu bölgede yangın olup olmadığını merak eden genel kullanıcı.
- **İkincil (ilerleyen aşama):** Veriye API üzerinden erişmek isteyen geliştiriciler ve küçük kurumlar.

## 3. MVP kapsamı

MVP'de yer alanlar:

1. Kullanıcı bir konum girer (koordinat ya da şehir/ilçe seçimi).
2. Belirli yarıçap içinde son gözlemlenen aktif yangın noktaları listelenir ve haritada gösterilir.
3. Her noktanın yanında **gözlem zamanı, güven değeri ve uydu kaynağı** yazar.
4. Konum için basit bir hava özeti gösterilir (rüzgar, nem, sıcaklık).
5. Her sayfada sorumluluk notu bulunur (resmi uyarı değildir, veri gecikmeli olabilir).
6. Veri çekme işi düzenli çalışır ve son başarılı güncelleme zamanı API'de görünür.

MVP'de **olmayanlar** (bilinçli olarak dışarıda):

- Yayılım yönü ve hız tahmini (sonraki aşama, geri test yapılmadan gösterilmez)
- Olasılık alanları ve risk haritası (hava verisi entegrasyonu MVP'den sonra)
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
[Open-Meteo] ──► [Hava işi (zamanlanmış)]  ──┘        │
                                                      ▼
                                              [REST API (FastAPI)]
                                                      │
                                                      ▼
                                          [Web arayüzü (sonra)]
```

Teknoloji seçimi (MVP için önerilen):
- **Dil ve çatı:** Python, FastAPI
- **Veritabanı:** SQLite ile başla, veri büyüyünce PostgreSQL + PostGIS'e geç
- **Zamanlama:** Önce basit bir zamanlayıcı (APScheduler ya da işletim sistemi görev zamanlayıcısı)
- **Testler:** pytest, FIRMS yanıtları için sabit (fixture) veriler
- **Konfigürasyon:** API anahtarı `.env` dosyasında, repo'ya girmez

## 6. Veri kaynakları

| Kaynak | Ne için | Erişim | Not |
|---|---|---|---|
| NASA FIRMS | Aktif yangın noktaları | Ücretsiz, API anahtarı | Gecikmeli, güven değeri var |
| Open-Meteo | Rüzgar, nem, sıcaklık | Ücretsiz, anahtar gerekmez | Ticari kullanım koşullarını kontrol et |
| Sentinel-2 (Copernicus) | Doğrulama, yanmış alan | Ücretsiz, kayıt gerekir | Sonraki aşama |

## 7. Veri modeli (taslak)

**fire_detections**: `id`, `source` (MODIS / VIIRS), `latitude`, `longitude`, `acquired_at` (uydu gözlem zamanı, UTC), `confidence`, `brightness`, `frp` (radyatif güç), `ingested_at`

**weather_snapshots** (MVP'de önbellek olarak isteğe bağlı): `id`, `latitude`, `longitude`, `fetched_at`, `wind_speed`, `wind_direction`, `humidity`, `temperature`

**ingest_runs** (sağlık takibi): `id`, `source`, `started_at`, `finished_at`, `status`, `record_count`, `error`

## 8. API taslağı (MVP)

- `GET /health`: Servis ayakta mı
- `GET /status`: Her kaynak için son başarılı güncelleme zamanı ve gecikme
- `GET /fires?lat=&lon=&radius_km=&since_hours=`: Yakın yangın noktaları, gözlem zamanı ve güven değeriyle
- `GET /weather?lat=&lon=`: Konum için basit hava özeti

Her yanıtta `data_as_of` alanı bulunur. Arayüz bu alanı kullanıcıya gösterir.

## 9. Veri gecikmesi ve dürüstlük ilkeleri

- Her nokta "şu saatte gözlemlendi" bilgisiyle gösterilir, "şu an yanıyor" denmez.
- Veri ne kadar eskiyse arayüzde o kadar belirgin uyarı verilir.
- Yanlış pozitif riski için MVP'de kurallar kullanılır: tekrarlayan sabit sıcak noktalar (fabrika, rafineri vb.) ayrı işaretlenir, düşük güvenli tespitler ayrı gösterilir.
- Model eğitimi, yeterli etiketli veri birikmeden yapılmaz.

## 10. Backend aşamaları

**Aşama 0: Temel (ilk hedef)**
- Proje iskeleti, `.gitignore`, `.env` yönetimi
- FIRMS ingest işi: çek, doğrula, kaydet
- `ingest_runs` ile kayıt altına al
- Testler (sabit veriyle)

**Aşama 1: API**
- `/health`, `/status`, `/fires`
- Konuma ve yarıçapa göre sorgu
- Hata ve zaman damgası davranışının testleri

**Aşama 2: Hava entegrasyonu**
- Open-Meteo ingest ve `/weather`
- Önbellek (aynı konum için tekrar istek atmamak)

**Aşama 3: Arayüz**
- Harita (Leaflet), konum girişi, liste görünümü
- Sorumluluk notu ve veri zamanı her ekranda

**Aşama 4: Doğrulama ve analiz**
- Geçmiş yangınlarla geri test (FIRMS arşivi + Sentinel-2)
- Metrikleri herkese açık bir sayfada yayımlama
- Yayılım yönü (ardışık FIRMS geçişlerinden), yalnızca geri test sonuçları olduktan sonra

**Aşama 5: Yayın**
- Deploy, dokümantasyon, public API
- Gerekirse native uygulama kararı

## 11. Test ve kalite

- Ingest işi: sabit FIRMS yanıtıyla birim testi, boş ve hatalı yanıt senaryoları
- API: `/fires` için sınır değerleri (yarıçap 0, çok büyük yarıçap, geçersiz koordinat)
- Zaman damgaları her zaman UTC, yanıtta açıkça belirtilir
- Hata durumunda son başarılı veri döner ve `data_as_of` eski olduğunu gösterir

## 12. Riskler

| Risk | Etki | Önlem |
|---|---|---|
| FIRMS gecikmesi | Kullanıcı eski veriyi güncel sanar | `data_as_of` her yanıtta, arayüzde belirgin uyarı |
| Yanlış pozitifler | Güven kaybı | Sabit kaynak filtresi, güven değeri, ayrı işaretleme |
| Yanlış anlaşılan sorumluluk | Hukuki ve etik risk | Her ekranda net uyarı, 112/AFAD yönlendirmesi |
| API kullanım limiti | Servis durması | Önbellek, ingest aralığını makul tutmak |
| Kapsam büyümesi | Proje bitmez | Bu dosyadaki MVP dışı listesine sadık kalmak |

## 13. Kararlar

- Hedef bölge: Türkiye tamamı. Sorgular bölge bazlı tutulur, kapsam ileride büyüyebilir.
- Konum girişi: MVP'de il/ilçe seçimi. Koordinat girişi sonra gelir.
- Lisans: MIT (bkz. `LICENSE`).

## 14. Sonraki adım

Aşama 0'ı başlatmak: proje iskeletini kurmak ve FIRMS ingest işini yazmak. FIRMS API anahtarı hazır olduğunda başlanır.
