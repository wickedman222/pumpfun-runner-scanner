# Pump.fun runner scanner

Posts **only high-conviction Pump.fun candidates** to Telegram.

Every new mint is scanned. **Spot** it on-curve in the $8k–$50k band. **Buy** only if that same mint is still near ATH, at least **+60% from our arm**, and at least **4 minutes** have passed. Graduation by itself is not a buy — that print is exit liquidity and is what slowly bled the paper book.

No real-SOL trading. After a call it opens a **paper** position (starts at 2 SOL) and manages scale-outs / dead exits in Telegram.

Repo: [github.com/wickedman222/pumpfun-runner-scanner](https://github.com/wickedman222/pumpfun-runner-scanner)

---

## What has to be true before a post

Every gate must pass. There is **no daily trade cap** — if several names hit the trigger, we take them (paper still max 5 open at once).

1. **Spot** — every launch. Arm only on-curve, **$8k–$50k**, near ATH. First look already graduated is skipped.
2. **Wait** — at least 4 minutes after the arm so a 12-second wick is not a buy.
3. **Buy** — still within 25% of ATH, **+60% from our arm**, MC still under ~$120k. Structure only at buy.
4. **Or wallet follow** — harvest holders from recent coins that actually ran (ATH ≥ $200k, still near highs), **plus early curve buyers from on-chain txs** (the wallets that already sold and vanished from the holder list). If **two** of those wallets are already in a young book, that is the buy. Graduation fill is still not a buy.

Never:

- Mayhem or cashback painted books
- One-way BOOST tape (ATH glued to spot from $200k up, empty room, not live) — fake chart, never harvest wallets from it
- Copy-ticker farms (same ticker flooding — farm mechanic, not the letters)
- First look already above ~$200k (just-graduated runners are allowed; $1M chases are not)
- Serial-rug / bundled structure

Empty on-site chat is normal in 2026. We do not ban or buy because of how the name is spelled.

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
Calls: live crowd, a headline already in the window, or first-mover tape.
The ticker spelling is not a signal.
```

Every 6 hours it also posts an **all-time top 15** of calls ranked by ATH multiple (the x from the price when we posted), plus the paper book.

## Paper book

Starts at **2 SOL** (not real). A call buys **7.5% of equity** (0.15 SOL at the start, floored at 0.10, capped at 0.20). Above $80k MC the size is 70% (still take the graduate). Hard skip only above ~$200k. Max **5** open. Fills assume 1% fee, 8% entry slip, 5% exit slip.

| Level | Action |
|---|---|
| −45% from entry | Flatten — dead (before first take-profit) |
| Live path, stream dies, still &lt; 1.2x | Flatten |
| **2x** | Sell 25% |
| **4x** | Sell 25% (50% moonbag left) |
| **10x** | Clip part of the moonbag |
| After 4x, −65% off post-entry ATH | Trail the rest |

No 2-hour “must 1.6x” flatten — that killed grind-then-run names. Dead books still hit the −45% stop.

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

- Graduation ≠ runner. The bot is trying to catch first-mover expansion with clean structure, not every coin that fills the curve.
- Most tokens that pass still die. The edge is selection quality, not a high win rate.
- Empty days are normal and preferred over spam.
