# Putting Kıvılcım on the internet

Run on your own computer, Kıvılcım only works while it is switched on. Three things need a server that is
always on: **Telegram alerts** (the bot has to be listening), **link previews and search engines** (they
need an address that answers), and **people finding the site during a fire** (the moment it matters).

A small virtual server is enough (one CPU, 1-2 GB of memory; a few euros a month at most hosting companies).
The steps below use Docker and a reverse proxy that gets the HTTPS certificate by itself.

## What you need

| Thing | Where from | Used for |
| --- | --- | --- |
| A server with Docker | any VPS provider (Ubuntu or Debian is fine) | running the site |
| A domain name | any registrar; point an `A` record at the server's address | HTTPS and the site's address |
| `FIRMS_MAP_KEY` | https://firms.modaps.eosdis.nasa.gov/api/map_key/ (free) | the satellite data |
| A Telegram bot token | message **@BotFather**, send `/newbot` | alerts (optional) |

## Steps

1. On the server: install Docker, then `git clone https://github.com/beyuphan/kivilcim && cd kivilcim`.
2. `cp .env.example .env` and set at least:
   ```
   FIRMS_MAP_KEY=...
   SITE_ADDRESS=kivilcim.example.com
   PUBLIC_URL=https://kivilcim.example.com
   TRUST_PROXY=true
   TELEGRAM_BOT_TOKEN=123456:ABC...        # optional
   TELEGRAM_BOT_USERNAME=your_bot_name     # optional, without the @
   ```
3. `docker compose -f docker-compose.prod.yml up -d --build`.
4. Open `https://kivilcim.example.com/health` (should say `{"status":"ok"}`), then `/status` (after a few
   minutes it shows the first ingest). The first start downloads the last days of detections.
5. Optional, for the past-fires pages: `docker compose -f docker-compose.prod.yml exec kivilcim python -m kivilcim.ingest --source VIIRS_SNPP_SP --from 2026-06-01 --to 2026-06-10` (repeat in 10-day pieces and for each source; see the README).

Ports 80 and 443 must be open in the server's firewall. Nothing else needs to be.

## Telegram

With `TELEGRAM_BOT_TOKEN` set, the bot starts listening when the app starts. Talk to your bot: `/start`,
send a location, get a message when a satellite sees a new fire near it. The site then shows a "Telegram'dan
haber ver" button on every place. Hints:

- In @BotFather: `/setcommands` with `yer`, `liste`, `yaricap`, `durdur`, `yardim`, and `/setdescription`
  explaining that this is not an official warning system.
- A public channel for big fires: create a channel, add the bot as an administrator, set
  `TELEGRAM_CHANNEL=@yourchannel`.
- Check it end to end with your own phone before telling anyone: add your district, then fake nothing: the
  first real detection near you proves the chain. Until then `/liste` and a reply to `/yer` show the bot works.

## Keeping it healthy

- **Updates:** `git pull && docker compose -f docker-compose.prod.yml up -d --build`. The database stays in
  the `kivilcim-data` volume.
- **Backups:** the database is one SQLite file. A consistent copy while the site runs:
  ```
  docker compose -f docker-compose.prod.yml exec kivilcim python -c \
    "import sqlite3; s=sqlite3.connect('/data/kivilcim.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"
  docker compose -f docker-compose.prod.yml cp kivilcim:/data/backup.db ./backup-$(date +%F).db
  ```
  Put that in a daily cron job. Losing the database loses the history and the Telegram subscriptions, not
  the live data (that comes back from FIRMS).
- **Logs:** `docker compose -f docker-compose.prod.yml logs -f kivilcim`. Request logs are off on purpose:
  addresses contain coordinates (see the methodology page).
- **Is it alive?** Point an uptime checker (UptimeRobot, Healthchecks) at `/health`; and `/status` reports
  when the last data arrived (`freshness` becomes `stale` after six hours).

## Limits to know about

- OpenStreetMap's public map tiles are meant for light use. For a busy site use a tile provider and set
  `TILE_URL` and `TILE_ATTRIBUTION`.
- Open-Meteo's free weather service is for non-commercial use, with a daily call limit that the app respects
  (`OPEN_METEO_DAILY_QUOTA`).
- The site is not an official warning system and says so everywhere. Keep that.
