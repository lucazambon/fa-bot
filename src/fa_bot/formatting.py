"""Testi dei messaggi Telegram (HTML)."""

from __future__ import annotations

import re
from html import escape

from .models import Job, Profile

AREA_LABEL = {"milano": "📍 Milano", "italia": "🇮🇹 Italia", "europa": "🇪🇺 Europa", "altro": "🌍 Altro"}
MODE_LABEL = {"remoto": "🏠 Remoto", "ibrido": "🔀 Ibrido", "in_sede": "🏢 In sede", "non_indicato": "❔ Modalità n.d."}
DURATION_LABEL = {"breve": "Breve", "media": "Media", "lunga": "Lunga", "non_indicata": "Durata n.d."}
COMPANY_LABEL = {
    "agenzia_comunicazione": "Agenzia di comunicazione",
    "media_grande_impresa": "Media/grande impresa",
    "consulenza": "Consulenza",
    "altro": "Altro",
}

TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$|^([01]?\d|2[0-3])$")


def parse_times(args: list[str]) -> list[str]:
    """'9 14:30 18.00' -> ['09:00', '14:30', '18:00']. Solleva ValueError se un orario non è valido."""
    out = set()
    for raw in " ".join(args).replace(",", " ").split():
        m = TIME_RE.match(raw.strip())
        if not m:
            raise ValueError(raw)
        if m.group(3) is not None:
            h, mnt = int(m.group(3)), 0
        else:
            h, mnt = int(m.group(1)), int(m.group(2))
        out.add(f"{h:02d}:{mnt:02d}")
    return sorted(out)


def rate_label(job: Job, rate_min: int, rate_max: int) -> str:
    lo, hi = job.daily_rate_min, job.daily_rate_max
    if lo is None and hi is None:
        return "💶 " + (escape(job.rate_text) if job.rate_text else "Rate non indicato")
    lo, hi = lo or hi, hi or lo
    value = f"{lo}€/g" if lo == hi else f"{lo}–{hi}€/g"
    if hi > rate_max:
        tag = " 🔥 sopra target"
    elif lo >= rate_min:
        tag = " ✅ in target"
    else:
        tag = " ⚠️ parzialmente in target"
    return f"💶 {value}{tag}"


def job_message(index: int, job: Job, rate_min: int, rate_max: int) -> str:
    duration = DURATION_LABEL[job.duration_type]
    if job.duration_text:
        duration += f" ({escape(job.duration_text)})"
    lines = [
        f"<b>{index}. {escape(job.title)}</b>",
        f"🏷 {escape(job.company)} · <i>{COMPANY_LABEL[job.company_type]}</i>",
        f"{AREA_LABEL[job.area]} — {escape(job.location)} · {MODE_LABEL[job.work_mode]}",
        f"⏱ {duration}",
        rate_label(job, rate_min, rate_max),
        f"🎯 Match {job.match_score}% — {escape(job.match_reason)}",
    ]
    meta = escape(job.source) + (" · ✅ verificata" if job.verified else " · ⚠️ non verificata")
    if job.posted_date:
        meta += f" · pubblicato {escape(job.posted_date)}"
    lines.append(f"🔗 <a href=\"{escape(job.url, quote=True)}\">Apri annuncio</a> · {meta}")
    return "\n".join(lines)


def profile_message(profile: Profile, preferences: list[str]) -> str:
    text = (
        f"<b>{escape(profile.full_name)}</b> — {escape(profile.headline)}\n"
        f"{profile.years_experience} anni di esperienza\n\n"
        f"{escape(profile.summary)}\n\n"
        f"<b>Ruoli cercati:</b> {escape(', '.join(profile.target_roles))}\n"
        f"<b>Competenze:</b> {escape(', '.join(profile.core_skills))}\n"
        f"<b>Settori:</b> {escape(', '.join(profile.sectors))}\n"
        f"<b>Keyword:</b> {escape(', '.join(profile.search_keywords_it + profile.search_keywords_en))}"
    )
    if preferences:
        text += "\n\n<b>Tue indicazioni:</b>\n" + "\n".join(f"• {escape(p)}" for p in preferences)
    return text


HELP = """\
<b>Freelance Careers Advisor</b>

/cerca — avvia subito una ricerca
/orari — mostra gli orari di invio · <code>/orari 9 14 18</code> per cambiarli
/rate — mostra il rate · <code>/rate 250 300</code> per cambiarlo
/cv — mostra il profilo estratto dal CV · invia un nuovo file per aggiornarlo
/preferenze <i>testo</i> — aggiungi un'indicazione (es. <code>/preferenze anche ruoli da Scrum Master</code>)
/preferenze_reset — cancella le indicazioni
/filtri — riepilogo dei criteri di ricerca
/salvati — offerte salvate con ⭐
/pausa · /riprendi — sospendi o riattiva gli invii automatici
"""
