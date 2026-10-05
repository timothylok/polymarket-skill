"""Offline tests: polymarket._get is replaced by a fake serving fixtures shaped
like the real API responses (captured 2026-10-05), so no network is used.
The live API is exercised separately by tests/smoke_live.py."""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import polymarket as pm  # noqa: E402

YES, NO = "111", "222"
MARKET = {
    "id": "1034", "slug": "fed-hike", "question": "Another Fed rate hike in 2026?",
    "conditionId": "0xabc", "groupItemTitle": "",
    # Gamma sends these three as JSON-encoded strings, not arrays.
    "outcomes": '["Yes", "No"]', "outcomePrices": '["0.78", "0.22"]',
    "clobTokenIds": f'["{YES}", "{NO}"]',
    "volumeNum": 541010.5, "volume24hr": 1200, "liquidityNum": 9000,
    "bestBid": 0.77, "bestAsk": 0.79,
    "endDate": "2026-12-10T00:00:00Z", "active": True, "closed": False,
}
LADDER = [
    dict(MARKET, slug="cut-0", question="Will no cuts happen?", groupItemTitle="0 (0 bps)",
         outcomePrices='["0.96", "0.04"]'),
    dict(MARKET, slug="cut-1", question="Will 1 cut happen?", groupItemTitle="1 (25 bps)",
         outcomePrices='["0.02", "0.98"]', closed=True),
]
EVENT = {"id": "9", "slug": "fed-cuts", "title": "How many Fed cuts?", "volume": "53870538",
         "volume24hr": 10, "endDate": "2027-01-01", "closed": False, "markets": LADDER}


class FakeAPI:
    def __init__(self):
        self.calls = []

    def __call__(self, base, path, **params):
        params = {k: v for k, v in params.items() if v is not None}
        self.calls.append((base, path, params))
        if path == "/markets":
            return [MARKET] if params.get("slug") == "fed-hike" else []
        if path == "/markets/1034":
            return MARKET
        if path == "/events":
            if "slug" in params:
                return [EVENT] if params["slug"] == "fed-cuts" else []
            return [EVENT]
        if path == "/public-search":
            return {"events": [EVENT, dict(EVENT, slug="old", closed=True)]}
        if path == "/midpoint":
            return {"mid": "0.78"}
        if path == "/book":
            # Real books list levels worst-first; the client must sort.
            return {"bids": [{"price": "0.75", "size": "10"}, {"price": "0.77", "size": "1209"}],
                    "asks": [{"price": "0.999", "size": "5"}, {"price": "0.79", "size": "130"}]}
        if path == "/prices-history":
            return {"history": [{"t": 1, "p": 0.70}, {"t": 2, "p": 0.78}]}
        if path == "/trades":
            return [{"timestamp": 1791171683, "side": "BUY", "outcome": "Yes", "price": 0.76, "size": 2.63}]
        raise AssertionError(f"unexpected call {base}{path} {params}")


class FakeAPITestCase(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self._orig = pm._get
        pm._get = self.api

    def tearDown(self):
        pm._get = self._orig


class ClientTests(FakeAPITestCase):
    def test_normalize_decodes_json_string_fields(self):
        m = pm.normalize_market(MARKET)
        self.assertEqual([o["name"] for o in m["outcomes"]], ["Yes", "No"])
        self.assertEqual(m["outcomes"][0], {"name": "Yes", "price": 0.78, "token_id": YES})
        self.assertIsNone(m["label"])  # empty groupItemTitle -> None
        self.assertEqual(m["url"], "https://polymarket.com/market/fed-hike")
        self.assertEqual((m["best_bid"], m["best_ask"]), (0.77, 0.79))

    def test_normalize_tolerates_missing_and_bad_fields(self):
        m = pm.normalize_market({"outcomes": "not json", "slug": None})
        self.assertEqual(m["outcomes"], [])
        self.assertIsNone(m["url"])
        self.assertIsNone(m["best_bid"])  # empty side of the book

    def test_market_by_slug_and_id(self):
        self.assertEqual(pm.market("fed-hike")["condition_id"], "0xabc")
        self.assertEqual(pm.market("1034")["slug"], "fed-hike")
        with self.assertRaises(pm.PolymarketError):
            pm.market("nope")

    def test_event_keeps_labels(self):
        e = pm.event("fed-cuts")
        self.assertEqual([m["label"] for m in e["markets"]], ["0 (0 bps)", "1 (25 bps)"])
        self.assertEqual(e["volume"], 53870538.0)

    def test_search_drops_closed_unless_asked(self):
        self.assertEqual([e["slug"] for e in pm.search("fed")], ["fed-cuts"])
        self.assertEqual(len(pm.search("fed", include_closed=True)), 2)

    def test_book_sorted_best_first(self):
        b = pm.book(YES, depth=1)
        self.assertEqual((b["best_bid"], b["best_ask"], b["spread"]), (0.77, 0.79, 0.02))
        self.assertEqual(len(b["bids"]), 1)

    def test_history_applies_fidelity_minimums(self):
        def fidelity(interval, requested=None):
            pm.history(YES, interval, requested)
            return self.api.calls[-1][2]["fidelity"]
        self.assertEqual(fidelity("1m", 1), 10)   # CLOB rejects < 10 for 1m
        self.assertEqual(fidelity("1w", 1), 5)    # and < 5 for 1w
        self.assertEqual(fidelity("1h"), 1)       # default
        self.assertEqual(fidelity("1d", 30), 30)  # caller's value kept when valid

    def test_trades_shape(self):
        t = pm.trades("0xabc", 1)[0]
        self.assertEqual((t["side"], t["outcome"], t["price"]), ("BUY", "Yes", 0.76))

    def test_resolve_token(self):
        self.assertEqual(pm._resolve_token("fed-hike", None)[0], YES)
        self.assertEqual(pm._resolve_token("fed-hike", "no")[0], NO)
        self.assertEqual(pm._resolve_token("fed-hike", "1")[0], NO)
        raw = "7" * 70
        self.assertEqual(pm._resolve_token(raw, None), (raw, raw))
        with self.assertRaises(pm.PolymarketError):
            pm._resolve_token("fed-hike", "maybe")


class CLITests(FakeAPITestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = pm.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_every_subcommand_runs(self):
        for argv in (["search", "fed"], ["top"], ["event", "fed-cuts"], ["market", "fed-hike"],
                     ["price", "fed-hike"], ["book", "fed-hike"], ["history", "fed-hike"],
                     ["trades", "fed-hike"]):
            code, out, _ = self.run_cli(*argv)
            self.assertEqual(code, 0, argv)
            self.assertTrue(out.strip(), argv)

    def test_json_flag_emits_json(self):
        code, out, _ = self.run_cli("--json", "price", "fed-hike", "--outcome", "no")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["token_id"], NO)

    def test_unknown_slug_exits_1(self):
        code, out, err = self.run_cli("market", "nope")
        self.assertEqual(code, 1)
        self.assertIn("error:", err)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
