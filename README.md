# Freelance Careers Advisor (fa-bot)

Bot Telegram personale che cerca in rete offerte di lavoro freelance compatibili con il tuo CV
e te le invia agli orari che scegli (di default una volta al giorno, alle 9:00).

## Come funziona

1. **CV → profilo**: invii il CV al bot (docx, pdf, txt). Claude ne estrae un profilo
   (ruoli, competenze, settori, keyword di ricerca). Puoi affinarlo con `/preferenze`.
2. **Fonti strutturate** (gratuite, link diretto e dati affidabili):

   | Fonte | Come | Chiave |
   |---|---|---|
   | iProgrammatori | feed RSS ufficiale + pagina annuncio (contratto, modalità, retribuzione) | no |
   | Remotive | API pubblica, offerte remote | no |
   | Arbeitnow | API pubblica, offerte remote in Europa | no |
   | Adzuna | API, Italia | sì, gratuita (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`) |
   | Jooble | API, Italia | sì, gratuita (`JOOBLE_API_KEY`) |

   Il bot tiene solo gli annunci recenti (ultime 3 settimane) che contengono i termini del tuo profilo
   (es. react, frontend, smart tv) e scarta quelli con contratto solo da dipendente.
   Claude valuta gli altri rispetto al CV.
3. **Ricerca web** (opzionale, `WEB_SEARCH_ENABLED`): Claude cerca anche su LinkedIn, Malt,
   pagine carriere di agenzie ecc., apre le pagine per verificarle e le marca ✅ verificate o
   ⚠️ non verificate (LinkedIn e Indeed non si lasciano aprire).
4. **Filtri**: il bot scarta le offerte non freelance, quelle solo in sede fuori Milano,
   quelle con rate chiaramente sotto il minimo e quelle con match < 55%.
5. **Ordinamento**: area (Milano → Italia → Europa), poi durata (brevi → lunghe → medie),
   poi tipo di azienda (agenzie di comunicazione → medie/grandi imprese → consulenza), poi match.
6. **Niente doppioni**: le offerte già inviate non vengono riproposte. Con 👎 scarti un'offerta
   (le ricerche successive evitano offerte simili), con ⭐ la salvi.

## Setup

1. Crea il bot con [@BotFather](https://t.me/BotFather) (`/newbot`) e copia il token.
2. Crea una chiave API su [console.anthropic.com](https://console.anthropic.com).
3. Configura l'ambiente:

   ```bash
   cp .env.example .env
   ```

   Compila `TELEGRAM_BOT_TOKEN` e `ANTHROPIC_API_KEY` in `.env`.
4. Avvia il bot:

   ```bash
   uv run python -m fa_bot
   ```

5. Su Telegram apri il bot, invia `/start` e poi il tuo CV come file.
   Il primo utente che fa `/start` diventa il proprietario: il bot non risponde a nessun altro.
   In alternativa imposta `TELEGRAM_OWNER_ID` in `.env`.

### Avvio automatico sul Mac

```bash
./scripts/install-launchd.sh
```

Il bot parte al login e si riavvia da solo se si chiude. I log sono in `logs/bot.log`.
Se il Mac è in stop all'orario previsto, l'invio viene recuperato entro un'ora dal risveglio,
altrimenti salta. Per disinstallare: `./scripts/uninstall-launchd.sh`.

## Comandi

| Comando | Cosa fa |
|---|---|
| `/start` | Configurazione iniziale |
| `/cerca` | Ricerca immediata |
| `/orari [9 14 18]` | Mostra o cambia gli orari di invio |
| `/rate [250 300]` | Mostra o cambia il rate giornaliero target |
| `/cv` | Mostra il profilo estratto. Invia un nuovo file per aggiornarlo |
| `/preferenze <testo>` | Aggiunge un'indicazione (es. "anche ruoli da Scrum Master") |
| `/preferenze_reset` | Cancella le indicazioni |
| `/filtri` | Riepilogo dei criteri |
| `/salvati` | Offerte salvate con ⭐ |
| `/pausa`, `/riprendi` | Sospende o riattiva gli invii automatici |

## Costi

Ogni ricerca usa la web search (max `WEB_SEARCH_MAX_USES` ricerche, 10 $ ogni 1000) e i token
dei risultati letti, che sono la voce principale. Il prompt caching è attivo: i passaggi interni
della ricerca rileggono il contesto dalla cache al 10% del prezzo.

Misurato con Opus 5.5: valutazione delle fonti strutturate ~0,15 $ e ricerca web ~0,32 $,
circa 0,50 $ a giro in tutto (~15 $/mese con un invio al giorno). Per spendere ancora meno:

- `WEB_SEARCH_ENABLED=false` (solo fonti strutturate, ~0,15 $ a giro, ~5 $/mese);
- `CLAUDE_MODEL=claude-sonnet-5-5` in `.env` (prezzo circa dimezzato);
- abbassa `WEB_SEARCH_MAX_USES` (es. 5).

Il consumo di ogni giro è nei log (`input`, `cache_write`, `cache_read`, `output`) e nella console Anthropic.

## Sviluppo

```bash
uv run pytest
```

Struttura: `src/fa_bot/` contiene `bot.py` (Telegram), `search.py` (ricerca e ordinamento),
`cv.py` (analisi del CV), `db.py` (SQLite in `data/`), `formatting.py` (messaggi) e `models.py`.
