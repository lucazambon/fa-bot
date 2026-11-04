import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from fa_bot.bot import JOB_NAME, STARTUP_DELAY, build_app, cycle_delay, schedule_digests, restart_cycle
from fa_bot.config import DEFAULT_INTERVAL_HOURS, Settings


def test_cycle_delay():
    now = 1_000_000.0
    assert cycle_delay(now, 72, now) == 72 * 3600  # appena inviato
    assert cycle_delay(now - 10 * 3600, 72, now) == 62 * 3600  # mancano 62 ore
    assert cycle_delay(now - 100 * 3600, 72, now) == STARTUP_DELAY  # scaduto (Mac spento): parte subito dopo l'avvio


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    settings = Settings(
        telegram_token="123:abc", owner_id=None, claude_model="claude-opus-5-5",
        web_search_max_uses=8, web_fetch_max_uses=10, web_search_enabled=True, usd_eur_rate=0.92,
        timezone=ZoneInfo("Europe/Rome"), db_path=tmp_path / "t.db",
    )
    return build_app(settings)


def pending(app):
    return app.job_queue.get_jobs_by_name(JOB_NAME)


def test_first_schedule_starts_cycle_now(app):
    store = app.bot_data["store"]
    before = time.time()
    next_run = schedule_digests(app)
    assert before <= store.get("last_run") <= time.time()
    assert len(pending(app)) == 1
    hours = (next_run - datetime.now(next_run.tzinfo)).total_seconds() / 3600
    assert hours == pytest.approx(DEFAULT_INTERVAL_HOURS, abs=0.01)


def test_rescheduling_never_duplicates_the_job(app):
    for _ in range(3):
        schedule_digests(app)
    assert len(pending(app)) == 1


def test_restart_does_not_reset_the_cycle(app):
    store = app.bot_data["store"]
    store.set("last_run", time.time() - 10 * 3600)  # ultimo invio 10 ore fa
    next_run = schedule_digests(app)
    hours = (next_run - datetime.now(next_run.tzinfo)).total_seconds() / 3600
    assert hours == pytest.approx(DEFAULT_INTERVAL_HOURS - 10, abs=0.01)


def test_changing_frequency_restarts_cycle_from_now(app):
    store = app.bot_data["store"]
    store.set("last_run", time.time() - 50 * 3600)
    store.set("interval_hours", 12)
    next_run = restart_cycle(app)
    hours = (next_run - datetime.now(next_run.tzinfo)).total_seconds() / 3600
    assert hours == pytest.approx(12, abs=0.01)


def test_paused_has_no_job_and_retry_delay_is_used(app):
    store = app.bot_data["store"]
    store.set("paused", True)
    assert schedule_digests(app) is None and not pending(app)
    store.set("paused", False)
    next_run = schedule_digests(app, delay=3600)
    assert (next_run - datetime.now(next_run.tzinfo)).total_seconds() == pytest.approx(3600, abs=5)
    assert len(pending(app)) == 1


# --- comando /frequenza -----------------------------------------------------

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fa_bot.bot import cmd_frequency


def send_frequency(app, args):
    app.bot_data["store"].set("owner_id", 1)
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1), effective_message=message, message=message, callback_query=None
    )
    ctx = SimpleNamespace(args=args, bot_data=app.bot_data, application=app)
    asyncio.run(cmd_frequency(update, ctx))
    return message.reply_text.call_args.args[0]


def test_frequency_command_sets_interval_and_reschedules(app):
    text = send_frequency(app, ["36h"])
    assert "ogni 36 ore" in text and "Prossimo invio" in text
    assert app.bot_data["store"].get("interval_hours") == 36
    assert len(pending(app)) == 1


def test_frequency_command_accepts_days(app):
    send_frequency(app, ["3d"])
    assert app.bot_data["store"].get("interval_hours") == 72


def test_frequency_command_rejects_invalid_and_keeps_setting(app):
    app.bot_data["store"].set("interval_hours", 48)
    text = send_frequency(app, ["boh"])
    assert "non valida" in text
    assert app.bot_data["store"].get("interval_hours") == 48


def test_frequency_command_shows_monthly_cost_estimate(app):
    store = app.bot_data["store"]
    store.set("interval_hours", 36)
    store.set("last_cost_eur", 0.77)
    text = send_frequency(app, [])
    assert "ogni 36 ore" in text
    assert "15,40 € al mese" in text  # 0,77 € × (720 h / 36 h)
