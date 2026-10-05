"""Live smoke test against the real Polymarket APIs (network required).

Catches upstream changes the offline tests can't: renamed fields, a new
required parameter, an endpoint moving. Run by the scheduled CI job, or
locally: python tests/smoke_live.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import polymarket as pm  # noqa: E402


def check(cond, msg):
    if not cond:
        sys.exit(f"FAIL: {msg}")
    print(f"ok: {msg}")


events = pm.top_events(5)
check(events and events[0]["slug"] and events[0]["markets"], "top_events returns events with markets")

# Pick a liquid, open, two-outcome market to exercise the CLOB endpoints.
market = next((m for e in events for m in e["markets"]
               if not m["closed"] and len(m["outcomes"]) == 2 and m["outcomes"][0]["token_id"]
               and m["outcomes"][0]["price"] is not None), None)
check(market is not None, "found an open two-outcome market with token ids and prices")
check(0 <= market["outcomes"][0]["price"] <= 1, "Gamma price is a probability")
check(pm.market(market["slug"])["condition_id"] == market["condition_id"], "market() by slug round-trips")

token = market["outcomes"][0]["token_id"]
mid = pm.midpoint(token)
check(mid is not None and 0 <= mid <= 1, "CLOB midpoint is a probability")
book = pm.book(token)
check(isinstance(book["bids"], list) and isinstance(book["asks"], list), "CLOB book has bid/ask lists")
for interval in ("1d", "1w", "1m"):  # 1w / 1m enforce a minimum fidelity
    check(isinstance(pm.history(token, interval), list), f"prices-history {interval} accepted")
check(isinstance(pm.trades(market["condition_id"], 3), list), "Data API trades returns a list")
check(isinstance(pm.search("election", 3), list), "public-search returns a list")
print("live smoke passed")
