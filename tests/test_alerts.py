import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from kivilcim import wind
from kivilcim.alerts import (
    CHANNEL,
    MAX_PLACES,
    AlertService,
    BotPoller,
    Telegram,
    nearest_name,
    resolve_place,
)
from kivilcim.api import create_app
from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.models import AlertSent, AlertSubscription, BotState, FireDetection

UTC = timezone.utc
NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
TOKEN = "123:SECRET"


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'a.db'}")
    init_db(engine)
    return make_session_factory(engine)


class Telegram_:
    """A stand-in for the Telegram Bot API: records what the bot sends and serves queued messages."""

    def __init__(self):
        self.sent = []
        self.queue = []
        self.fail_with = None

    def handler(self, request):
        method = str(request.url).rsplit("/", 1)[1]
        body = json.loads(request.content or b"{}")
        if method == "getUpdates":
            updates, self.queue = [u for u in self.queue if u["update_id"] >= body["offset"]], []
            return httpx.Response(200, json={"ok": True, "result": updates})
        if method == "sendMessage":
            if self.fail_with:
                return httpx.Response(self.fail_with, json={"ok": False, "error_code": self.fail_with, "description": "x"})
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {}})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "kivilcim_bot"}})
        return httpx.Response(404)

    def say(self, text=None, chat=1, location=None, update_id=1, kind="private"):
        message = {"chat": {"id": chat, "type": kind}}
        if text is not None:
            message["text"] = text
        if location:
            message["location"] = {"latitude": location[0], "longitude": location[1]}
        self.queue.append({"update_id": update_id, "message": message})


@pytest.fixture
def telegram():
    with respx.mock:
        fake = Telegram_()
        respx.route(host="api.telegram.org").mock(side_effect=fake.handler)
        yield fake


def service(session_factory, weather=None, fixed=lambda s: set(), **kw):
    client = httpx.Client()
    return AlertService(
        session_factory, Telegram(TOKEN, client), lambda: NOW, lambda: "https://kivilcim.example",
        weather or (lambda lat, lon: None), fixed, **kw,
    )


def talk(svc, fake, text=None, **kw):
    fake.say(text, **kw)
    for update in list(fake.queue):
        svc.handle(update)
    fake.queue.clear()
    return fake.sent[-1]["text"] if fake.sent else None


def subs(session_factory):
    with session_factory() as s:
        return s.scalars(select(AlertSubscription)).all()


def add_fire(session_factory, lat, lon, frp=40.0, ingested=NOW, hours_ago=2.0, level="nominal", source="VIIRS_SNPP_NRT"):
    with session_factory() as s:
        s.add(
            FireDetection(
                source=source, satellite="N", instrument="VIIRS", latitude=lat, longitude=lon,
                acquired_at=NOW - timedelta(hours=hours_ago), confidence_raw="n", confidence_level=level,
                brightness=330.0, frp=frp, ingested_at=ingested,
            )
        )
        s.commit()


# ---- finding the place a person means --------------------------------------


def test_places_are_found_by_province_or_district_in_any_spelling():
    assert [p.name for p in resolve_place("adana")] == ["Adana"]
    assert [p.name for p in resolve_place("ŞANLIURFA")] == ["Şanlıurfa"]
    assert [p.name for p in resolve_place("Çankaya, Ankara")] == ["Çankaya, Ankara"]
    assert [p.name for p in resolve_place("cankaya ankara")] == ["Çankaya, Ankara"]
    assert resolve_place("") == [] and resolve_place("xyzzy") == []


def test_a_district_name_used_in_several_provinces_gives_several_matches():
    assert len(resolve_place("Gölbaşı")) > 1


def test_the_nearest_district_names_a_point():
    assert nearest_name(39.93, 32.86) == "Çankaya, Ankara" or "Ankara" in nearest_name(39.93, 32.86)
    assert nearest_name(10.0, 10.0) == "Seçilen yer"


# ---- talking to the bot ----------------------------------------------------


def test_the_first_message_explains_what_the_bot_does_and_asks_for_a_location(session_factory, telegram):
    svc = service(session_factory)
    text = talk(svc, telegram, "/start")
    assert "yeni bir yangın" in text and "112" in text
    assert telegram.sent[-1]["reply_markup"]["keyboard"][0][0]["request_location"] is True


def test_sending_a_location_adds_the_place_rounded_to_a_kilometer(session_factory, telegram):
    svc = service(session_factory)
    text = talk(svc, telegram, location=(39.93456, 32.86789))
    (sub,) = subs(session_factory)
    assert (sub.lat, sub.lon, sub.radius_km, sub.chat_id) == (39.93, 32.87, 15, 1)
    assert "15 km" in text and "geç gelir" in text


def test_a_place_can_be_added_by_name_and_listed_and_stopped(session_factory, telegram):
    svc = service(session_factory)
    assert "Adana" in talk(svc, telegram, "/yer Adana")
    assert "Çankaya, Ankara" in talk(svc, telegram, "Çankaya, Ankara")
    assert "1. Adana" in talk(svc, telegram, "/liste") and "2. Çankaya, Ankara" in telegram.sent[-1]["text"]
    assert "silindi" in talk(svc, telegram, "/durdur 1")
    assert [s.name for s in subs(session_factory)] == ["Çankaya, Ankara"]
    assert "silindi" in talk(svc, telegram, "/durdur")
    assert subs(session_factory) == []


def test_the_site_link_adds_a_place_with_its_radius(session_factory, telegram):
    svc = service(session_factory)
    talk(svc, telegram, "/start p3993_3286_25")
    (sub,) = subs(session_factory)
    assert (sub.lat, sub.lon, sub.radius_km) == (39.93, 32.86, 25)


@pytest.mark.parametrize("payload", ["p1_2_3", "pxx_yy_zz", "p3993_3286", "p100_100_10"])
def test_a_broken_site_link_falls_back_to_the_welcome(session_factory, telegram, payload):
    svc = service(session_factory)
    assert "yeni bir yangın" in talk(svc, telegram, f"/start {payload}")
    assert subs(session_factory) == []


def test_the_radius_can_be_changed_within_limits(session_factory, telegram):
    svc = service(session_factory)
    talk(svc, telegram, "/yer Adana")
    assert "25 km" in talk(svc, telegram, "/yaricap 1 25")
    assert "50 km" in talk(svc, telegram, "/yaricap 1 500")
    assert "3 km" in talk(svc, telegram, "/yaricap 1 0")
    assert "Şöyle yazın" in talk(svc, telegram, "/yaricap 9 10")
    assert "Şöyle yazın" in talk(svc, telegram, "/yaricap 0 10")


def test_a_chat_can_watch_only_a_few_places_and_the_same_place_only_once(session_factory, telegram):
    svc = service(session_factory)
    names = ["Adana", "Ankara", "Bursa", "Konya", "Mersin", "Izmir"]
    for name in names[:MAX_PLACES]:
        talk(svc, telegram, f"/yer {name}")
    assert "En fazla" in talk(svc, telegram, f"/yer {names[MAX_PLACES]}")
    assert "zaten" in talk(svc, telegram, "/yer Adana")
    assert len(subs(session_factory)) == MAX_PLACES


def test_unknown_places_and_commands_get_a_helpful_answer(session_factory, telegram):
    svc = service(session_factory)
    assert "bulamadım" in talk(svc, telegram, "Atlantis")
    assert "/yardim" in talk(svc, telegram, "/bilinmeyen")
    assert "Birden fazla" in talk(svc, telegram, "Gölbaşı")


def test_groups_are_not_served(session_factory, telegram):
    svc = service(session_factory)
    talk(svc, telegram, "/yer Adana", kind="group")
    assert telegram.sent == [] and subs(session_factory) == []


# ---- telling people --------------------------------------------------------


def watch(session_factory, telegram, name="Çankaya, Ankara", chat=1, radius=15):
    svc = service(session_factory, weather=lambda lat, lon: (225.0, 25.0))
    talk(svc, telegram, f"/yer {name}", chat=chat)
    if radius != 15:
        talk(svc, telegram, f"/yaricap 1 {radius}", chat=chat)
    telegram.sent.clear()
    return svc


def start(svc):
    assert svc.dispatch() == 0  # the first run only marks the time: nothing old is announced


def test_a_new_detection_near_a_place_is_announced_once_with_distance_direction_and_wind(session_factory, telegram):
    svc = watch(session_factory, telegram)
    start(svc)
    sub = subs(session_factory)[0]
    add_fire(session_factory, sub.lat - 0.05, sub.lon - 0.05, ingested=NOW + timedelta(minutes=5))  # south west
    assert svc.dispatch() == 1
    text = telegram.sent[-1]["text"]
    assert "Çankaya, Ankara" in text and "güneybatı yönünde" in text and "2 sa 0 dk önce" in text
    assert "güneybatıdan 25 km/sa" in text and "yangından size doğru" in text
    assert "https://kivilcim.example/?lat=" in text and "112" in text


def test_the_same_fire_is_not_announced_again_within_six_hours(session_factory, telegram):
    svc = watch(session_factory, telegram)
    start(svc)
    sub = subs(session_factory)[0]
    add_fire(session_factory, sub.lat - 0.05, sub.lon, ingested=NOW + timedelta(minutes=5))
    assert svc.dispatch() == 1
    add_fire(session_factory, sub.lat - 0.05, sub.lon + 0.001, ingested=NOW + timedelta(minutes=10), hours_ago=1.0)
    # a later detection in the same spot: the cooldown holds (the clock is fixed, so it is the same moment)
    with session_factory() as s:
        s.execute(select(BotState))  # keep the state readable
    assert svc.dispatch() == 0


def test_detections_outside_the_radius_or_weak_or_low_confidence_or_old_are_ignored(session_factory, telegram):
    svc = watch(session_factory, telegram, radius=5)
    start(svc)
    sub = subs(session_factory)[0]
    later = NOW + timedelta(minutes=5)
    add_fire(session_factory, sub.lat + 0.5, sub.lon, ingested=later)  # about 55 km away
    add_fire(session_factory, sub.lat + 0.01, sub.lon, frp=1.0, ingested=later)  # too weak
    add_fire(session_factory, sub.lat + 0.01, sub.lon + 0.01, level="low", ingested=later)  # low confidence
    add_fire(session_factory, sub.lat - 0.01, sub.lon, ingested=later, hours_ago=20)  # seen too long ago
    add_fire(session_factory, sub.lat - 0.01, sub.lon - 0.01, ingested=NOW - timedelta(hours=1))  # was there before
    assert svc.dispatch() == 0 and telegram.sent == []


def test_heat_sources_that_burn_all_the_time_are_not_announced(session_factory, telegram):
    from kivilcim.fixed import cell_of

    sub_lat, sub_lon = 39.93, 32.86
    svc_fixed = service(
        session_factory, weather=lambda a, b: None, fixed=lambda s: {cell_of(sub_lat + 0.02, sub_lon)}
    )
    talk(svc_fixed, telegram, "/yer Çankaya, Ankara")
    telegram.sent.clear()
    start(svc_fixed)
    add_fire(session_factory, sub_lat + 0.02, sub_lon, ingested=NOW + timedelta(minutes=5))
    assert svc_fixed.dispatch() == 0


def test_only_the_chats_with_a_place_nearby_are_told(session_factory, telegram):
    svc = service(session_factory, weather=lambda a, b: None)
    talk(svc, telegram, "/yer Çankaya, Ankara", chat=1)
    talk(svc, telegram, "/yer Seyhan, Adana", chat=2)
    telegram.sent.clear()
    start(svc)
    near = [s for s in subs(session_factory) if s.chat_id == 1][0]
    add_fire(session_factory, near.lat + 0.03, near.lon, ingested=NOW + timedelta(minutes=5))
    assert svc.dispatch() == 1 and telegram.sent[-1]["chat_id"] == 1


def test_a_calm_or_unknown_wind_adds_no_claim(session_factory, telegram):
    svc = service(session_factory, weather=lambda a, b: None)
    talk(svc, telegram, "/yer Çankaya, Ankara")
    telegram.sent.clear()
    start(svc)
    sub = subs(session_factory)[0]
    add_fire(session_factory, sub.lat + 0.03, sub.lon, ingested=NOW + timedelta(minutes=5))
    svc.dispatch()
    assert "Rüzgar" not in telegram.sent[-1]["text"]


def test_a_person_who_blocked_the_bot_is_forgotten(session_factory, telegram):
    svc = watch(session_factory, telegram)
    start(svc)
    sub = subs(session_factory)[0]
    add_fire(session_factory, sub.lat + 0.03, sub.lon, ingested=NOW + timedelta(minutes=5))
    telegram.fail_with = 403
    svc.dispatch()
    assert subs(session_factory) == []


def test_a_telegram_outage_loses_nothing_and_a_later_run_tries_again(session_factory, telegram):
    svc = watch(session_factory, telegram)
    start(svc)
    sub = subs(session_factory)[0]
    add_fire(session_factory, sub.lat + 0.03, sub.lon, ingested=NOW + timedelta(minutes=5))
    telegram.fail_with = 500
    assert svc.dispatch() == 0 and len(subs(session_factory)) == 1
    with session_factory() as s:
        assert s.scalars(select(AlertSent)).all() == []  # nothing was recorded as sent


def test_inactive_subscriptions_are_removed_after_a_year(session_factory, telegram):
    svc = watch(session_factory, telegram)
    start(svc)
    with session_factory() as s:
        s.execute(AlertSubscription.__table__.update().values(last_seen_at=NOW - timedelta(days=400)))
        s.commit()
    svc.dispatch()
    assert subs(session_factory) == []


def test_big_new_fires_are_posted_once_on_the_channel(session_factory, telegram):
    svc = service(session_factory, channel="@kivilcim_yangin", channel_min_frp=300.0)
    start(svc)
    for i in range(10):
        add_fire(session_factory, 37.45 + 0.001 * i, 35.45, frp=60.0, ingested=NOW + timedelta(minutes=5))
    assert svc.dispatch() == 1
    post = telegram.sent[-1]
    assert post["chat_id"] == "@kivilcim_yangin" and "10 yeni yangın sıcaklığı" in post["text"] and "toplam 600 MW" in post["text"]
    add_fire(session_factory, 37.45, 35.451, frp=60.0, ingested=NOW + timedelta(minutes=10))
    assert svc.dispatch() == 0  # the same zone is not posted twice in a day
    with session_factory() as s:
        assert [r.sub_id for r in s.scalars(select(AlertSent))] == [CHANNEL]


def test_small_fires_are_not_posted_on_the_channel(session_factory, telegram):
    svc = service(session_factory, channel="@k", channel_min_frp=300.0)
    start(svc)
    add_fire(session_factory, 37.45, 35.45, frp=50.0, ingested=NOW + timedelta(minutes=5))
    assert svc.dispatch() == 0


# ---- reading the messages ---------------------------------------------------


def test_the_poller_answers_waiting_messages_and_remembers_where_it_stopped(session_factory, telegram):
    svc = service(session_factory)
    poller = BotPoller(svc, Telegram(TOKEN, httpx.Client()), session_factory)
    telegram.say("/yer Adana", update_id=7)
    assert poller.poll_once(timeout=0) == 1
    assert len(subs(session_factory)) == 1
    with session_factory() as s:
        assert s.get(BotState, "offset").value == "8"
    assert poller.poll_once(timeout=0) == 0  # nothing new, and the old message is not read again


def test_an_unreachable_telegram_makes_the_poller_wait_instead_of_asking_again_at_once(session_factory):
    with respx.mock:
        respx.route(host="api.telegram.org").mock(return_value=httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}))
        poller = BotPoller(service(session_factory), Telegram(TOKEN, httpx.Client()), session_factory)
        with pytest.raises(ConnectionError):
            poller.poll_once(timeout=0)


def test_a_message_that_breaks_the_handler_does_not_stop_the_bot(session_factory, telegram):
    svc = service(session_factory)
    poller = BotPoller(svc, Telegram(TOKEN, httpx.Client()), session_factory)
    telegram.queue.append({"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "location": {"oops": 1}}})
    telegram.say("/yer Adana", update_id=2)
    assert poller.poll_once(timeout=0) == 2
    assert len(subs(session_factory)) == 1


def test_the_token_never_appears_in_what_is_logged_or_returned(session_factory, telegram, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    svc = service(session_factory)
    talk(svc, telegram, "/yer Adana")
    assert TOKEN not in caplog.text


# ---- the app -----------------------------------------------------------------


def test_the_site_says_whether_the_bot_is_available(session_factory):
    off = TestClient(create_app(session_factory, now=lambda: NOW, settings=Settings(_env_file=None))).get("/alerts/bot").json()
    assert off == {"enabled": False, "username": None}
    on = TestClient(
        create_app(session_factory, now=lambda: NOW, settings=Settings(_env_file=None, telegram_bot_token=TOKEN, telegram_bot_username="kivilcim_bot"))
    ).get("/alerts/bot").json()
    assert on == {"enabled": True, "username": "kivilcim_bot"}


# ---- the wind geometry in Python matches the page ---------------------------


def test_wind_relations_match_the_page():
    place, fire_sw = (39.0, 35.0), (38.9, 34.9)
    assert wind.compass_name(wind.bearing(*place, *fire_sw)) == "güneybatı"
    assert wind.relation(place, fire_sw, 225, 25) == "toward"
    assert wind.relation(place, fire_sw, 45, 25) == "away"
    assert wind.relation(place, fire_sw, 135, 25) == "across"
    assert wind.relation(place, fire_sw, 225, 3) == "calm" and wind.relation(place, fire_sw, None, None) == "calm"
    assert wind.angle_diff(350, 10) == 20 and wind.from_name(225) == "güneybatıdan"
