#!/usr/bin/env python3
"""Read-only Polymarket client: importable module + CLI. Stdlib only, no keys.

APIs (all public, no auth):
  Gamma  https://gamma-api.polymarket.com  - events, markets, search
  CLOB   https://clob.polymarket.com       - midpoint, order book, price history
  Data   https://data-api.polymarket.com   - recent trades

Prices are probabilities in [0, 1]. A market's outcomes each have a CLOB token id;
book/history/midpoint take a token id, trades take the market's conditionId.

Import:  from polymarket import search, market, book, history, trades
CLI:     python polymarket.py --help
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
TIMEOUT = 20


class PolymarketError(RuntimeError):
    pass


def _get(base, path, **params):
    params = {k: v for k, v in params.items() if v is not None}
    url = f"{base}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-skill/1.0", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise PolymarketError(f"{e.code} from {url}: {e.read()[:200].decode('utf-8', 'replace')}") from e
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise PolymarketError(f"cannot reach {url}: {e.reason}") from e


def _json_list(value):
    # Gamma returns outcomes / outcomePrices / clobTokenIds as JSON-encoded strings.
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return []
    return value or []


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_market(m):
    """Flatten a Gamma market into the fields callers actually use."""
    names = _json_list(m.get("outcomes"))
    prices = _json_list(m.get("outcomePrices"))
    tokens = _json_list(m.get("clobTokenIds"))
    outcomes = [
        {"name": n, "price": _num(prices[i]) if i < len(prices) else None,
         "token_id": tokens[i] if i < len(tokens) else None}
        for i, n in enumerate(names)
    ]
    return {
        "id": m.get("id"),
        "slug": m.get("slug"),
        "question": m.get("question"),
        "label": m.get("groupItemTitle") or None,  # short option name inside a multi-market event
        "condition_id": m.get("conditionId"),
        "outcomes": outcomes,
        "volume": _num(m.get("volumeNum", m.get("volume"))),
        "volume_24h": _num(m.get("volume24hr")),
        "liquidity": _num(m.get("liquidityNum", m.get("liquidity"))),
        "end_date": m.get("endDate"),
        "active": m.get("active"),
        "closed": m.get("closed"),
        "url": f"https://polymarket.com/market/{m.get('slug')}" if m.get("slug") else None,
    }


def normalize_event(e):
    return {
        "id": e.get("id"),
        "slug": e.get("slug"),
        "title": e.get("title"),
        "volume": _num(e.get("volume")),
        "volume_24h": _num(e.get("volume24hr")),
        "end_date": e.get("endDate"),
        "closed": e.get("closed"),
        "markets": [normalize_market(m) for m in e.get("markets") or []],
        "url": f"https://polymarket.com/event/{e.get('slug')}" if e.get("slug") else None,
    }


# ---- Gamma: discovery ------------------------------------------------------

def search(query, limit=5, include_closed=False):
    """Events matching free text, with their markets."""
    data = _get(GAMMA, "/public-search", q=query, limit_per_type=limit,
                events_status=None if include_closed else "active")
    events = [normalize_event(e) for e in data.get("events") or []]
    if not include_closed:
        events = [e for e in events if not e["closed"]]
    return events


def top_events(limit=10, tag=None):
    """Active events ranked by 24h volume, optionally filtered by tag slug (e.g. politics, crypto)."""
    data = _get(GAMMA, "/events", limit=limit, active="true", closed="false",
                order="volume24hr", ascending="false", tag_slug=tag)
    return [normalize_event(e) for e in data]


def event(slug_or_id):
    """One event (with all its markets) by slug or numeric id."""
    if str(slug_or_id).isdigit():
        return normalize_event(_get(GAMMA, f"/events/{slug_or_id}"))
    data = _get(GAMMA, "/events", slug=slug_or_id)
    if not data:
        raise PolymarketError(f"no event with slug {slug_or_id!r}")
    return normalize_event(data[0])


def market(slug_or_id):
    """One market by slug or numeric id."""
    if str(slug_or_id).isdigit():
        return normalize_market(_get(GAMMA, f"/markets/{slug_or_id}"))
    data = _get(GAMMA, "/markets", slug=slug_or_id)
    if not data:
        raise PolymarketError(f"no market with slug {slug_or_id!r}")
    return normalize_market(data[0])


# ---- CLOB: live prices -----------------------------------------------------

def midpoint(token_id):
    return _num(_get(CLOB, "/midpoint", token_id=token_id).get("mid"))


def book(token_id, depth=5):
    """Best bids/asks for one outcome token, best price first."""
    data = _get(CLOB, "/book", token_id=token_id)
    bids = sorted(((float(o["price"]), float(o["size"])) for o in data.get("bids", [])), reverse=True)
    asks = sorted((float(o["price"]), float(o["size"])) for o in data.get("asks", []))
    return {
        "token_id": token_id,
        "best_bid": bids[0][0] if bids else None,
        "best_ask": asks[0][0] if asks else None,
        "spread": round(asks[0][0] - bids[0][0], 4) if bids and asks else None,
        "bids": [{"price": p, "size": s} for p, s in bids[:depth]],
        "asks": [{"price": p, "size": s} for p, s in asks[:depth]],
    }


# Default minutes-per-point per interval (~100-300 points), and the CLOB's enforced minimums.
_FIDELITY_DEFAULT = {"1h": 1, "6h": 5, "1d": 15, "1w": 60, "1m": 180, "max": 1440}
_FIDELITY_MIN = {"1w": 5, "1m": 10}


def history(token_id, interval="1w", fidelity=None):
    """Price series for one outcome token. interval: 1h 6h 1d 1w 1m max; fidelity: minutes per point."""
    fidelity = max(fidelity or _FIDELITY_DEFAULT.get(interval, 60), _FIDELITY_MIN.get(interval, 1))
    data = _get(CLOB, "/prices-history", market=token_id, interval=interval, fidelity=fidelity)
    return [{"t": p["t"], "p": p["p"]} for p in data.get("history", [])]


# ---- Data API: trades ------------------------------------------------------

def trades(condition_id, limit=20):
    """Most recent trades on a market (by conditionId)."""
    data = _get(DATA, "/trades", market=condition_id, limit=limit)
    return [{"time": t["timestamp"], "side": t["side"], "outcome": t.get("outcome"),
             "price": t["price"], "size": t["size"]} for t in data]


# ---- CLI -------------------------------------------------------------------

def _pct(p):
    return "  n/a" if p is None else f"{p * 100:5.1f}%"


def _fmt_market(m, indent=""):
    lines = [f"{indent}{m['question']}  [{m['slug']}]"]
    odds = "  ".join(f"{o['name']} {_pct(o['price']).strip()}" for o in m["outcomes"])
    vol = f"${m['volume']:,.0f}" if m["volume"] is not None else "n/a"
    lines.append(f"{indent}  {odds}   vol {vol}   ends {(m['end_date'] or '')[:10]}")
    return "\n".join(lines)


def _fmt_event(e, max_markets=5):
    vol = f"${e['volume']:,.0f}" if e["volume"] is not None else "n/a"
    out = [f"{e['title']}  [{e['slug']}]  vol {vol}"]
    live = [m for m in e["markets"] if not m["closed"]] or e["markets"]
    live.sort(key=lambda m: -(m["outcomes"][0]["price"] or 0) if m["outcomes"] else 0)
    out += [_fmt_market(m, "  ") for m in live[:max_markets]]
    if len(live) > max_markets:
        out.append(f"  ... {len(live) - max_markets} more markets (use `event {e['slug']}`)")
    return "\n".join(out)


def _resolve_token(market_ref, outcome):
    """Accept a raw token id, or a market slug/id plus outcome name/index."""
    if market_ref.isdigit() and len(market_ref) > 20:
        return market_ref, market_ref
    m = market(market_ref)
    for i, o in enumerate(m["outcomes"]):
        if outcome is None and i == 0 or outcome is not None and (str(i) == outcome or o["name"].lower() == outcome.lower()):
            return o["token_id"], f"{m['question']} - {o['name']}"
    raise PolymarketError(f"outcome {outcome!r} not in {[o['name'] for o in m['outcomes']]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only Polymarket CLI (no keys).")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="free-text search for events")
    s.add_argument("query", nargs="+")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--closed", action="store_true", help="include resolved events")

    t = sub.add_parser("top", help="active events by 24h volume")
    t.add_argument("--limit", type=int, default=10)
    t.add_argument("--tag", help="tag slug, e.g. politics, crypto, sports")

    e = sub.add_parser("event", help="event + all its markets, by slug or id")
    e.add_argument("ref")

    m = sub.add_parser("market", help="one market by slug or id")
    m.add_argument("ref")

    for name, hlp in (("book", "order book"), ("history", "price history"), ("price", "live midpoint")):
        p = sub.add_parser(name, help=f"{hlp} for an outcome (market slug/id + --outcome, or raw token id)")
        p.add_argument("ref")
        p.add_argument("--outcome", help="outcome name or index (default: first, usually Yes)")
        if name == "book":
            p.add_argument("--depth", type=int, default=5)
        if name == "history":
            p.add_argument("--interval", default="1w", choices=["1h", "6h", "1d", "1w", "1m", "max"])
            p.add_argument("--fidelity", type=int, help="minutes per point")

    r = sub.add_parser("trades", help="recent trades on a market (slug/id or conditionId)")
    r.add_argument("ref")
    r.add_argument("--limit", type=int, default=20)

    a = ap.parse_args(argv)
    try:
        if a.cmd == "search":
            res = search(" ".join(a.query), a.limit, a.closed)
            text = "\n\n".join(_fmt_event(x) for x in res) or "no matching events"
        elif a.cmd == "top":
            res = top_events(a.limit, a.tag)
            text = "\n\n".join(_fmt_event(x, 3) for x in res)
        elif a.cmd == "event":
            res = event(a.ref)
            text = _fmt_event(res, max_markets=50)
        elif a.cmd == "market":
            res = market(a.ref)
            text = _fmt_market(res) + f"\n  {res['url']}"
        elif a.cmd == "price":
            token, label = _resolve_token(a.ref, a.outcome)
            res = {"token_id": token, "label": label, "midpoint": midpoint(token)}
            text = f"{label}: {_pct(res['midpoint']).strip()}"
        elif a.cmd == "book":
            token, label = _resolve_token(a.ref, a.outcome)
            res = book(token, a.depth)
            rows = [f"{label}  bid {res['best_bid']}  ask {res['best_ask']}  spread {res['spread']}"]
            rows += [f"  bid {b['price']:<6} x {b['size']:>12,.0f}" for b in res["bids"]]
            rows += [f"  ask {x['price']:<6} x {x['size']:>12,.0f}" for x in res["asks"]]
            text = "\n".join(rows)
        elif a.cmd == "history":
            token, label = _resolve_token(a.ref, a.outcome)
            res = history(token, a.interval, a.fidelity)
            if res:
                first, last = res[0]["p"], res[-1]["p"]
                lo, hi = min(p["p"] for p in res), max(p["p"] for p in res)
                text = (f"{label} over {a.interval}: {_pct(first).strip()} -> {_pct(last).strip()} "
                        f"({(last - first) * 100:+.1f} pts), range {_pct(lo).strip()}-{_pct(hi).strip()}, {len(res)} points")
            else:
                text = f"{label}: no price history for {a.interval}"
        elif a.cmd == "trades":
            cid = a.ref if a.ref.startswith("0x") else market(a.ref)["condition_id"]
            res = trades(cid, a.limit)
            text = "\n".join(
                f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(x['time']))}Z  {x['side']:<4} {x['outcome'] or '':<10} "
                f"{x['price']:<6.3f} x {x['size']:,.2f}" for x in res) or "no trades"
    except PolymarketError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
