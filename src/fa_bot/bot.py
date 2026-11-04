"""Bot Telegram: comandi, caricamento CV, pulsanti sulle offerte e invii programmati."""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from datetime import datetime

from anthropic import AsyncAnthropic
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

from . import formatting as fmt
from .config import (
    DEFAULT_INTERVAL_HOURS,
    DEFAULT_RATE_MAX,
    DEFAULT_RATE_MIN,
    RETRY_SECONDS,
    Settings,
)
from .cv import SUPPORTED, build_profile, cv_to_content
from .db import Store
from .search import find_jobs

log = logging.getLogger(__name__)
JOB_NAME = "digest"
STARTUP_DELAY = 30  # secondi: un invio già scaduto parte poco dopo l'avvio


# --- helper ---------------------------------------------------------------

def _store(ctx: ContextTypes.DEFAULT_TYPE) -> Store:
    return ctx.bot_data["store"]


def _settings(ctx: ContextTypes.DEFAULT_TYPE) -> Settings:
    return ctx.bot_data["settings"]


def _claude(ctx: ContextTypes.DEFAULT_TYPE) -> AsyncAnthropic:
    return ctx.bot_data["claude"]


def owner_only(handler):
    """Il bot è personale: risponde solo al proprietario."""

    @functools.wraps(handler)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        owner = _settings(ctx).owner_id or _store(ctx).get("owner_id")
        if user is None or owner is None or user.id != owner:
            if update.callback_query:
                await update.callback_query.answer("Bot privato.")
            elif update.effective_message:
                await update.effective_message.reply_text("Questo è un bot privato.")
            return
        return await handler(update, ctx)

    return wrapper


def interval_hours(store: Store) -> int:
    return store.get("interval_hours", DEFAULT_INTERVAL_HOURS)


def cycle_delay(last_run: float, hours: int, now: float) -> float:
    """Secondi al prossimo invio: `hours` ore dopo l'ultimo; se è già scaduto, poco dopo l'avvio."""
    return max(last_run + hours * 3600 - now, STARTUP_DELAY)


def schedule_digests(app: Application, delay: float | None = None) -> datetime | None:
    """(Ri)programma il prossimo invio. Un solo invio è in attesa alla volta: ogni giro programma il successivo.

    Il punto di partenza del ciclo è `last_run`, salvato su disco: un riavvio del bot non lo azzera.
    Restituisce quando partirà il prossimo invio, o None se gli invii sono in pausa.
    """
    store: Store = app.bot_data["store"]
    for job in app.job_queue.get_jobs_by_name(JOB_NAME):
        job.schedule_removal()
    app.bot_data["next_run"] = None
    if store.get("paused", False):
        return None
    now = time.time()
    if store.get("last_run") is None:
        store.set("last_run", now)  # primo avvio: il ciclo parte da adesso
    if delay is None:
        delay = cycle_delay(store.get("last_run"), interval_hours(store), now)
    app.job_queue.run_once(
        scheduled_digest,
        when=delay,
        name=JOB_NAME,
        # Se il Mac dorme quando scade, l'invio parte al risveglio invece di saltare.
        job_kwargs={"misfire_grace_time": None},
    )
    next_run = datetime.fromtimestamp(now + delay, app.bot_data["settings"].timezone)
    app.bot_data["next_run"] = next_run
    return next_run


def restart_cycle(app: Application) -> datetime | None:
    """Fa ripartire il ciclo da adesso (nuova frequenza o ripresa dopo una pausa)."""
    app.bot_data["store"].set("last_run", time.time())
    return schedule_digests(app)


def cost_estimate(store: Store) -> str:
    """Costo mensile stimato alla frequenza attuale, in base all'ultimo giro."""
    last = store.get("last_cost_eur")
    if not last:
        return ""
    monthly = last * (24 * 30 / interval_hours(store))
    return f"💶 Costo stimato: circa {fmt.fmt_eur(monthly)} al mese (ultimo giro: {fmt.fmt_eur(last)})"


def schedule_text(app: Application) -> str:
    store: Store = app.bot_data["store"]
    if store.get("paused"):
        return "⏸ Invii automatici in pausa (usa /riprendi)."
    next_run = app.bot_data.get("next_run")
    return "📅 Prossimo invio: " + (fmt.fmt_when(next_run) if next_run else "da programmare")


async def _digest(app: Application, chat_id: int, manual: bool) -> str:
    """Un giro di ricerca. Esito: "done", "error" o "busy" (ce n'è già uno in corso)."""
    store: Store = app.bot_data["store"]
    settings: Settings = app.bot_data["settings"]
    lock: asyncio.Lock = app.bot_data["search_lock"]

    if lock.locked():
        if manual:
            await app.bot.send_message(chat_id, "⏳ C'è già una ricerca in corso, attendi qualche minuto.")
        return "busy"

    async with lock:
        status = await app.bot.send_message(chat_id, "🔎 Sto cercando nuove offerte freelance… (può richiedere qualche minuto)")
        try:
            result = await find_jobs(
                app.bot_data["claude"], store, settings.claude_model, settings.web_search_max_uses,
                fetch_max_uses=settings.web_fetch_max_uses,
                web_search=settings.web_search_enabled,
                debug_dir=settings.db_path.parent,
                usd_eur=settings.usd_eur_rate,
            )
        except Exception as exc:  # noqa: BLE001 - l'errore va mostrato all'utente
            log.exception("Ricerca fallita")
            await status.edit_text(f"❌ Ricerca non riuscita: {exc}", parse_mode=None)
            return "error"

        store.set("last_cost_eur", round(result.stats.cost_eur, 4))
        jobs = result.jobs
        summary = fmt.run_summary(result.stats)
        if not jobs:
            await status.edit_text("😶 Nessuna nuova offerta compatibile in questo giro.\n\n" + summary)
            return "done"

        await status.edit_text(
            f"✨ <b>{len(jobs)} nuove offerte</b> compatibili con il tuo profilo:\n\n{summary}"
        )
        rate_min, rate_max = store.get("rate_min"), store.get("rate_max")
        for i, (key, job) in enumerate(jobs, start=1):
            store.add_sent(key, job)
            keyboard = InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("⭐ Salva", callback_data=f"s:{key}"),
                    InlineKeyboardButton("👎 Non mi interessa", callback_data=f"d:{key}"),
                ]]
            )
            await app.bot.send_message(chat_id, fmt.job_message(i, job, rate_min, rate_max), reply_markup=keyboard)
    return "done"


async def run_digest(app: Application, chat_id: int, manual: bool) -> None:
    """Esegue un giro e programma il successivo: ogni invio fa ripartire il ciclo, anche se lanciato a mano."""
    outcome = await _digest(app, chat_id, manual)
    store: Store = app.bot_data["store"]
    if outcome == "done":
        store.set("last_run", time.time())
        schedule_digests(app)
    elif not manual:
        # Invio automatico fallito o rimandato: si riprova tra poco, senza aspettare un altro intero ciclo.
        schedule_digests(app, delay=RETRY_SECONDS)


async def scheduled_digest(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = _store(ctx).get("chat_id")
    if chat_id and _store(ctx).get_profile():
        await run_digest(ctx.application, chat_id, manual=False)
    else:
        schedule_digests(ctx.application, delay=RETRY_SECONDS)  # CV o chat non ancora configurati


# --- comandi --------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store, settings = _store(ctx), _settings(ctx)
    user = update.effective_user
    owner = settings.owner_id or store.get("owner_id")
    if owner is None:
        store.set("owner_id", user.id)
        owner = user.id
    if user.id != owner:
        await update.message.reply_text("Questo è un bot privato.")
        return

    store.set("chat_id", update.effective_chat.id)
    schedule_digests(ctx.application)
    text = (
        f"Ciao {user.first_name}! 👋\n"
        "Sono il tuo <b>Freelance Careers Advisor</b>: cerco in rete offerte freelance compatibili "
        "con il tuo CV e te le mando in automatico.\n\n"
        f"🕘 Frequenza: ogni {fmt.fmt_interval(interval_hours(store))}\n"
        f"💶 Rate target: {store.get('rate_min')}–{store.get('rate_max')} €/giorno\n\n"
    )
    if store.get_profile() is None:
        text += f"Per iniziare, <b>inviami il tuo CV</b> come file ({', '.join(SUPPORTED)})."
    else:
        text += "Il tuo CV è già caricato. Usa /cerca per una ricerca immediata o /help per i comandi."
    await update.message.reply_text(text)


@owner_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(fmt.HELP)


@owner_only
async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if _store(ctx).get_profile() is None:
        await update.message.reply_text("Prima inviami il tuo CV come file (docx o pdf).")
        return
    ctx.application.create_task(run_digest(ctx.application, update.effective_chat.id, manual=True))


@owner_only
async def cmd_frequency(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(ctx)
    if ctx.args:
        try:
            hours = fmt.parse_interval(ctx.args)
        except ValueError:
            await update.message.reply_text(
                "Frequenza non valida. Indica ogni quante ore (da 1 a 720) o giorni:\n"
                "<code>/frequenza 12</code> · <code>/frequenza 36h</code> · <code>/frequenza 3d</code>"
            )
            return
        store.set("interval_hours", hours)
        restart_cycle(ctx.application)
        head = f"✅ Il recap arriverà ogni {fmt.fmt_interval(hours)}."
    else:
        head = (
            f"🕘 Il recap arriva ogni {fmt.fmt_interval(interval_hours(store))}.\n"
            "Per cambiarlo: <code>/frequenza 36</code> (ore) o <code>/frequenza 3d</code> (giorni)."
        )
    lines = [head, schedule_text(ctx.application), cost_estimate(store)]
    await update.message.reply_text("\n".join(x for x in lines if x))


@owner_only
async def cmd_rate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(ctx)
    if ctx.args:
        try:
            nums = [int(a.replace("€", "")) for a in ctx.args[:2]]
        except ValueError:
            await update.message.reply_text("Esempio: <code>/rate 250 300</code>")
            return
        lo, hi = (nums[0], nums[0]) if len(nums) == 1 else sorted(nums)
        store.set("rate_min", lo)
        store.set("rate_max", hi)
        await update.message.reply_text(f"✅ Rate target aggiornato: {lo}–{hi} €/giorno")
    else:
        await update.message.reply_text(
            f"💶 Rate target: {store.get('rate_min')}–{store.get('rate_max')} €/giorno\n"
            "Per cambiarlo: <code>/rate 250 300</code>"
        )


@owner_only
async def cmd_cv(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(ctx)
    profile = store.get_profile()
    if profile is None:
        await update.message.reply_text(f"Nessun CV caricato. Inviamelo come file ({', '.join(SUPPORTED)}).")
        return
    await update.message.reply_text(
        fmt.profile_message(profile, store.get("preferences", []))
        + "\n\nPer aggiornarlo inviami un nuovo file; per affinarlo usa /preferenze."
    )


async def _rebuild_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE, content: list[dict]) -> None:
    store = _store(ctx)
    waiting = await update.message.reply_text("🧠 Analizzo il CV…")
    try:
        profile = await build_profile(
            _claude(ctx), _settings(ctx).claude_model, content, store.get("preferences", [])
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Analisi CV fallita")
        await waiting.edit_text(f"❌ Analisi non riuscita: {exc}", parse_mode=None)
        return
    store.set_profile(profile)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔎 Cerca ora", callback_data="search")]])
    await waiting.edit_text(
        "✅ <b>Profilo aggiornato</b>\n\n" + fmt.profile_message(profile, store.get("preferences", []))
        + "\n\nSe qualcosa non va, correggilo con /preferenze <i>testo</i>.",
        reply_markup=keyboard,
    )


@owner_only
async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    name = doc.file_name or "cv"
    if not name.lower().endswith(SUPPORTED):
        await update.message.reply_text(f"Formato non supportato. Usa: {', '.join(SUPPORTED)}")
        return
    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        await update.message.reply_text("File troppo grande (max 10 MB).")
        return
    data = bytes(await (await doc.get_file()).download_as_bytearray())
    try:
        content, _ = cv_to_content(name, data)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"Non riesco a leggere il file: {exc}", parse_mode=None)
        return
    _store(ctx).set("cv_content", content)
    _store(ctx).set("chat_id", update.effective_chat.id)
    await _rebuild_profile(update, ctx, content)


@owner_only
async def cmd_preferences(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(ctx)
    note = " ".join(ctx.args).strip()
    prefs: list[str] = store.get("preferences", [])
    if not note:
        listed = "\n".join(f"• {p}" for p in prefs) if prefs else "(nessuna)"
        await update.message.reply_text(
            f"<b>Indicazioni attuali</b>\n{listed}\n\n"
            "Aggiungine una: <code>/preferenze escludi offerte in ambito gaming</code>",
        )
        return
    prefs.append(note)
    store.set("preferences", prefs)
    content = store.get("cv_content")
    if content:
        await _rebuild_profile(update, ctx, content)
    else:
        await update.message.reply_text("✅ Indicazione salvata. Inviami il CV per completare il profilo.")


@owner_only
async def cmd_preferences_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _store(ctx).set("preferences", [])
    content = _store(ctx).get("cv_content")
    if content:
        await _rebuild_profile(update, ctx, content)
    else:
        await update.message.reply_text("✅ Indicazioni cancellate.")


@owner_only
async def cmd_filters(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(ctx)
    prefs = store.get("preferences", [])
    text = (
        "<b>Criteri di ricerca</b>\n"
        "• Modalità: remoto o ibrido (in sede solo a Milano)\n"
        "• Area: Milano → resto d'Italia → Europa\n"
        f"• Rate: {store.get('rate_min')}–{store.get('rate_max')} €/giorno (mostrate anche le offerte senza rate)\n"
        "• Durata: brevi → lunghe → medie\n"
        "• Aziende: agenzie di comunicazione → medie/grandi imprese → consulenza\n"
        f"• Frequenza: ogni {fmt.fmt_interval(interval_hours(store))}"
    )
    if prefs:
        text += "\n\n<b>Tue indicazioni</b>\n" + "\n".join(f"• {p}" for p in prefs)
    await update.message.reply_text(text)


@owner_only
async def cmd_saved(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(ctx)
    saved = store.jobs_by_status("saved")
    if not saved:
        await update.message.reply_text("Nessuna offerta salvata. Usa ⭐ sotto un'offerta per salvarla.")
        return
    rate_min, rate_max = store.get("rate_min"), store.get("rate_max")
    for i, job in enumerate(saved, start=1):
        await update.message.reply_text(fmt.job_message(i, job, rate_min, rate_max))


@owner_only
async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _store(ctx).set("paused", True)
    schedule_digests(ctx.application)
    await update.message.reply_text("⏸ Invii automatici sospesi. Usa /riprendi per riattivarli.")


@owner_only
async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _store(ctx).set("paused", False)
    restart_cycle(ctx.application)
    await update.message.reply_text(
        f"▶️ Invii automatici riattivati, ogni {fmt.fmt_interval(interval_hours(_store(ctx)))}.\n"
        + schedule_text(ctx.application)
    )


@owner_only
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    if data == "search":
        await query.answer("Ricerca avviata")
        ctx.application.create_task(run_digest(ctx.application, query.message.chat_id, manual=True))
        return
    action, _, key = data.partition(":")
    status = {"s": "saved", "d": "dismissed"}.get(action)
    if not status or _store(ctx).set_status(key, status) is None:
        await query.answer("Offerta non trovata.")
        return
    if status == "saved":
        await query.answer("⭐ Salvata")
        await query.edit_message_reply_markup(
            InlineKeyboardMarkup([[InlineKeyboardButton("👎 Non mi interessa", callback_data=f"d:{key}")]])
        )
    else:
        await query.answer("Ok, terrò conto della tua scelta nelle prossime ricerche")
        await query.edit_message_text(f"<s>{query.message.text_html}</s>\n\n👎 <i>Scartata</i>")


# --- avvio ----------------------------------------------------------------

async def _post_init(app: Application) -> None:
    store: Store = app.bot_data["store"]
    if store.get("rate_min") is None:
        store.set("rate_min", DEFAULT_RATE_MIN)
        store.set("rate_max", DEFAULT_RATE_MAX)
    if store.get("interval_hours") is None:
        store.set("interval_hours", DEFAULT_INTERVAL_HOURS)
    next_run = schedule_digests(app)
    log.info(
        "Recap ogni %d ore, prossimo invio: %s",
        interval_hours(store), next_run.isoformat(timespec="minutes") if next_run else "in pausa",
    )
    await app.bot.set_my_commands([
        ("cerca", "Cerca subito nuove offerte"),
        ("frequenza", "Ogni quante ore inviare il recap"),
        ("rate", "Mostra o cambia il rate giornaliero"),
        ("cv", "Mostra il profilo estratto dal CV"),
        ("preferenze", "Aggiungi un'indicazione per la ricerca"),
        ("filtri", "Riepilogo dei criteri di ricerca"),
        ("salvati", "Offerte salvate"),
        ("pausa", "Sospendi gli invii automatici"),
        ("riprendi", "Riattiva gli invii automatici"),
        ("help", "Elenco comandi"),
    ])


def build_app(settings: Settings) -> Application:
    defaults = Defaults(
        parse_mode=ParseMode.HTML,
        tzinfo=settings.timezone,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    app = Application.builder().token(settings.telegram_token).defaults(defaults).post_init(_post_init).build()
    app.bot_data.update(
        settings=settings,
        store=Store(settings.db_path),
        claude=AsyncAnthropic(),
        search_lock=asyncio.Lock(),
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cerca", cmd_search))
    app.add_handler(CommandHandler(["frequenza", "orari"], cmd_frequency))
    app.add_handler(CommandHandler("rate", cmd_rate))
    app.add_handler(CommandHandler("cv", cmd_cv))
    app.add_handler(CommandHandler("preferenze", cmd_preferences))
    app.add_handler(CommandHandler("preferenze_reset", cmd_preferences_reset))
    app.add_handler(CommandHandler("filtri", cmd_filters))
    app.add_handler(CommandHandler("salvati", cmd_saved))
    app.add_handler(CommandHandler("pausa", cmd_pause))
    app.add_handler(CommandHandler("riprendi", cmd_resume))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(CallbackQueryHandler(on_button))
    return app
