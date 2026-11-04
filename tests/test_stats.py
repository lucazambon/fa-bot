from collections import Counter
from types import SimpleNamespace

import pytest

from fa_bot.formatting import fmt_duration, fmt_eur, fmt_tokens, run_summary
from fa_bot.search import filter_and_sort
from fa_bot.stats import Phase, RunStats, SourceStat

from test_core import make_job


def api_usage(inp=0, write=0, read=0, out=0, searches=0, fetches=0):
    return SimpleNamespace(
        input_tokens=inp,
        cache_creation_input_tokens=write,
        cache_read_input_tokens=read,
        output_tokens=out,
        server_tool_use=SimpleNamespace(web_search_requests=searches, web_fetch_requests=fetches),
    )


def test_phase_cost_opus():
    ph = Phase("x", "claude-opus-5-5")
    ph.add_usage(api_usage(inp=1_000_000, write=1_000_000, read=1_000_000, out=1_000_000, searches=100))
    # 4 (input) + 5 (scrittura cache) + 0,20 (lettura cache) + 20 (output) = 29,20 $ + 100 ricerche × 0,01 $
    assert ph.token_cost_usd == pytest.approx(29.20)
    assert ph.search_fee_usd == pytest.approx(1.0)
    assert ph.cost_usd == pytest.approx(30.20)


def test_phase_without_model_is_free_and_handles_none_usage():
    ph = Phase("fonti")
    ph.add_usage(SimpleNamespace(input_tokens=None, output_tokens=5, server_tool_use=None))
    assert ph.cost_usd == 0 and ph.usage.output == 5


def test_unknown_model_uses_conservative_prices():
    ph = Phase("x", "claude-nuovo-modello")
    ph.add_usage(api_usage(inp=1_000_000))
    assert ph.token_cost_usd == pytest.approx(4.0)


def test_run_totals_and_eur():
    stats = RunStats(usd_eur=0.5)
    with stats.phase("a", "claude-opus-5-5") as ph:
        ph.add_usage(api_usage(inp=1_000_000, searches=10))
    with stats.phase("b", "claude-sonnet-5-5") as ph:
        ph.add_usage(api_usage(out=1_000_000))
    assert stats.cost_usd == pytest.approx(4.0 + 0.10 + 10.0)
    assert stats.cost_eur == pytest.approx(stats.cost_usd * 0.5)
    assert stats.usage.total_tokens == 2_000_000
    assert all(p.seconds >= 0 for p in stats.phases)


def test_filter_counts_first_reason():
    reasons = Counter()
    jobs = [
        make_job(title="ok"),
        make_job(title="basso", match_score=10),
        make_job(title="sede", area="italia", work_mode="in_sede"),
        make_job(title="rate", daily_rate_max=100),
        make_job(title="basso e sede", match_score=10, area="italia", work_mode="in_sede"),
    ]
    assert [j.title for j in filter_and_sort(jobs, 250, reasons)] == ["ok"]
    assert (reasons["below_match"], reasons["on_site"], reasons["below_rate"]) == (2, 1, 1)


def test_formatters():
    assert fmt_duration(45) == "45 s"
    assert fmt_duration(151) == "2 min 31 s"
    assert fmt_tokens(950) == "950" and fmt_tokens(28_412) == "28,4k" and fmt_tokens(1_250_000) == "1,25M"
    assert fmt_eur(0.4712) == "0,47 €" and fmt_eur(0.001) == "meno di 0,01 €"


def test_run_summary_contents():
    stats = RunStats(usd_eur=0.92)
    stats.sources["iProgrammatori"] = SourceStat(scanned=488, relevant=43)
    stats.sources["Arbeitnow"] = SourceStat(error="timeout")
    with stats.phase("Fonti strutturate"):
        pass
    with stats.phase("Ricerca web", "claude-opus-5-5") as ph:
        ph.add_usage(api_usage(inp=24, write=17_835, read=131_168, out=3_840, searches=8, fetches=10))
    stats.counts.update(
        source_total=53, to_assess=21, known=12, employee_only=9, duplicates=3, web_found=4,
        not_freelance=3, below_match=14, shown=5,
    )
    stats.elapsed = 151
    text = run_summary(stats)
    for expected in (
        "iProgrammatori: 488 annunci letti → 43 pertinenti",
        "Arbeitnow: non disponibile",
        "8 ricerche, 10 pagine aperte → 4 offerte trovate",
        "53 annunci pertinenti → 21 da valutare",
        "12 già inviate",
        "Valutati da Claude: 25",
        "3 non freelance, 14 match sotto 55%",
        "Proposte: 5",
        "Fonti strutturate: 0 s · gratis",
        "Tempo totale</b>: 2 min 31 s",
        "€",
        "ricerche web 0,07 €",
    ):
        assert expected in text, expected
    assert len(text) < 4000  # limite dei messaggi Telegram
