"""Valutazione con Claude degli annunci arrivati dalle fonti strutturate."""

from __future__ import annotations

import logging
from typing import Optional

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field

from .models import Area, CompanyType, DurationType, Job, WorkMode
from .sources import Posting
from .stats import RunStats

log = logging.getLogger(__name__)


class Assessment(BaseModel):
    candidate_id: int
    is_freelance: bool = Field(
        description="true se è un incarico freelance / partita IVA / contract / collaborazione, "
        "false se è solo lavoro dipendente"
    )
    company_type: CompanyType
    area: Area
    work_mode: WorkMode
    duration_type: DurationType
    duration_text: Optional[str]
    daily_rate_min: Optional[int]
    daily_rate_max: Optional[int]
    rate_text: Optional[str]
    match_score: int = Field(description="Compatibilità con il profilo, da 0 a 100")
    match_reason: str = Field(description="Una frase in italiano sul perché è (o non è) compatibile")


class Assessments(BaseModel):
    items: list[Assessment]


PROMPT = """\
Valuta questi annunci di lavoro rispetto al profilo di un professionista che cerca incarichi freelance.

<profilo>
{profile}
</profilo>

<requisiti>
- Compenso target: {rate_min}–{rate_max} €/giorno.
- Modalità preferite: remoto o ibrido; in sede solo se a Milano.
- Priorità aree: Milano, poi resto d'Italia, poi Europa.
{preferences}
</requisiti>

Per ogni annuncio restituisci una valutazione (stesso candidate_id). Regole:
- is_freelance: guarda il tipo di contratto e il testo. "Partita Iva" conta come freelance anche se \
accompagnato da "Indeterminato"; "Indeterminato" o "Determinato" da soli no.
- daily_rate_*: in euro al giorno. Converti: annuale ÷ 220, mensile ÷ 20, orario × 8. \
Ignora valori palesemente sbagliati (es. 99.990 al mese) e lascia null.
- area: "milano" se Milano o provincia (Monza e Brianza conta come Milano); "italia" se altrove in Italia \
o remoto per aziende italiane; "europa" se remoto/sede in un altro paese europeo; altrimenti "altro".
- duration_type: "breve" fino a 3 mesi, "media" 3-6 mesi, "lunga" oltre 6 mesi o a tempo indeterminato, \
"non_indicata" se manca.
- match_score: quanto l'annuncio corrisponde a competenze ed esperienza del profilo (stack, seniority, ruolo). \
Uno stack principale diverso (es. Java, .NET, Angular) con il frontend solo secondario vale al massimo 50.

<annunci>
{postings}
</annunci>
"""


def _posting_block(i: int, p: Posting) -> str:
    details = "\n".join(f"{k}: {v}" for k, v in p.details.items() if v)
    date = p.published.date().isoformat() if p.published else "n.d."
    return (
        f'<annuncio candidate_id="{i}">\n'
        f"titolo: {p.title}\nazienda: {p.company}\nluogo: {p.location or 'n.d.'}\n"
        f"pubblicato: {date}\nfonte: {p.source}\n{details}\n"
        f"descrizione: {p.description}\n</annuncio>"
    )


async def assess_postings(
    client: AsyncAnthropic,
    model: str,
    postings: list[Posting],
    profile_text: str,
    rate_min: int,
    rate_max: int,
    preferences: list[str],
    stats: RunStats | None = None,
) -> list[Job]:
    if not postings:
        return []
    prompt = PROMPT.format(
        profile=profile_text,
        rate_min=rate_min,
        rate_max=rate_max,
        preferences=("Indicazioni del candidato:\n" + "\n".join(f"- {p}" for p in preferences)) if preferences else "",
        postings="\n\n".join(_posting_block(i, p) for i, p in enumerate(postings)),
    )
    stats = stats or RunStats()
    with stats.phase("Valutazione annunci", model) as ph:
        response = await client.messages.parse(
            model=model,
            max_tokens=16000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
            output_format=Assessments,
        )
        ph.add_usage(response.usage)
    log.info(
        "Valutazione: %d annunci, input=%d output=%d",
        len(postings), response.usage.input_tokens, response.usage.output_tokens,
    )
    if response.parsed_output is None:
        log.warning("Valutazione fallita: stop=%s", response.stop_reason)
        return []

    jobs = []
    for a in response.parsed_output.items:
        if not 0 <= a.candidate_id < len(postings):
            continue
        p = postings[a.candidate_id]
        if not a.is_freelance:
            stats.counts["not_freelance"] += 1
            log.info("  non freelance: %s — %s", p.title, p.company)
            continue
        jobs.append(
            Job(
                title=p.title,
                company=p.company,
                company_type=a.company_type,
                location=p.location or "n.d.",
                area=a.area,
                work_mode=a.work_mode,
                duration_type=a.duration_type,
                duration_text=a.duration_text,
                daily_rate_min=a.daily_rate_min,
                daily_rate_max=a.daily_rate_max,
                rate_text=a.rate_text or p.details.get("retribuzione") or None,
                match_score=a.match_score,
                match_reason=a.match_reason,
                url=p.url,
                source=p.source,
                posted_date=p.published.date().isoformat() if p.published else None,
                verified=True,  # dati presi direttamente dalla fonte, non dedotti da una ricerca
            )
        )
    return jobs
