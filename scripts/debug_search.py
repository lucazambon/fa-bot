"""Esegue una ricerca completa senza Telegram e mostra cosa succede in ogni fase.
Non segna le offerte come inviate. Uso: uv run python scripts/debug_search.py"""
import asyncio
import logging
import re
import os

from anthropic import AsyncAnthropic

from fa_bot.config import PROJECT_ROOT
from fa_bot.db import Store
from fa_bot.formatting import run_summary
from fa_bot.search import find_jobs

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


async def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    store = Store(PROJECT_ROOT / "data" / "fa_bot.sqlite3")
    result = await find_jobs(
        AsyncAnthropic(), store,
        os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
        int(os.getenv("WEB_SEARCH_MAX_USES", "8")),
        fetch_max_uses=int(os.getenv("WEB_FETCH_MAX_USES", "10")),
        web_search=os.getenv("WEB_SEARCH_ENABLED", "true").lower() == "true",
        usd_eur=float(os.getenv("USD_EUR_RATE", "0.92")),
        debug_dir=PROJECT_ROOT / "data",
    )
    jobs = result.jobs
    print(f"\n=== {len(jobs)} offerte nuove ===")
    for _, j in jobs:
        print(f"- {j.source} [{j.match_score}{'✓' if j.verified else '?'}] {j.title} — {j.company} | {j.area}/{j.work_mode}/{j.duration_type} | {j.url}")
    print(f"\nResoconto completo: {PROJECT_ROOT / 'data' / 'last_report.md'}")

    print("\n" + re.sub(r"<[^>]+>", "", run_summary(result.stats)))


asyncio.run(main())
