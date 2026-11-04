"""Bot Telegram: comandi, caricamento CV, pulsanti sulle offerte e invii programmati."""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import time

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
from .config import DEFAULT_RATE_MAX, DEFAULT_RATE_MIN, DEFAULT_TIMES, Settings
from .cv import SUPPORTED, build_profile, cv_to_content
from .db import Store
from .search import find_jobs

log = logging.getLogger(__name__)
JOB_PREFIX = "digest-"


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


def schedule_digests(app: Application) -> list[str]:
    store: Store = app.bot_data["store"]
    tz = app.bot_data["settings"].timezone
    for job in app.job_queue.jobs():
        if job.name and job.name.startswith(JOB_PREFIX):
            job.schedule_removal()
    times = store.get("times", DEFAULT_TIMES)
    if store.get("paused", False):
        return times
    for t in times:
        h, m = map(int, t.split(":"))
        app.job_queue.run_daily(
            scheduled_digest,
            time=time(h, m, tzinfo=tz),
            name=JOB_PREFIX + t,
            # Se il Mac era in stop all'orario previsto, recupera l'invio entro un'ora.
            job_kwargs={"misfire_grace_time": 3600, "coalesce": True},
        )
    return times


async def run_digest(app: Application, chat_id: int, manual: bool) -> None:
    store: Store = app.bot_data["store"]
    settings: Settings = app.bot_data["settings"]
    lock: asyncio.Lock = app.bot_data["search_lock"]

    if lock.locked():
        if manual:
            await app.bot.send_message(chat_id, "⏳ C'è già una ricerca in corso, attendi qualche minuto.")
        return

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
            return

        jobs = result.jobs
        summary = fmt.run_summary(result.stats)
        if not jobs:
            await status.edit_text("😶 Nessuna nuova offerta compatibile in questo giro.\n\n" + summary)
            return

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


async def scheduled_digest(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = _store(ctx).get("chat_id")
    if chat_id and _store(ctx).get_profile():
        await run_digest(ctx.application, chat_id, manual=False)


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
    times = schedule_digests(ctx.application)
    text = (
        f"Ciao {user.first_name}! 👋\n"
        "Sono il tuo <b>Freelance Careers Advisor</b>: cerco in rete offerte freelance compatibili "
        "con il tuo CV e te le mando ogni giorno.\n\n"
        f"🕘 Invii programmati: {', '.join(times)}\n"
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
async def cmd_times(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(ctx)
    if ctx.args:
        try:
            times = fmt.parse_times(ctx.args)
        except ValueError as bad:
            await update.message.reply_text(f"Orario non valido: <code>{bad}</code>. Esempio: <code>/orari 9 14:30 18</code>")
            return
        if not times or len(times) > 8:
            await update.message.reply_text("Indica da 1 a 8 orari.")
            return
        store.set("times", times)
        schedule_digests(ctx.application)
        await update.message.reply_text(f"✅ Nuovi orari di invio: {', '.join(times)}")
    else:
        paused = " (in pausa)" if store.get("paused") else ""
        await update.message.reply_text(
            f"🕘 Orari di invio{paused}: {', '.join(store.get('times', DEFAULT_TIMES))}\n"
            "Per cambiarli: <code>/orari 9 14 18</code>"
        )


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
        f"• Orari: {', '.join(store.get('times', DEFAULT_TIMES))}"
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
    times = schedule_digests(ctx.application)
    await update.message.reply_text(f"▶️ Invii automatici riattivati: {', '.join(times)}")


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
    if store.get("times") is None:
        store.set("times", DEFAULT_TIMES)
    times = schedule_digests(app)
    log.info("Invii programmati: %s", times)
    await app.bot.set_my_commands([
        ("cerca", "Cerca subito nuove offerte"),
        ("orari", "Mostra o cambia gli orari di invio"),
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
    app.add_handler(CommandHandler("orari", cmd_times))
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
