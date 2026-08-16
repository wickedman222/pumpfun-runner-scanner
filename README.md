# Pump.fun runner scanner

Posts **only high-conviction Pump.fun candidates** to Telegram.

It does **not** snipe the board. It starts from exogenous attention (news / reddit), then takes the first clean mint that maps to that story. Most days it posts nothing. That is the design.

No auto-trading.

Repo: [github.com/wickedman222/pumpfun-runner-scanner](https://github.com/wickedman222/pumpfun-runner-scanner)

---

## What has to be true before a post

Every gate must pass:

1. **Attention** — a real headline exists *outside crypto* (not a “new memecoin launched” article)
2. **First-mover** — this is the first mint in our window that maps to that story
3. **Structure** — dev is not a serial launcher, holders are not bundled/top-heavy, rugcheck is clean
4. **Expansion** — after a wait, MC / replies still hold or grow (not an instant dump)

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
Attention-first. I only post if every gate passes.
```

If that message does not arrive: token is wrong, bot is not in the channel, or chat id is wrong.

### Optional variables

| Variable | Default | Meaning |
|---|---|---|
| `MAX_SIGNALS_PER_DAY` | `6` | Hard cap so the channel stays quiet |
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
