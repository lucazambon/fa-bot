"""Testi dei messaggi Telegram (HTML)."""

from __future__ import annotations

import re
from html import escape

from .config import MAX_INTERVAL_HOURS, MIN_INTERVAL_HOURS, MIN_MATCH_SCORE
from .models import Job, Profile
from .stats import RunStats

AREA_LABEL = {"milano": "📍 Milano", "italia": "🇮🇹 Italia", "europa": "🇪🇺 Europa", "altro": "🌍 Altro"}
MODE_LABEL = {"remoto": "🏠 Remoto", "ibrido": "🔀 Ibrido", "in_sede": "🏢 In sede", "non_indicato": "❔ Modalità n.d."}
DURATION_LABEL = {"breve": "Breve", "media": "Media", "lunga": "Lunga", "non_indicata": "Durata n.d."}
COMPANY_LABEL = {
    "agenzia_comunicazione": "Agenzia di comunicazione",
    "media_grande_impresa": "Media/grande impresa",
    "consulenza": "Consulenza",
    "altro": "Altro",
}

INTERVAL_RE = re.compile(r"^(\d+)(h|ore|ora|o|d|g|gg|giorni|giorno)?$")
DAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MONTHS = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]


def parse_interval(args: list[str]) -> int:
    """'36', '36h', '36 ore' -> 36; '3d', '3 giorni' -> 72. Solleva ValueError se non è valido."""
    raw = "".join(args).strip().lower()
    m = INTERVAL_RE.match(raw)
    if not m:
        raise ValueError(raw)
    number, unit = int(m.group(1)), m.group(2) or "h"
    hours = number * 24 if unit[0] in "dg" else number
    if not MIN_INTERVAL_HOURS <= hours <= MAX_INTERVAL_HOURS:
        raise ValueError(raw)
    return hours


def fmt_interval(hours: int) -> str:
    if hours % 24 == 0:
        days = hours // 24
        return f"{hours} ore ({days} {'giorno' if days == 1 else 'giorni'})"
    return "1 ora" if hours == 1 else f"{hours} ore"


def fmt_when(dt) -> str:
    return f"{DAYS[dt.weekday()]} {dt.day} {MONTHS[dt.month - 1]}, {dt:%H:%M}"


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


def fmt_duration(seconds: float) -> str:
    sec = round(seconds)
    if sec < 60:
        return f"{sec} s"
    minutes, rest = divmod(sec, 60)
    return f"{minutes} min {rest:02d} s"


def fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}".replace(".", ",") + "k"
    return f"{n / 1_000_000:.2f}".replace(".", ",") + "M"


def fmt_eur(value: float) -> str:
    if value < 0.005:
        return "meno di 0,01 €"
    return f"{value:.2f}".replace(".", ",") + " €"


def fmt_usd(value: float) -> str:
    return "$" + f"{value:.2f}"


def run_summary(stats: RunStats) -> str:
    """Riepilogo del lavoro di selezione di un giro: fonti, filtri, fasi, tempo, token e costi."""
    c = stats.counts
    lines = ["📊 <b>Lavoro di selezione</b>", "", "<b>🔎 Fonti analizzate</b>"]
    for name, src in stats.sources.items():
        if src.error:
            lines.append(f"• {escape(name)}: non disponibile")
        else:
            lines.append(f"• {escape(name)}: {src.scanned} annunci letti → {src.relevant} pertinenti")
    web = stats.phase_named("Ricerca web")
    if web:
        lines.append(
            f"• Ricerca web: {web.usage.web_searches} ricerche, {web.usage.web_fetches} pagine aperte"
            f" → {c['web_found']} offerte trovate"
        )

    lines += ["", "<b>🧹 Selezione</b>"]
    skipped = [
        f"{c[key]} {label}"
        for key, label in (("duplicates", "doppioni"), ("known", "già inviate"), ("employee_only", "solo da dipendente"))
        if c[key]
    ]
    line = f"• Dalle fonti: {c['source_total']} annunci pertinenti → {c['to_assess']} da valutare"
    lines.append(line + (f" (scartati: {', '.join(skipped)})" if skipped else ""))
    lines.append(f"• Valutati da Claude: {c['to_assess'] + c['web_found']}")
    dropped = [
        f"{c[key]} {label}"
        for key, label in (
            ("not_freelance", "non freelance"),
            ("below_match", f"match sotto {MIN_MATCH_SCORE}%"),
            ("on_site", "solo in sede fuori Milano"),
            ("below_rate", "rate sotto il minimo"),
            ("repeated", "già proposte"),
        )
        if c[key]
    ]
    if dropped:
        lines.append(f"• Scartati: {', '.join(dropped)}")
    lines.append(f"• <b>Proposte: {c['shown']}</b>")

    lines += ["", "<b>⚙️ Fasi</b>"]
    for ph in stats.phases:
        cost = "gratis" if ph.cost_usd == 0 else fmt_eur(stats.eur(ph.cost_usd))
        lines.append(f"• {escape(ph.label)}: {fmt_duration(ph.seconds)} · {cost}")

    u = stats.usage
    lines += [
        "",
        f"⏱ <b>Tempo totale</b>: {fmt_duration(stats.elapsed)}",
        f"🔤 <b>Token</b>: {fmt_tokens(u.total_tokens)} "
        f"({fmt_tokens(u.input)} input · {fmt_tokens(u.cache_write)} scritti in cache · "
        f"{fmt_tokens(u.cache_read)} letti da cache · {fmt_tokens(u.output)} output)",
        f"💶 <b>Costo</b>: {fmt_eur(stats.cost_eur)} ({fmt_usd(stats.cost_usd)})",
    ]
    if stats.search_fee_usd:
        lines.append(
            f"   token {fmt_eur(stats.eur(stats.token_cost_usd))} · "
            f"ricerche web {fmt_eur(stats.eur(stats.search_fee_usd))}"
        )
    lines.append(f"<i>Stima da listino, cambio 1 $ = {str(stats.usd_eur).replace('.', ',')} €</i>")
    return "\n".join(lines)


HELP = """\
<b>Freelance Careers Advisor</b>

/cerca — avvia subito una ricerca
/frequenza — ogni quante ore inviare il recap · <code>/frequenza 36</code> o <code>/frequenza 3d</code> per cambiarla
/rate — mostra il rate · <code>/rate 250 300</code> per cambiarlo
/cv — mostra il profilo estratto dal CV · invia un nuovo file per aggiornarlo
/preferenze <i>testo</i> — aggiungi un'indicazione (es. <code>/preferenze anche ruoli da Scrum Master</code>)
/preferenze_reset — cancella le indicazioni
/filtri — riepilogo dei criteri di ricerca
/salvati — offerte salvate con ⭐
/pausa · /riprendi — sospendi o riattiva gli invii automatici
"""
