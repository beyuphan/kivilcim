"""Telegram alerts: tell a person when a satellite sees a new fire near a place they chose.

There are no accounts: a person talks to the bot, sends a place (or taps a link on the site) and a radius,
and gets a message when a new detection appears inside it. Only the Telegram chat id and a place rounded
to about a kilometer are stored; `/durdur` removes them. The bot reads its messages by polling Telegram, so
it needs no public address.

An alert says what a satellite saw, hours ago, and nothing more: it is not a warning system.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from kivilcim import wind
from kivilcim.fixed import cell_of
from kivilcim.geo import haversine_km
from kivilcim.models import AlertSent, AlertSubscription, BotState, FireDetection
from kivilcim.seo import provinces, slugify

log = logging.getLogger("kivilcim.alerts")

API = "https://api.telegram.org"
MAX_PLACES = 5  # per chat
DEFAULT_RADIUS_KM = 15
MIN_RADIUS_KM, MAX_RADIUS_KM = 3, 50
COOLDOWN = timedelta(hours=6)  # the same fire is not announced again sooner than this
INACTIVE_AFTER = timedelta(days=365)
MIN_FRP_MW = 3.0  # weaker detections are mostly noise and small crop fires
CHANNEL = -1  # the `sub_id` that marks a post to the public channel

DISCLAIMER = "Kıvılcım resmi bir uyarı sistemi değildir; uydu verisi saatler geç gelir. Tehlikedeyseniz 112'yi arayın."
HELP = (
    "Kıvılcım, yakınınızda uydunun yeni bir yangın sıcaklığı gördüğünde size haber verir.\n\n"
    "• Bir yer eklemek için konumunuzu gönderin ya da il/ilçe adı yazın: /yer Çankaya, Ankara\n"
    "• /liste kayıtlı yerlerinizi gösterir\n"
    "• /yaricap 1 25  → 1. yer için yarıçapı 25 km yapar (3-50 km, varsayılan 15)\n"
    "• /durdur 1  → 1. yeri siler; /durdur tüm yerleri siler\n\n"
    + DISCLAIMER
)


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float


def resolve_place(text: str) -> list[Place]:
    """The provinces and districts a text could mean. 'adana', 'Çankaya, Ankara' and 'çankaya ankara' work;
    a district name used in several provinces gives several matches."""
    wanted = slugify(text)
    if not wanted:
        return []
    exact_provinces = [p for p in provinces() if p["slug"] == wanted]
    if exact_provinces:
        p = exact_provinces[0]
        return [Place(p["name"], p["lat"], p["lon"])]
    found = []
    for p in provinces():
        for d in p["districts"]:
            district = slugify(d["name"])
            if wanted in (district, f"{district}-{p['slug']}"):
                found.append(Place(f"{d['name']}, {p['name']}", d["lat"], d["lon"]))
    return found


def nearest_name(lat: float, lon: float) -> str:
    best = min(
        ((haversine_km(lat, lon, d["lat"], d["lon"]), f"{d['name']}, {p['name']}") for p in provinces() for d in p["districts"]),
        default=(0, "Seçilen yer"),
    )
    return best[1] if best[0] <= 30 else "Seçilen yer"


class Telegram:
    """The few calls of the Bot API that the bot uses. Never logs the token."""

    def __init__(self, token: str, client: httpx.Client) -> None:
        self._base = f"{API}/bot{token}"
        self._client = client
        logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines contain the token

    def call(self, method: str, **params) -> dict | None:
        try:
            response = self._client.post(f"{self._base}/{method}", json=params, timeout=params.get("timeout", 10) + 10)
        except httpx.HTTPError:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        if not body.get("ok"):
            return {"ok": False, "error_code": body.get("error_code"), "description": body.get("description")}
        return body

    def updates(self, offset: int, timeout: int = 25) -> list[dict] | None:
        """The waiting messages, or None when Telegram could not be reached or refused (a wrong token)."""
        body = self.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message"])
        return body["result"] if body and body.get("ok") else None

    def send(self, chat_id: int, text: str, keyboard: dict | None = None) -> str:
        """'sent', 'blocked' (the person stopped the bot or left) or 'failed'."""
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard is not None:
            params["reply_markup"] = keyboard
        body = self.call("sendMessage", **params)
        if body and body.get("ok"):
            return "sent"
        if body and body.get("error_code") in (400, 403):
            return "blocked"
        return "failed"

    def me(self) -> str | None:
        body = self.call("getMe")
        return body["result"].get("username") if body and body.get("ok") else None


LOCATION_KEYBOARD = {
    "keyboard": [[{"text": "Konumumu gönder", "request_location": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}


class AlertService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        telegram: Telegram,
        clock: Callable[[], datetime],
        site_url: Callable[[], str],
        weather_for: Callable[[float, float], tuple[float | None, float | None] | None],
        fixed_cells: Callable[[Session], set],
        channel: str = "",
        channel_min_frp: float = 400.0,
    ) -> None:
        self._sessions = session_factory
        self._tg = telegram
        self._clock = clock
        self._site_url = site_url
        self._weather_for = weather_for
        self._fixed_cells = fixed_cells
        self._channel = channel
        self._channel_min_frp = channel_min_frp
        self._lock = threading.Lock()

    # ---- what people say to the bot --------------------------------------------------------------

    def handle(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private" or "id" not in chat:
            return  # groups and channels are not served
        chat_id = chat["id"]
        now = self._clock()
        with self._sessions() as session:
            self._touch(session, chat_id, now)
            location = message.get("location")
            text = (message.get("text") or "").strip()
            if location:
                reply = self._add(session, chat_id, nearest_name(location["latitude"], location["longitude"]), location["latitude"], location["longitude"], DEFAULT_RADIUS_KM, now)
            elif text.startswith("/"):
                reply = self._command(session, chat_id, text, now)
            elif text:
                reply = self._by_name(session, chat_id, text, now)
            else:
                return
            session.commit()
        status = self._tg.send(chat_id, reply[0], reply[1] if len(reply) > 1 else None)
        if status == "blocked":
            self._forget(chat_id)

    def _command(self, session: Session, chat_id: int, text: str, now: datetime) -> tuple:
        command, _, argument = text.partition(" ")
        command = command.split("@")[0].lower()
        argument = argument.strip()
        if command == "/start":
            if argument.startswith("p"):  # the deep link of the site: p<lat*100>_<lon*100>_<radius>
                parsed = self._parse_payload(argument)
                if parsed:
                    lat, lon, radius = parsed
                    return self._add(session, chat_id, nearest_name(lat, lon), lat, lon, radius, now)
            return (HELP, LOCATION_KEYBOARD)
        if command in ("/yardim", "/help"):
            return (HELP,)
        if command == "/yer":
            if not argument:
                return ("Konumunuzu gönderin ya da il/ilçe adını yazın.", LOCATION_KEYBOARD)
            return self._by_name(session, chat_id, argument, now)
        if command == "/liste":
            return (self._list(session, chat_id),)
        if command == "/yaricap":
            return (self._radius(session, chat_id, argument),)
        if command in ("/durdur", "/stop"):
            return (self._stop(session, chat_id, argument),)
        return ("Bu komutu bilmiyorum. /yardim yazın.",)

    @staticmethod
    def _parse_payload(payload: str) -> tuple[float, float, int] | None:
        try:
            lat, lon, radius = payload[1:].split("_")
            lat, lon, radius = int(lat) / 100, int(lon) / 100, int(radius)
        except ValueError:
            return None
        if not (35.5 <= lat <= 42.5 and 25.5 <= lon <= 45.0):
            return None
        return lat, lon, min(max(radius, MIN_RADIUS_KM), MAX_RADIUS_KM)

    def _by_name(self, session: Session, chat_id: int, text: str, now: datetime) -> tuple:
        matches = resolve_place(text)
        if not matches:
            return (f"\"{escape(text[:40])}\" adında bir il ya da ilçe bulamadım. Konumunuzu gönderin ya da 'Çankaya, Ankara' gibi yazın.", LOCATION_KEYBOARD)
        if len(matches) > 1:
            options = "\n".join(f"• {escape(m.name)}" for m in matches[:8])
            return (f"Birden fazla yer buldum, hangisi?\n{options}\n\nİlçe ve il adını birlikte yazın.",)
        place = matches[0]
        return self._add(session, chat_id, place.name, place.lat, place.lon, DEFAULT_RADIUS_KM, now)

    def _add(self, session: Session, chat_id: int, name: str, lat: float, lon: float, radius: int, now: datetime) -> tuple:
        lat, lon = round(lat, 2), round(lon, 2)  # about a kilometer: all that is kept of the place
        existing = session.scalars(select(AlertSubscription).where(AlertSubscription.chat_id == chat_id)).all()
        if any(s.lat == lat and s.lon == lon for s in existing):
            return (f"{escape(name)} zaten listenizde.",)
        if len(existing) >= MAX_PLACES:
            return (f"En fazla {MAX_PLACES} yer ekleyebilirsiniz. /durdur <no> ile birini silin.",)
        session.add(AlertSubscription(chat_id=chat_id, name=name[:60], lat=lat, lon=lon, radius_km=radius, created_at=now, last_seen_at=now))
        # Only fires seen from now on are announced, not the ones already on the map.
        return (
            f"Tamam: <b>{escape(name)}</b> çevresinde {radius} km içinde uydu yeni bir yangın sıcaklığı görürse size yazacağım.\n\n"
            "Uydular bir yeri günde birkaç kez tarar ve veri yaklaşık 3 saat geç gelir; sessizlik yangın olmadığı anlamına gelmez. "
            + DISCLAIMER,
        )

    def _list(self, session: Session, chat_id: int) -> str:
        subs = session.scalars(select(AlertSubscription).where(AlertSubscription.chat_id == chat_id).order_by(AlertSubscription.id)).all()
        if not subs:
            return "Kayıtlı yeriniz yok. Konumunuzu gönderin ya da /yer yazın."
        return "Yerleriniz:\n" + "\n".join(f"{i}. {escape(s.name)} · {s.radius_km} km" for i, s in enumerate(subs, 1))

    def _radius(self, session: Session, chat_id: int, argument: str) -> str:
        parts = argument.split()
        subs = session.scalars(select(AlertSubscription).where(AlertSubscription.chat_id == chat_id).order_by(AlertSubscription.id)).all()
        try:
            number, km = int(parts[0]), int(parts[1])
            if number < 1:
                raise IndexError
            sub = subs[number - 1]
        except (ValueError, IndexError):
            return "Şöyle yazın: /yaricap 1 25 (1. yer için 25 km). Numaraları /liste gösterir."
        sub.radius_km = min(max(km, MIN_RADIUS_KM), MAX_RADIUS_KM)
        return f"{escape(sub.name)} için yarıçap {sub.radius_km} km oldu."

    def _stop(self, session: Session, chat_id: int, argument: str) -> str:
        subs = session.scalars(select(AlertSubscription).where(AlertSubscription.chat_id == chat_id).order_by(AlertSubscription.id)).all()
        if not argument:
            for sub in subs:
                session.delete(sub)
            return "Tüm yerleriniz silindi; artık mesaj göndermeyeceğim."
        try:
            number = int(argument)
            if number < 1:
                raise IndexError
            sub = subs[number - 1]
        except (ValueError, IndexError):
            return "Hangi yeri? /liste numaraları gösterir; tümünü silmek için sadece /durdur yazın."
        session.delete(sub)
        return f"{escape(sub.name)} silindi."

    def _touch(self, session: Session, chat_id: int, now: datetime) -> None:
        for sub in session.scalars(select(AlertSubscription).where(AlertSubscription.chat_id == chat_id)):
            sub.last_seen_at = now

    def _forget(self, chat_id: int) -> None:
        """The person blocked the bot: remove what is kept about them."""
        with self._sessions() as session:
            session.execute(delete(AlertSubscription).where(AlertSubscription.chat_id == chat_id))
            session.commit()

    # ---- telling people ---------------------------------------------------------------------------

    def dispatch(self) -> int:
        """Announce detections that arrived since the last call. Returns how many messages were sent."""
        with self._lock:
            return self._dispatch()

    def _dispatch(self) -> int:
        now = self._clock()
        sent = 0
        with self._sessions() as session:
            state = session.get(BotState, "last_dispatch")
            if state is None:  # the first run: start from now so nothing old is announced
                session.add(BotState(key="last_dispatch", value=now.isoformat()))
                session.commit()
                return 0
            since = datetime.fromisoformat(state.value)
            fresh = session.scalars(
                select(FireDetection).where(
                    FireDetection.ingested_at > since,
                    FireDetection.acquired_at > now - timedelta(hours=12),
                    FireDetection.confidence_level != "low",
                    FireDetection.frp >= MIN_FRP_MW,
                )
            ).all()
            fixed = self._fixed_cells(session) if fresh else set()
            fresh = [d for d in fresh if cell_of(d.latitude, d.longitude) not in fixed]
            state.value = now.isoformat()
            session.commit()
            if fresh:
                sent += self._tell_subscribers(session, fresh, now)
                sent += self._tell_channel(session, fresh, now)
            session.execute(delete(AlertSubscription).where(AlertSubscription.last_seen_at < now - INACTIVE_AFTER))
            session.execute(delete(AlertSent).where(AlertSent.sent_at < now - timedelta(days=2)))
            session.commit()
        return sent

    def _tell_subscribers(self, session: Session, fresh: list[FireDetection], now: datetime) -> int:
        sent = 0
        for sub in session.scalars(select(AlertSubscription)).all():
            near = []
            for d in fresh:
                km = haversine_km(sub.lat, sub.lon, d.latitude, d.longitude)
                if km <= sub.radius_km:
                    near.append((km, d))
            if not near:
                continue
            near.sort(key=lambda item: item[0])
            cells = {cell_of(d.latitude, d.longitude) for _, d in near}
            key = ",".join(f"{c[0]}:{c[1]}" for c in sorted(cells)[:3])
            recent = session.scalar(
                select(func.count()).select_from(AlertSent).where(AlertSent.sub_id == sub.id, AlertSent.cell == key, AlertSent.sent_at > now - COOLDOWN)
            )
            if recent:
                continue
            km, closest = near[0]
            status = self._tg.send(sub.chat_id, self._message(sub, km, closest, len(near), now))
            if status == "blocked":
                self._forget(sub.chat_id)
                continue
            if status == "sent":
                sent += 1
                session.merge(AlertSent(sub_id=sub.id, cell=key, sent_at=now))
                sub.last_alert_at = now
                session.commit()
        return sent

    def _message(self, sub: AlertSubscription, km: float, closest: FireDetection, count: int, now: datetime) -> str:
        direction = wind.compass_name(wind.bearing(sub.lat, sub.lon, closest.latitude, closest.longitude))
        age = max(0, int((now - closest.acquired_at).total_seconds() // 60))
        age_text = f"{age} dk" if age < 60 else f"{age // 60} sa {age % 60} dk"
        lines = [
            f"🔥 <b>{escape(sub.name)}</b> çevresinde ({sub.radius_km} km) uydu yeni yangın sıcaklığı gördü.",
            f"En yakını {km:.1f} km {direction} yönünde, {age_text} önce görüldü; toplam {count} tespit.",
        ]
        weather = self._weather_for(sub.lat, sub.lon)
        if weather and weather[1] is not None:
            direction_from, speed = weather
            relation = wind.relation((sub.lat, sub.lon), (closest.latitude, closest.longitude), direction_from, speed)
            if relation == "toward":
                lines.append(f"Rüzgar {wind.from_name(direction_from)} {speed:.0f} km/sa esiyor: şu an yangından size doğru.")
            elif relation == "across":
                lines.append("Rüzgar yangına göre yan yönde esiyor.")
            elif relation == "away":
                lines.append("Rüzgar şu an yangından uzağa esiyor.")
        base = self._site_url()
        if base:
            lines.append(f'<a href="{escape(base)}/?lat={sub.lat:.4f}&amp;lon={sub.lon:.4f}&amp;r={sub.radius_km}">Haritada gör</a>')
        lines.append("")
        lines.append(DISCLAIMER)
        return "\n".join(lines)

    def _tell_channel(self, session: Session, fresh: list[FireDetection], now: datetime) -> int:
        """Big new fires, once each, on the public channel (when one is set)."""
        if not self._channel:
            return 0
        from kivilcim.zones import find_zones

        sent = 0
        for zone in find_zones(fresh):
            if zone.total_frp < self._channel_min_frp:
                continue
            key = f"{zone.center_lat:.1f}:{zone.center_lon:.1f}:{now.date()}"
            if session.scalar(select(func.count()).select_from(AlertSent).where(AlertSent.sub_id == CHANNEL, AlertSent.cell == key)):
                continue
            base = self._site_url()
            link = f'\n<a href="{escape(base)}/?lat={zone.center_lat:.4f}&amp;lon={zone.center_lon:.4f}&amp;r=30">Haritada gör</a>' if base else ""
            text = (
                f"🔥 <b>{escape(zone.district)}, {escape(zone.province)}</b>: uydu {zone.detections} yeni yangın sıcaklığı gördü "
                f"(toplam {zone.total_frp:.0f} MW).{link}\n\n{DISCLAIMER}"
            )
            channel = int(self._channel) if self._channel.lstrip("-").isdigit() else self._channel
            if self._tg.send(channel, text) == "sent":
                sent += 1
                session.merge(AlertSent(sub_id=CHANNEL, cell=key, sent_at=now))
                session.commit()
        return sent


class BotPoller(threading.Thread):
    """Reads what people send to the bot, one long poll after another, until stopped."""

    def __init__(self, service: AlertService, telegram: Telegram, session_factory: sessionmaker[Session]) -> None:
        super().__init__(name="telegram-bot", daemon=True)
        self._service = service
        self._tg = telegram
        self._sessions = session_factory
        self._halt = threading.Event()

    def _offset(self) -> int:
        with self._sessions() as session:
            row = session.get(BotState, "offset")
            return int(row.value) if row else 0

    def _save_offset(self, offset: int) -> None:
        with self._sessions() as session:
            session.merge(BotState(key="offset", value=str(offset)))
            session.commit()

    def poll_once(self, timeout: int = 25) -> int:
        """One round: fetch the waiting messages and answer them. Returns how many there were."""
        offset = self._offset()
        updates = self._tg.updates(offset, timeout)
        if updates is None:
            raise ConnectionError("Telegram did not answer")  # the loop backs off instead of asking again at once
        for update in updates:
            try:
                self._service.handle(update)
            except Exception:
                log.exception("could not answer a message to the bot")
            offset = max(offset, update["update_id"] + 1)
        if updates:
            self._save_offset(offset)
        return len(updates)

    def run(self) -> None:
        delay = 1.0
        while not self._halt.is_set():
            try:
                got = self.poll_once()
                delay = 1.0
                if not got and self._halt.wait(0.2):
                    break
            except Exception as error:
                if isinstance(error, ConnectionError):
                    log.warning("the Telegram bot cannot reach Telegram (is the token right?); trying again in %.0f s", delay)
                else:
                    log.exception("the bot's polling failed")
                if self._halt.wait(delay):
                    break
                delay = min(delay * 2, 60.0)

    def stop(self) -> None:
        self._halt.set()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
