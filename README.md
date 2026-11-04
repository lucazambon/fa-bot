# Freelance Careers Advisor (fa-bot)

A personal Telegram bot that searches the web for freelance job offers matching your CV
and sends you a recap at the interval you choose (by default every 3 days, to keep costs low).

The bot's interface and messages are in Italian.

## How it works

1. **CV → profile**: you send your CV to the bot (docx, pdf, txt). Claude extracts a profile from it
   (roles, skills, industries, search keywords). You can refine it with `/preferenze`.
2. **Structured sources** (free, with direct links and reliable data):

   | Source | How | Key |
   |---|---|---|
   | iProgrammatori | official RSS feed + job page (contract, work mode, pay) | no |
   | Remotive | public API, remote jobs | no |
   | Arbeitnow | public API, remote jobs in Europe | no |
   | Adzuna | API, Italy | yes, free (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`) |
   | Jooble | API, Italy | yes, free (`JOOBLE_API_KEY`) |

   The bot keeps only recent postings (last 3 weeks) that contain terms from your profile
   (e.g. react, frontend, smart tv) and drops those offering employee-only contracts.
   Claude assesses the rest against your CV.
3. **Web search** (optional, `WEB_SEARCH_ENABLED`): Claude also searches LinkedIn, Malt,
   agency career pages and so on, opens the pages to verify them and marks each offer as
   ✅ verified or ⚠️ unverified (LinkedIn and Indeed don't allow their pages to be opened).
4. **Filters**: the bot drops offers that aren't freelance, on-site-only offers outside Milan,
   offers clearly below the minimum rate, and offers with a match below 55%.
5. **Sorting**: by area (Milan → rest of Italy → Europe), then duration (short → long → medium),
   then company type (communication agencies → mid-size/large companies → consulting firms), then match.
6. **No duplicates**: offers already sent are never repeated. Tap 👎 to dismiss an offer
   (later searches avoid similar ones), or ⭐ to save it.
7. **Selection report**: the first message of each delivery, above the list of offers, summarizes the
   work done: sources read and how many postings each gave, how many were dropped and why, time and
   cost per phase, and the totals for time, tokens and cost (in $ and €).

## Setup

1. Create the bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and copy the token.
2. Create an API key at [console.anthropic.com](https://console.anthropic.com).
3. Set up the environment:

   ```bash
   cp .env.example .env
   ```

   Fill in `TELEGRAM_BOT_TOKEN` and `ANTHROPIC_API_KEY` in `.env`.
4. Start the bot:

   ```bash
   uv run python -m fa_bot
   ```

5. In Telegram, open the bot, send `/start` and then your CV as a file.
   The first user to send `/start` becomes the owner, and the bot ignores everyone else.
   Alternatively, set `TELEGRAM_OWNER_ID` in `.env`.

### Auto-start on macOS

```bash
./scripts/install-launchd.sh
```

The bot starts at login and restarts on its own if it stops. The application log is in
`logs/fa_bot.log`, and launchd's output goes to `logs/bot.log`.
The schedule is saved on disk, so restarting the bot does not reset it. If the Mac is asleep when a
recap is due, it is sent shortly after the Mac wakes up. To uninstall: `./scripts/uninstall-launchd.sh`.

## Commands

| Command | What it does |
|---|---|
| `/start` | Initial setup |
| `/cerca` | Search now |
| `/frequenza [36h \| 3d]` | Show or change how often the recap is sent, in hours or days (alias: `/orari`) |
| `/rate [250 300]` | Show or change the target daily rate |
| `/cv` | Show the extracted profile. Send a new file to update it |
| `/preferenze <text>` | Add a guideline (e.g. "anche ruoli da Scrum Master") |
| `/preferenze_reset` | Clear the guidelines |
| `/filtri` | Summary of the search criteria |
| `/salvati` | Offers saved with ⭐ |
| `/pausa`, `/riprendi` | Pause or resume automatic deliveries |

## Costs

Assessing postings from the structured sources costs only Claude tokens. The web search adds
search fees (up to `WEB_SEARCH_MAX_USES` searches, $10 per 1,000) and the tokens of the results
it reads, which make up most of its cost. Prompt caching is enabled: each internal step of the
search re-reads the earlier context from the cache at 10% of the price.

Measured with Opus 5.5 on one full run: $0.84 (about 0.77 €), of which about $0.16 to assess the
structured sources, $0.64 for the web search and $0.04 for the data extraction. That is about
$25/month with one run a day and about $8/month at the default of one run every 3 days; the web search varies a lot with how many pages it opens
(earlier runs cost $0.4–0.5). To spend less:

- `WEB_SEARCH_ENABLED=false` (structured sources only, ~$0.16 per run, ~$5/month);
- `CLAUDE_MODEL=claude-sonnet-5-5` in `.env` (roughly half the price);
- lower `WEB_SEARCH_MAX_USES` (e.g. 5).

You don't have to estimate: every delivery reports its own time, tokens and cost (see "Selection
report" above). Costs are computed from the list prices in `src/fa_bot/stats.py`, and euros use the
fixed exchange rate in `USD_EUR_RATE` (default 0.92), so treat them as estimates; the Anthropic
Console has the exact bill.

## Development

```bash
uv run pytest
```

To run a full search without Telegram and see what happens at each step
(nothing is marked as sent):

```bash
uv run python scripts/debug_search.py
```

Layout: `src/fa_bot/` contains `bot.py` (Telegram), `sources.py` (feeds and APIs),
`scoring.py` (Claude assessment of postings), `search.py` (web search, filters and sorting),
`stats.py` (time, token and cost accounting),
`cv.py` (CV analysis), `db.py` (SQLite in `data/`), `formatting.py` (messages) and `models.py`.
