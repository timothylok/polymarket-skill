---
name: polymarket
description: Query live Polymarket prediction-market odds — search events, top markets by volume, a market's outcome probabilities, order book, price history, and recent trades. Read-only public APIs, no key, wallet, or login. Use when the task involves Polymarket, prediction-market odds, "what are the odds of X", or crowd-implied probabilities.
---

# Polymarket (read-only)

Stdlib-only Python 3.8+ client for Polymarket's public APIs. One file, `scripts/polymarket.py`, is both a CLI and an importable module. No dependencies, no credentials.

## Use this when

- Someone asks the market-implied probability of an event (elections, Fed decisions, sports, crypto prices)
- You need to find a market's slug, outcomes, or token ids
- You need odds movement over time, order-book depth/spread, or recent trades

## Do not use this for

- Placing or cancelling orders, wallets, positions, or anything that moves money (out of scope by design)
- Treating odds as fact — they are crowd prices, and thin markets (low volume / wide spread) are noisy

## Concepts

- **Event** groups one or more **markets** (e.g. "How many Fed cuts in 2026?" → one market per count).
- Each market has **outcomes** (usually Yes/No); each outcome has a CLOB **token id**. Price = probability in [0, 1].
- `book` / `history` / `price` take an outcome token; `trades` takes the market's `conditionId`. The CLI resolves both from a market slug for you.

## Preferred workflow

1. `search <text>` to find the event and market slugs (or `top --tag politics` to browse)
2. `market <slug>` for current odds, or `event <slug>` for every market in a multi-outcome event
3. `history <slug> --interval 1w` for "has it moved?" claims; `book <slug>` to check liquidity before trusting a price
4. Add `--json` (before the subcommand) for structured output to chain into other tools

## CLI

```bash
python scripts/polymarket.py [--json] <command> [flags]
```

- `search <query> [--limit N] [--closed]` — events matching free text, with their markets
- `top [--limit N] [--tag SLUG]` — active events by 24h volume (tags: politics, crypto, sports, …)
- `event <slug|id>` — one event with all markets
- `market <slug|id>` — one market: outcomes, prices, volume, end date, URL
- `price <slug|token_id> [--outcome NAME|INDEX]` — live CLOB midpoint (default outcome: first, usually Yes)
- `book <slug|token_id> [--outcome …] [--depth N]` — best bid/ask, spread, top levels
- `history <slug|token_id> [--outcome …] [--interval 1h|6h|1d|1w|1m|max] [--fidelity MIN]` — change, range, point count
- `trades <slug|id|conditionId> [--limit N]` — most recent fills

Exit code 1 with `error: …` on stderr for unknown slugs or API failures (429/5xx are retried twice).

## Python

```python
import sys; sys.path.insert(0, "<path-to>/polymarket-skill/scripts")
import polymarket as pm

m = pm.market("another-fed-rate-hike-in-2026")
yes = m["outcomes"][0]                     # {"name", "price", "token_id"}
pm.midpoint(yes["token_id"])               # 0.78
pm.history(yes["token_id"], "1w")          # [{"t": unix, "p": 0.77}, ...]
pm.book(yes["token_id"])                   # best_bid / best_ask / spread / levels
pm.trades(m["condition_id"], limit=10)
pm.search("bitcoin 150k"); pm.top_events(10, tag="crypto"); pm.event("slug")
```

Functions raise `pm.PolymarketError` on failure.

## API notes (verified 2026-10-05)

- Gamma `https://gamma-api.polymarket.com` — `/public-search`, `/events`, `/markets` (by `slug=` or `/markets/{id}`). `outcomes`, `outcomePrices`, `clobTokenIds` arrive as JSON-encoded strings; the client decodes them.
- CLOB `https://clob.polymarket.com` — `/midpoint`, `/book` (levels arrive unsorted for best price; the client sorts), `/prices-history`. History enforces a minimum `fidelity` (minutes/point): 5 for `1w`, 10 for `1m`; the client applies defaults and clamps.
- Data `https://data-api.polymarket.com` — `/trades?market=<conditionId>`.
- Gamma prices (`outcomePrices`) can lag the CLOB midpoint by a tick; use `price` when it matters.

## Reuse in another repo

Junction or symlink this folder into the repo's `.claude/skills/polymarket` (Windows: `cmd /c mklink /J <repo>\.claude\skills\polymarket D:\ai\polymarket-skill`), or vendor `scripts/polymarket.py` as a single file.
