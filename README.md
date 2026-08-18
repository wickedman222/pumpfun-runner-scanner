# Pump.fun runner scanner

Posts **only high-conviction Pump.fun candidates** to Telegram.

It does **not** snipe the board. It starts from exogenous attention (news / reddit), then takes the first clean mint that maps to that story. Most days it posts nothing. That is the design.

No real-SOL trading. After a call it opens a **paper** position (starts at 2 SOL) and manages scale-outs / dead exits in Telegram.

Repo: [github.com/wickedman222/pumpfun-runner-scanner](https://github.com/wickedman222/pumpfun-runner-scanner)

---

## What has to be true before a post

Every gate must pass. A call comes from one of two **real** paths:

1. **Tight news / wiki / reddit map** — the token *is* the story (PNUT / Moo Deng / Jimothy). A website plus a paragraph does not count.
2. **Live crowd** — first mint, still live, real people in the room (SHOBON).

BOOST is allowed only when that identity is real. Website-only “character” names are not calls.

Never:

- Fake US fund/reserve names (USWS, EYE, UOTF, WWR, Z500…)
- BOOST with **no** character, site, or live crowd (painted one-way tape)
- Mayhem
- Copy-ticker farms
- First look already above ~$80k
- Serial-rug / bundled structure

Empty on-site chat is normal in 2026. The scam tell is the USWS chart (ATH glued to spot, acronym, no identity), not BOOST by itself.

Generic tickers (`PEPE`, `CAT`, `MOON`, …) are ignored on purpose.

---

## Railway — what you fill in

Railway → your service → **Variables**:

| Variable | Required | What to put |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **yes** | Full token from [@BotFather](https://t.me/BotFather) (`123456:AA...`) |
| `TELEGRAM_CHAT_ID` | **yes** | Channel / group / user id. Channels look like `-100xxxxxxxxxx` |

Aliases also work: `TG_TOKEN` / `TELEGRAM_TOKEN` and `CHAT_ID` / `SIGNAL_CHAT_ID`.

### Telegram checklist

1. Create a bot with BotFather → copy the **full** token.
2. Add the bot to your channel as **admin** (it must be able to post).
3. Get the chat id:
   - forward a channel post to [@userinfobot](https://t.me/userinfobot), or
   - open `https://api.telegram.org/bot<TOKEN>/getUpdates` after posting something the bot can see.
4. Paste both values into Railway Variables and **redeploy**.

On a healthy boot the channel gets:

```
Pump.fun runner scanner online
Attention-first. I only post if it looks like a real runner.
No daily cap — gates decide.
```

Every 6 hours it also posts an **all-time top 15** of calls ranked by ATH multiple (the x from the price when we posted), plus the paper book.

## Paper book

Starts at **2 SOL** (not real). A call buys **7.5% of equity** (0.15 SOL at the start, floored at 0.10, capped at 0.20). Max **4** open names. Fills assume 1% fee, 8% entry slip, 5% exit slip.

| Level | Action |
|---|---|
| −45% from entry | Flatten — dead |
| 2 hours and never 1.6x | Flatten — no go |
| Live path, stream dies, still &lt; 1.2x | Flatten |
| **2x** | Sell 40% |
| **4x** | Sell 30% (30% moonbag left) |
| **10x** | Sell half the moonbag |
| After 2x, −50% off post-entry ATH | Trail the rest |

Size grows if the book grows, shrinks if it draws down. Telegram gets a **paper balance report every 2 hours**. Set `PAPER_ENABLED=0` to turn it off. `PAPER_START_SOL` only applies on first boot of an empty wallet. `PAPER_REPORT_SEC` defaults to `7200`.

If that message does not arrive: token is wrong, bot is not in the channel, or chat id is wrong.

### Optional variables

| Variable | Default | Meaning |
|---|---|---|
| `LEADERBOARD_SEC` | `21600` | Hours between all-time top-15 boards (default 6h) |
| `LEADERBOARD_SIZE` | `15` | How many names on the board |
| `EXPANSION_WAIT_SEC` | `180` | Wait after first match before posting |
| `PUMP_POLL_SEC` | `12` | How often new Pump.fun mints are pulled |
| `ATTENTION_POLL_SEC` | `120` | How often news/reddit is refreshed |
| `MAX_DEV_PRIOR_TOKENS` | `2` | Fail if the creator already launched more |
| `MAX_TOP_HOLDER_PCT` | `10` | Fail if one wallet (ex LP) holds more |
| `MAX_TOP10_PCT` | `40` | Fail if top 10 (ex LP) hold more |
| `MIN_MATCH_SCORE` | `70` | How tightly the mint must map to the headline |
| `HELIUS_API_KEY` | empty | Optional better Solana RPC |

---

## Deploy (GitHub → Railway)

1. This repo is already on GitHub.
2. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub** → `pumpfun-runner-scanner`
3. Builder: **Dockerfile** (`railway.toml` already sets this)
4. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
5. Deploy. Logs should show `Scanner loop started` and Telegram should get the boot message.

This is a long-running worker with a tiny `/` health endpoint.

---

## Local run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# edit .env
python main.py
```

On Windows, sqlite state is stored in `./data`. On Railway it is `/app/data` (ephemeral — that is fine; the scanner is stateless across redeploys except for de-dupe).

---

## Signal format

```
PUMP.FUN RUNNER CANDIDATE
$TICKER  Name
why this exists / why this token / structure / expansion
invalidate if …
Not a trade call.
```

---

## Honest expectations

- Graduation ≠ runner. This bot is trying to catch the rare *story-first* names, not every coin that fills the curve.
- Most tokens that pass still die. The edge is selection quality, not a high win rate.
- Empty days are normal and preferred over spam.
