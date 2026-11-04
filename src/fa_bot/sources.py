"""Fonti strutturate di annunci: feed RSS e API pubbliche.

Ogni fonte restituisce `Posting` grezzi; la valutazione rispetto al profilo la fa Claude in `scoring.py`.
Le fonti che richiedono una chiave (Adzuna, Jooble) si attivano solo se la chiave è nel .env.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "fa-bot/0.1 (bot personale di ricerca offerte freelance)"
MAX_AGE_DAYS = 21
DESC_CHARS = 1500

FREELANCE_RE = re.compile(
    r"freelance|free-lance|partita\s*iva|p\.\s?iva|libero\s+professionista|collaborazione|"
    r"contract(or)?\b|consulente\s+esterno|\bb2b\b",
    re.I,
)


@dataclass
class Posting:
    source: str
    title: str
    company: str
    url: str
    location: str = ""
    published: datetime | None = None
    description: str = ""
    details: dict[str, str] = field(default_factory=dict)  # contratto, modalità, retribuzione…

    @property
    def text(self) -> str:
        return f"{self.title} {self.description} {' '.join(self.details.values())}"


def clean_html(raw: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def is_recent(p: Posting, now: datetime | None = None) -> bool:
    if p.published is None:
        return True
    now = now or datetime.now(timezone.utc)
    return now - p.published <= timedelta(days=MAX_AGE_DAYS)


def matches_terms(p: Posting, terms: list[str]) -> bool:
    text = p.text.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", text) for t in terms)


# --- iProgrammatori ------------------------------------------------------

IPROG_FEED = "https://www.iprogrammatori.it/rss/offerte-di-lavoro.xml"
IPROG_FIELDS = {
    "luogo": r"Luogo lavoro\s+(.+?)\s+Modalità di lavoro",
    "modalita": r"Modalità di lavoro\s+(.+?)\s+Impegno",
    "impegno": r"Impegno\s+(.+?)\s+(?:Retribuzione|Posti|Tipo di contratto)",
    "retribuzione": r"Retribuzione \(lorda\)\s+(.+?)\s+(?:Posti|Tipo di contratto)",
    "contratto": r"Tipo di contratto\s+(.+?)\s+Descrizione dell'annuncio",
    "aggiornato": r"Aggiornato il\s+(\d{2}/\d{2}/\d{4})",
}
IPROG_DETAIL_LIMIT = 50


def parse_iprog_feed(xml_text: str) -> list[Posting]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.findall("./channel/item"):
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            published = None
        out.append(
            Posting(
                source="iProgrammatori",
                title=(item.findtext("title") or "").strip(),
                company=(item.findtext("{http://purl.org/dc/elements/1.1/}creator") or "").strip(),
                url=(item.findtext("link") or "").strip(),
                published=published,
                description=clean_html(item.findtext("description") or "")[:DESC_CHARS],
            )
        )
    return out


def parse_iprog_detail(page_html: str) -> tuple[dict[str, str], str]:
    text = re.sub(r"\s+", " ", clean_html(page_html))
    details = {}
    for key, pattern in IPROG_FIELDS.items():
        m = re.search(pattern, text)
        if m:
            details[key] = m.group(1).strip()[:200]
    desc = ""
    m = re.search(r"Descrizione dell'annuncio di lavoro\s+(.+)", text)
    if m:
        desc = m.group(1)[:DESC_CHARS]
    return details, desc


async def fetch_iprogrammatori(client: httpx.AsyncClient, terms: list[str]) -> list[Posting]:
    resp = await client.get(IPROG_FEED)
    resp.raise_for_status()
    feed = parse_iprog_feed(resp.text)
    relevant = [p for p in feed if is_recent(p) and matches_terms(p, terms)]
    log.info("iProgrammatori: %d nel feed, %d pertinenti", len(feed), len(relevant))

    # La pagina dell'annuncio ha i campi strutturati (contratto, modalità, retribuzione).
    for p in relevant[:IPROG_DETAIL_LIMIT]:
        try:
            page = await client.get(p.url)
            page.raise_for_status()
            p.details, desc = parse_iprog_detail(page.text)
            if desc:
                p.description = desc
            p.location = p.details.get("luogo", "")
        except httpx.HTTPError as exc:
            log.warning("iProgrammatori: dettaglio non letto %s (%s)", p.url, exc)
        await asyncio.sleep(1)  # cortesia verso il sito
    return relevant[:IPROG_DETAIL_LIMIT]


# --- Remotive ------------------------------------------------------------

async def fetch_remotive(client: httpx.AsyncClient, terms: list[str]) -> list[Posting]:
    resp = await client.get("https://remotive.com/api/remote-jobs", params={"category": "software-dev"})
    resp.raise_for_status()
    out = []
    for j in resp.json().get("jobs", []):
        loc = j.get("candidate_required_location") or ""
        if loc and not re.search(r"worldwide|europe|emea|italy|anywhere|cet", loc, re.I):
            continue
        p = Posting(
            source="Remotive",
            title=j.get("title", ""),
            company=j.get("company_name", ""),
            url=j.get("url", ""),
            location=f"Remoto ({loc})" if loc else "Remoto",
            published=_iso(j.get("publication_date")),
            description=clean_html(j.get("description", ""))[:DESC_CHARS],
            details={"contratto": j.get("job_type", ""), "retribuzione": j.get("salary", "")},
        )
        if is_recent(p) and matches_terms(p, terms):
            out.append(p)
    log.info("Remotive: %d pertinenti", len(out))
    return out


# --- Arbeitnow -----------------------------------------------------------

async def fetch_arbeitnow(client: httpx.AsyncClient, terms: list[str]) -> list[Posting]:
    out = []
    for page in (1, 2, 3):
        resp = await client.get("https://www.arbeitnow.com/api/job-board-api", params={"page": page})
        resp.raise_for_status()
        for j in resp.json().get("data", []):
            if not j.get("remote"):
                continue
            p = Posting(
                source="Arbeitnow",
                title=j.get("title", ""),
                company=j.get("company_name", ""),
                url=j.get("url", ""),
                location=f"Remoto ({j.get('location', '')})",
                published=datetime.fromtimestamp(j["created_at"], timezone.utc) if j.get("created_at") else None,
                description=clean_html(j.get("description", ""))[:DESC_CHARS],
                details={"contratto": ", ".join(j.get("job_types") or [])},
            )
            if is_recent(p) and matches_terms(p, terms):
                out.append(p)
    log.info("Arbeitnow: %d pertinenti", len(out))
    return out


# --- Adzuna (chiave gratuita: developer.adzuna.com) -----------------------

async def fetch_adzuna(client: httpx.AsyncClient, terms: list[str], queries: list[str]) -> list[Posting]:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return []
    out = []
    for q in queries[:4]:
        resp = await client.get(
            "https://api.adzuna.com/v1/api/jobs/it/search/1",
            params={
                "app_id": app_id, "app_key": app_key, "what": q, "results_per_page": 30,
                "max_days_old": MAX_AGE_DAYS, "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        for j in resp.json().get("results", []):
            lo, hi = j.get("salary_min"), j.get("salary_max")
            p = Posting(
                source="Adzuna",
                title=j.get("title", ""),
                company=(j.get("company") or {}).get("display_name", ""),
                url=j.get("redirect_url", ""),
                location=(j.get("location") or {}).get("display_name", ""),
                published=_iso(j.get("created")),
                description=clean_html(j.get("description", ""))[:DESC_CHARS],
                details={
                    "contratto": " ".join(filter(None, [j.get("contract_type"), j.get("contract_time")])),
                    "retribuzione": f"{lo}–{hi} €/anno" if lo or hi else "",
                },
            )
            if matches_terms(p, terms):
                out.append(p)
    log.info("Adzuna: %d pertinenti", len(out))
    return out


# --- Jooble (chiave gratuita: jooble.org/api/about) -----------------------

async def fetch_jooble(client: httpx.AsyncClient, terms: list[str], queries: list[str]) -> list[Posting]:
    key = os.getenv("JOOBLE_API_KEY")
    if not key:
        return []
    out = []
    for q in queries[:4]:
        resp = await client.post(f"https://it.jooble.org/api/{key}", json={"keywords": q, "location": "Italia"})
        resp.raise_for_status()
        for j in resp.json().get("jobs", []):
            p = Posting(
                source="Jooble",
                title=clean_html(j.get("title", "")),
                company=j.get("company", ""),
                url=j.get("link", ""),
                location=j.get("location", ""),
                published=_iso(j.get("updated")),
                description=clean_html(j.get("snippet", ""))[:DESC_CHARS],
                details={"contratto": j.get("type", ""), "retribuzione": j.get("salary", "")},
            )
            if is_recent(p) and matches_terms(p, terms):
                out.append(p)
    log.info("Jooble: %d pertinenti", len(out))
    return out


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def collect_postings(terms: list[str], queries: list[str]) -> list[Posting]:
    """Interroga tutte le fonti in parallelo; una fonte che fallisce non blocca le altre."""
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    ) as client:
        tasks = {
            "iProgrammatori": fetch_iprogrammatori(client, terms),
            "Remotive": fetch_remotive(client, terms),
            "Arbeitnow": fetch_arbeitnow(client, terms),
            "Adzuna": fetch_adzuna(client, terms, queries),
            "Jooble": fetch_jooble(client, terms, queries),
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    postings: list[Posting] = []
    for name, res in zip(tasks, results):
        if isinstance(res, BaseException):
            log.warning("Fonte %s non disponibile: %s", name, res)
        else:
            postings.extend(res)
    return postings
