import pytest

from fa_bot.db import Store, job_key
from fa_bot.formatting import fmt_interval, job_message, parse_interval, rate_label
from fa_bot.models import Job
from fa_bot.search import filter_and_sort


def make_job(**kw) -> Job:
    base = dict(
        title="React Developer",
        company="Acme",
        company_type="media_grande_impresa",
        location="Milano",
        area="milano",
        work_mode="ibrido",
        duration_type="breve",
        duration_text=None,
        daily_rate_min=None,
        daily_rate_max=None,
        rate_text=None,
        match_score=80,
        match_reason="Stack React",
        url="https://example.com/job/1",
        source="LinkedIn",
        posted_date=None,
        verified=True,
    )
    base.update(kw)
    return Job(**base)


def test_parse_interval():
    assert parse_interval(["12"]) == 12
    assert parse_interval(["36h"]) == 36
    assert parse_interval(["36", "ore"]) == 36
    assert parse_interval(["3d"]) == 72
    assert parse_interval(["2", "giorni"]) == 48
    assert parse_interval(["1g"]) == 24
    for bad in (["0"], ["721"], ["abc"], [], ["12x"], ["-5"], ["31d"]):
        with pytest.raises(ValueError):
            parse_interval(bad)


def test_fmt_interval():
    assert fmt_interval(1) == "1 ora"
    assert fmt_interval(36) == "36 ore"
    assert fmt_interval(24) == "24 ore (1 giorno)"
    assert fmt_interval(72) == "72 ore (3 giorni)"


def test_filter_and_sort_priorities():
    jobs = [
        make_job(title="europa", area="europa", work_mode="remoto"),
        make_job(title="media", duration_type="media"),
        make_job(title="lunga", duration_type="lunga"),
        make_job(title="breve"),
        make_job(title="non verificata", verified=False),
        make_job(title="sotto rate", daily_rate_max=200),
        make_job(title="in sede roma", area="italia", work_mode="in_sede"),
        make_job(title="score basso", match_score=30),
    ]
    assert [j.title for j in filter_and_sort(jobs, 250)] == ["breve", "lunga", "media", "europa", "non verificata"]


def test_rate_label():
    assert "non indicato" in rate_label(make_job(), 250, 300)
    assert "in target" in rate_label(make_job(daily_rate_min=250, daily_rate_max=300), 250, 300)
    assert "sopra target" in rate_label(make_job(daily_rate_min=350), 250, 300)


def test_job_message_escapes_html():
    msg = job_message(1, make_job(title="Dev <React> & co"), 250, 300)
    assert "Dev &lt;React&gt; &amp; co" in msg


def test_store_dedup_and_status(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    job = make_job()
    key = job_key(job)
    assert key == job_key(make_job(title="react developer!", url="https://other.site"))
    assert not store.is_known(key)
    store.add_sent(key, job)
    assert store.is_known(key)
    store.set_status(key, "saved")
    assert store.jobs_by_status("saved")[0].title == "React Developer"
    store.set("interval_hours", 72)
    assert store.get("interval_hours") == 72
