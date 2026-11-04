"""Configurazione letta da variabili d'ambiente / file .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Requisiti di default (modificabili dal bot con /rate e /preferenze)
DEFAULT_TIMES = ["09:00"]
DEFAULT_RATE_MIN = 250
DEFAULT_RATE_MAX = 300

# Ordine di priorità: indice più basso = più in alto nella lista
AREA_PRIORITY = ["milano", "italia", "europa", "altro"]
DURATION_PRIORITY = ["breve", "lunga", "media", "non_indicata"]
COMPANY_PRIORITY = ["agenzia_comunicazione", "media_grande_impresa", "consulenza", "altro"]

MIN_MATCH_SCORE = 55


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    owner_id: int | None
    claude_model: str
    web_search_max_uses: int
    web_fetch_max_uses: int
    web_search_enabled: bool
    timezone: ZoneInfo
    db_path: Path


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN mancante: copia .env.example in .env e compilalo.")

    owner = os.getenv("TELEGRAM_OWNER_ID", "").strip()
    db_path = Path(os.getenv("DB_PATH", "data/fa_bot.sqlite3"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    return Settings(
        telegram_token=token,
        owner_id=int(owner) if owner else None,
        claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5").strip(),
        web_search_max_uses=int(os.getenv("WEB_SEARCH_MAX_USES", "8")),
        web_fetch_max_uses=int(os.getenv("WEB_FETCH_MAX_USES", "10")),
        web_search_enabled=os.getenv("WEB_SEARCH_ENABLED", "true").strip().lower() in ("1", "true", "si", "sì", "yes"),
        timezone=ZoneInfo(os.getenv("TIMEZONE", "Europe/Rome")),
        db_path=db_path,
    )
