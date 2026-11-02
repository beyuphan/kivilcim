import pytest

from kivilcim import __main__ as entry


def test_help_lists_the_options(capsys):
    with pytest.raises(SystemExit) as exc:
        entry.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for option in ("--host", "--port", "--no-ingest", "--access-log"):
        assert option in out


def test_requests_are_not_logged_unless_asked(monkeypatch):
    seen = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: seen.append(kw["access_log"]))
    monkeypatch.setattr(entry, "create_app", lambda **kw: object())
    entry.main([])
    entry.main(["--access-log"])
    assert seen == [False, True]  # addresses carry the coordinates people look up


def test_main_starts_the_server_with_the_timer_unless_told_not_to(monkeypatch):
    started = {}

    def fake_run(app, host, port, log_level, access_log):
        started.update(host=host, port=port)

    monkeypatch.setattr(entry.uvicorn, "run", fake_run)
    seen = []
    monkeypatch.setattr(entry, "create_app", lambda **kw: seen.append(kw["run_scheduler"]) or object())

    assert entry.main([]) == 0
    assert started == {"host": "127.0.0.1", "port": 8000}
    assert entry.main(["--no-ingest", "--port", "9000", "--host", "0.0.0.0"]) == 0
    assert started == {"host": "0.0.0.0", "port": 9000}
    assert seen == [True, False]


def test_a_bad_port_is_rejected():
    with pytest.raises(SystemExit) as exc:
        entry.main(["--port", "abc"])
    assert exc.value.code == 2
