"""Ricerca delle offerte freelance con Claude + web search, poi estrazione strutturata e ordinamento."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from anthropic import AsyncAnthropic

from .config import AREA_PRIORITY, COMPANY_PRIORITY, DURATION_PRIORITY, MIN_MATCH_SCORE
from .cv import build_profile
from .db import Store, job_key, posting_key
from .models import Job, JobList, Profile
from .scoring import assess_postings
from .sources import FREELANCE_RE, Posting, collect_postings

log = logging.getLogger(__name__)

MAX_CONTINUATIONS = 5

SEARCH_SYSTEM = """\
Sei un career advisor che cerca offerte di lavoro freelance in rete per un singolo professionista.
Cerchi solo annunci reali, attuali e ancora aperti, con un link diretto all'annuncio. \
Non inventare mai offerte, aziende, compensi o link: se un dato non c'è nell'annuncio, dillo.\
"""

SEARCH_PROMPT = """\
Oggi è {today}. Cerca in rete offerte di lavoro freelance / a partita IVA / contract compatibili \
con questo profilo.

<profilo>
{profile}
</profilo>

<requisiti>
- Modalità: solo remoto o ibrido. Escludi le offerte solo in sede, tranne se a Milano.
- Area, in ordine di priorità: 1) Milano  2) resto d'Italia  3) Europa (remoto).
- Compenso target: {rate_min}–{rate_max} € al giorno. Includi anche le offerte senza compenso \
indicato; escludi quelle chiaramente sotto i {rate_min} €/giorno.
- Durata, in ordine di priorità: progetti brevi (fino a 3 mesi), poi lunghi (oltre 6 mesi), poi medi (3-6 mesi).
- Tipo di azienda, in ordine di priorità: agenzie di comunicazione, medie e grandi imprese, società di consulenza.
- Solo annunci pubblicati di recente (ultime 2-3 settimane) e ancora aperti.
{preferences}
</requisiti>

<gia_visti>
Queste offerte le ho già mostrate al candidato: non riproporle.
{seen}
</gia_visti>

<scartati>
Il candidato ha scartato offerte come queste: evita offerte simili.
{dismissed}
</scartati>

Come procedere:
1. Fai più ricerche con query diverse (italiano e inglese, ruoli diversi del profilo) su siti di annunci \
e piattaforme freelance, ad esempio LinkedIn Jobs, Indeed, InfoJobs, Malt, freelancermap, \
Welcome to the Jungle, Hays, Michael Page, Randstad, e le pagine carriere di agenzie e società di consulenza.
2. Apri con web_fetch le pagine dei singoli annunci più promettenti (non le pagine di elenco), per \
verificare che siano aperti, recenti e davvero freelance/partita IVA, e per leggere durata, compenso e \
data. Se apri una pagina di elenco, usala per trovare i link ai singoli annunci e apri quelli.
3. Scarta le offerte che dalla pagina risultano chiuse, vecchie o solo da dipendente.
4. Se una pagina non si apre (es. richiede login), puoi includere l'offerta solo se i risultati di \
ricerca danno abbastanza dettagli, segnalandola come "non verificata".

Alla fine scrivi l'elenco delle offerte (al massimo {max_jobs}, le più compatibili, prima le verificate). \
Per ciascuna indica: titolo, azienda e tipo di azienda, luogo, modalità (remoto/ibrido/in sede), \
durata, compenso, data di pubblicazione, link diretto all'annuncio scritto per intero, sito di provenienza, \
se l'hai verificata aprendo la pagina, e un punteggio di compatibilità 0-100 con una frase di motivazione.
"""

EXTRACT_PROMPT = """\
Qui sotto c'è il resoconto di una ricerca di offerte di lavoro freelance. \
Estrai ogni offerta nel formato richiesto, senza aggiungere, inventare o completare dati che non ci sono \
(usa null per i campi mancanti).
I link possono essere nel testo oppure nelle note "[fonti: ...]" subito dopo la frase che descrive \
l'offerta: per ogni offerta usa il link che corrisponde a quell'annuncio (preferisci la pagina \
dell'annuncio rispetto a una pagina di elenco). Scarta solo le voci per cui non c'è alcun link.

Regole di classificazione:
- area: "milano" se a Milano o provincia; "italia" se altrove in Italia o remoto per aziende italiane; \
"europa" se in un altro paese europeo o remoto UE; altrimenti "altro".
- duration_type: "breve" fino a 3 mesi, "media" da 3 a 6 mesi, "lunga" oltre 6 mesi, "non_indicata" se manca.
- verified: true solo se il resoconto dice che la pagina dell'annuncio è stata aperta e verificata.
- daily_rate_*: in euro al giorno. Converti tariffe orarie (x8) o mensili (/20) se espresse chiaramente.

<resoconto>
{report}
</resoconto>
"""


def _profile_text(profile: Profile) -> str:
    return (
        f"{profile.full_name} — {profile.headline} ({profile.years_experience} anni)\n"
        f"{profile.summary}\n"
        f"Ruoli: {', '.join(profile.target_roles)}\n"
        f"Competenze principali: {', '.join(profile.core_skills)}\n"
        f"Altre competenze: {', '.join(profile.secondary_skills)}\n"
        f"Settori: {', '.join(profile.sectors)}\n"
        f"Lingue: {', '.join(profile.languages)}\n"
        f"Keyword IT: {', '.join(profile.search_keywords_it)}\n"
        f"Keyword EN: {', '.join(profile.search_keywords_en)}"
    )


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "(nessuna)"


def sort_key(job: Job) -> tuple:
    return (
        not job.verified,
        AREA_PRIORITY.index(job.area),
        DURATION_PRIORITY.index(job.duration_type),
        COMPANY_PRIORITY.index(job.company_type),
        -job.match_score,
    )


def filter_and_sort(jobs: list[Job], rate_min: int) -> list[Job]:
    kept = []
    for j in jobs:
        if j.match_score < MIN_MATCH_SCORE:
            continue
        if j.work_mode == "in_sede" and j.area != "milano":
            continue
        top_rate = j.daily_rate_max or j.daily_rate_min
        if top_rate is not None and top_rate < rate_min:
            continue
        kept.append(j)
    return sorted(kept, key=sort_key)


async def _run_web_search(
    client: AsyncAnthropic, model: str, prompt: str, max_uses: int, fetch_max_uses: int
) -> str:
    messages: list[dict] = [{"role": "user", "content": prompt}]
    tools = [
        {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": max_uses,
            "user_location": {
                "type": "approximate",
                "city": "Milan",
                "country": "IT",
                "timezone": "Europe/Rome",
            },
        },
        {
            "type": "web_fetch_20260209",
            "name": "web_fetch",
            "max_uses": fetch_max_uses,
            # Limita i token letti per pagina: per un annuncio bastano, e tengono basso il costo.
            "max_content_tokens": 6000,
        },
    ]
    response = None
    for _ in range(MAX_CONTINUATIONS + 1):
        async with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            system=SEARCH_SYSTEM,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            # Caching automatico: con la web search attiva il server aggiunge un punto di cache dopo
            # ogni risultato, così ogni passaggio del ciclo rilegge il contesto precedente al 10% del prezzo.
            cache_control={"type": "ephemeral"},
        ) as stream:
            response = await stream.get_final_message()
        if response.stop_reason != "pause_turn":
            break
        # Il loop lato server ha raggiunto il limite di iterazioni: si riprende da dove si è fermato.
        messages = [messages[0], {"role": "assistant", "content": response.content}]

    if response is None or response.stop_reason == "refusal":
        raise RuntimeError("La ricerca è stata rifiutata dal modello.")
    report = report_with_sources(response.content)
    stu = response.usage.server_tool_use
    log.info(
        "Ricerca completata: stop=%s ricerche=%s pagine_aperte=%s caratteri=%d "
        "input=%d cache_write=%d cache_read=%d output=%d",
        response.stop_reason,
        getattr(stu, "web_search_requests", "?"), getattr(stu, "web_fetch_requests", "?"), len(report),
        response.usage.input_tokens,
        response.usage.cache_creation_input_tokens or 0,
        response.usage.cache_read_input_tokens or 0,
        response.usage.output_tokens,
    )
    return report


def report_with_sources(content: list) -> str:
    """Testo finale con i link delle citazioni in linea: con la web search gli URL degli annunci
    stanno nelle citazioni, non nel testo, e senza di loro l'estrazione non trova i link."""
    parts: list[str] = []
    for block in content:
        if block.type != "text":
            continue
        parts.append(block.text)
        urls = []
        for c in block.citations or []:
            url = getattr(c, "url", None)
            if url and url not in urls:
                urls.append(url)
        if urls:
            parts.append(" [fonti: " + " ; ".join(urls) + "]")
    return "".join(parts).strip()


async def _extract_jobs(client: AsyncAnthropic, model: str, report: str) -> list[Job]:
    response = await client.messages.parse(
        model=model,
        max_tokens=16000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": EXTRACT_PROMPT.format(report=report)}],
        output_format=JobList,
    )
    if response.parsed_output is None:
        log.warning("Estrazione fallita: stop=%s", response.stop_reason)
        return []
    return response.parsed_output.jobs


def _is_employee_only(p: Posting) -> bool:
    """Contratto indicato esplicitamente e nessun segnale freelance: inutile farlo valutare a Claude."""
    contract = p.details.get("contratto", "").lower()
    if not contract or "determinare" in contract:
        return False
    return not FREELANCE_RE.search(contract)


async def _ensure_match_terms(client: AsyncAnthropic, store: Store, model: str, profile: Profile) -> Profile:
    if profile.match_terms:
        return profile
    content = store.get("cv_content")
    if content:
        log.info("Profilo senza match_terms: lo rigenero dal CV")
        profile = await build_profile(client, model, content, store.get("preferences", []))
        store.set_profile(profile)
    if not profile.match_terms:
        profile.match_terms = [s.lower() for s in profile.core_skills]
    return profile


async def find_jobs(
    client: AsyncAnthropic,
    store: Store,
    model: str,
    max_uses: int,
    fetch_max_uses: int = 10,
    max_jobs: int = 10,
    debug_dir: Path | None = None,
    web_search: bool = True,
) -> list[tuple[str, Job]]:
    """Esegue una ricerca completa e restituisce solo le offerte nuove, ordinate, con la loro chiave."""
    profile = store.get_profile()
    if profile is None:
        raise RuntimeError("Nessun CV caricato. Inviami il tuo CV (docx o pdf) per iniziare.")
    profile = await _ensure_match_terms(client, store, model, profile)

    rate_min = store.get("rate_min")
    rate_max = store.get("rate_max")
    prefs = store.get("preferences", [])
    profile_text = _profile_text(profile)

    # 1) Fonti strutturate (feed e API): gratuite, dati affidabili con link diretto.
    postings = await collect_postings(profile.match_terms, profile.search_keywords_it[:2] + profile.search_keywords_en[:2])
    unique: dict[str, Posting] = {}
    for p in postings:
        key = posting_key(p.title, p.company)
        if key in unique or store.is_known(key) or _is_employee_only(p):
            continue
        unique[key] = p
    log.info("Fonti strutturate: %d annunci, %d nuovi da valutare", len(postings), len(unique))
    candidates = list(unique.values())
    jobs = await assess_postings(client, model, candidates, profile_text, rate_min, rate_max, prefs)

    # 2) Ricerca web: integra con agenzie, pagine carriere e piattaforme non coperte dalle fonti.
    if web_search:
        dismissed = [f"{j.title} — {j.company}" for j in store.jobs_by_status("dismissed", 20)]
        seen = store.recent_titles() + [f"{p.title} — {p.company}" for p in candidates]
        prompt = SEARCH_PROMPT.format(
            today=date.today().isoformat(),
            profile=profile_text,
            rate_min=rate_min,
            rate_max=rate_max,
            preferences=("Indicazioni aggiuntive del candidato:\n" + _bullets(prefs)) if prefs else "",
            seen=_bullets(seen),
            dismissed=_bullets(dismissed),
            max_jobs=max_jobs,
        )
        report = await _run_web_search(client, model, prompt, max_uses, fetch_max_uses)
        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / "last_report.md").write_text(report, encoding="utf-8")
        if report:
            jobs += await _extract_jobs(client, model, report)

    kept = filter_and_sort(jobs, rate_min)
    log.info("Offerte: valutate=%d dopo_filtri=%d", len(jobs), len(kept))
    for j in jobs:
        if j not in kept:
            log.info("  scartata: %s — %s (match=%d, %s, %s, rate=%s)", j.title, j.company,
                     j.match_score, j.area, j.work_mode, j.daily_rate_max or j.daily_rate_min)

    fresh: list[tuple[str, Job]] = []
    seen_now: set[str] = set()
    for job in kept:
        key = job_key(job)
        if key in seen_now or store.is_known(key):
            continue
        seen_now.add(key)
        fresh.append((key, job))
    return fresh[:max_jobs]
