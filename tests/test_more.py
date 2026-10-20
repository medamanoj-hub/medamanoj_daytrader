"""Offline tests for news, analyst, screener, rebalance, tax and alerts."""

import datetime as dt
import importlib.util
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_all import FX, SK, Sandbox  # noqa: E402

FAR = "200000"  # hours: fixtures are dated, so look back far enough


def load_module(skill, script):
    spec = importlib.util.spec_from_file_location(script[:-3], os.path.join(SK, skill, script))
    sys.path.insert(0, os.path.join(SK, skill))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestNews(Sandbox):
    def test_announcements_and_headlines(self):
        out = self.run_tool("india-news", "news.py", "scan", "--symbols", "LT,ITC", "--hours", FAR,
                            "--ann-file", os.path.join(FX, "announcements.json"),
                            "--rss-file", "ITC=" + os.path.join(FX, "rss_ITC.xml"))
        self.assertRegex(out, r"\[HIGH\s*\].*Resignation of Statutory Auditor")
        self.assertRegex(out, r"\[MEDIUM\].*Board Meeting")
        self.assertRegex(out, r"\[MEDIUM\].*buyback")
        self.assertNotIn("Trading Window", out)       # routine filing hidden
        self.assertNotIn("INFY", out)                 # not asked for
        self.assertEqual(out.count("announces buyback"), 1)  # duplicate headline merged

    def test_new_only_once(self):
        args = ["scan", "--symbols", "LT", "--hours", FAR, "--new",
                "--ann-file", os.path.join(FX, "announcements.json")]
        self.assertIn("Resignation", self.run_tool("india-news", "news.py", *args))
        self.assertIn("Nothing new", self.run_tool("india-news", "news.py", *args))

    def test_flags(self):
        news = load_module("india-news", "news.py")
        self.assertEqual(news.flag("CFO resigns with immediate effect"), "HIGH")
        self.assertEqual(news.flag("Invocation of pledge by lender"), "HIGH")
        self.assertEqual(news.flag("Outcome of Board Meeting - dividend declared"), "MEDIUM")
        self.assertEqual(news.flag("Loss of share certificate"), "NOISE")


class TestAnalystScreener(Sandbox):
    def test_nse_shape_growth_and_pe(self):
        an = load_module("india-analyst", "analyst.py")
        with open(os.path.join(FX, "results_SAMPLECO.json")) as f:
            rows = [an.from_nse_row(r) for r in json.load(f)["resCmpData"]]
        m = an.analyse("SAMPLECO", rows, price=1340.0)
        self.assertEqual(m["as_of"], "2026-09-30")
        self.assertEqual(m["revenue_cr"], 24500.0)          # lakh -> crore
        self.assertAlmostEqual(m["rev_growth_yoy"], 16.7, places=1)
        self.assertAlmostEqual(m["profit_growth_qoq"], 11.4, places=1)
        self.assertEqual(m["eps_ttm"], 67.6)
        self.assertAlmostEqual(m["pe_ttm"], 19.8, places=1)
        self.assertEqual(m["loss_quarters"], 0)

    def test_csv_then_screen(self):
        self.run_tool("india-analyst", "analyst.py", "facts", "LOSSCO",
                      "--file", os.path.join(FX, "results_LOSSCO.csv"))
        an = load_module("india-analyst", "analyst.py")
        with open(os.path.join(FX, "results_SAMPLECO.json")) as f:
            rows = [an.from_nse_row(r) for r in json.load(f)["resCmpData"]]
        mets = self.j("trader/metrics.json")
        mets["SAMPLECO"] = an.analyse("SAMPLECO", rows, price=1340.0)
        mets["THIRDCO"] = {"symbol": "THIRDCO", "rev_growth_yoy": 25.0, "profit_growth_yoy": 45.0,
                           "net_margin_pct": 9.0, "loss_quarters": 0, "pe_ttm": 55}
        self.put("trader/metrics.json", mets)
        self.assertEqual(mets["LOSSCO"]["loss_quarters"], 1)
        self.put("trader/rules.json", {
            "rules": [{"field": "rev_growth_yoy", "op": ">=", "value": 10},
                      {"field": "loss_quarters", "op": "==", "value": 0},
                      {"field": "pe_ttm", "op": "<=", "value": 60}],
            "rank": [{"field": "profit_growth_yoy", "weight": 2}, {"field": "pe_ttm", "weight": -1}]})
        out = self.run_tool("india-screener", "screen.py", "run", "--why", "--targets", "equal")
        self.assertIn("2 of 3 passed", out)
        self.assertRegex(out, r"LOSSCO\s+loss_quarters 1")
        s = self.j("trader/screen.json")
        self.assertEqual([p["symbol"] for p in s["passed"]], ["THIRDCO", "SAMPLECO"])
        t = self.j("trader/targets.json")
        self.assertEqual([x["weight"] for x in t["targets"]], [50.0, 50.0])

    def test_missing_field_policy(self):
        sc = load_module("india-screener", "screen.py")
        mets = {"A": {"x": 5}, "B": {}}
        r, f, _ = sc.screen(mets, {"rules": [{"field": "x", "op": ">", "value": 1}]})
        self.assertEqual([t[0] for t in r], ["A"])
        r, f, _ = sc.screen(mets, {"rules": [{"field": "x", "op": ">", "value": 1}], "missing": "skip"})
        self.assertEqual(sorted(t[0] for t in r), ["A", "B"])


class TestRebalance(Sandbox):
    def setUp(self):
        super().setUp()
        self.put("trader/targets.json", {"targets": [
            {"symbol": "HDFCBANK", "weight": 40}, {"symbol": "ICICIBANK", "weight": 40},
            {"symbol": "ITC", "weight": 20}]})
        self.put("trader/portfolio.json", {"cash": 10000, "positions": {
            "HDFCBANK": {"qty": 20, "avg_price": 1500}, "ICICIBANK": {"qty": 10, "avg_price": 1300},
            "ITC": {"qty": 50, "avg_price": 380}, "TCS": {"qty": 2, "avg_price": 3000}}, "history": []})
        self.put("p.json", {"prices": {"HDFCBANK": 1700, "ICICIBANK": 1400, "ITC": 400, "TCS": 3500},
                            "as_of": dt.datetime.now().isoformat(timespec="seconds")})

    def test_drift_sells_first_and_cash_limited_buys(self):
        out = self.run_tool("india-rebalance", "rebalance.py", "--prices", "p.json", "--write")
        self.assertIn("not in targets, left alone", out)
        o = self.j("trader/orders.json")["orders"]
        got = [(x["side"], x["symbol"], x["qty"]) for x in o]
        self.assertEqual(got[:2], [("SELL", "HDFCBANK", 7), ("SELL", "ITC", 11)])
        self.assertEqual(got[2], ("BUY", "ICICIBANK", 1))   # only ₹1,500 spendable above the buffer
        self.assertIn("after sells settle", out)
        # risk accepts the sells (shares are held) and the small buy
        self.run_tool("india-risk", "check.py")

    def test_band_and_open_orders_guard(self):
        out = self.run_tool("india-rebalance", "rebalance.py", "--prices", "p.json", "--band", "20")
        self.assertIn("No trades needed", out)
        self.run_tool("india-rebalance", "rebalance.py", "--prices", "p.json", "--write")
        out = self.run_tool("india-rebalance", "rebalance.py", "--prices", "p.json", "--write", ok=(1,))
        self.assertIn("open order", out)

    def test_sell_untargeted(self):
        self.run_tool("india-rebalance", "rebalance.py", "--prices", "p.json", "--write", "--sell-untargeted")
        o = self.j("trader/orders.json")["orders"]
        self.assertIn(("SELL", "TCS", 2), [(x["side"], x["symbol"], x["qty"]) for x in o])


class TestTax(Sandbox):
    def setUp(self):
        super().setUp()
        self.put("trader/trades.json", [
            {"date": "2017-06-01", "symbol": "INFY", "side": "BUY", "qty": 10, "price": 900, "charges": 0},
            {"date": "2026-05-10", "symbol": "INFY", "side": "SELL", "qty": 10, "price": 1500, "charges": 0},
            {"date": "2026-01-05", "symbol": "ITC", "side": "BUY", "qty": 100, "price": 450, "charges": 0},
            {"date": "2026-06-15", "symbol": "ITC", "side": "SELL", "qty": 100, "price": 420, "charges": 0},
            {"date": "2026-08-01", "symbol": "AXISBANK", "side": "BUY", "qty": 30, "price": 1200, "charges": 0},
            {"date": "2024-01-01", "symbol": "LT", "side": "BUY", "qty": 10, "price": 3000, "charges": 0}])
        self.put("trader/fmv_2018.json", {"INFY": 1150})
        self.run_tool("india-tax", "tax.py", "import-csv", os.path.join(FX, "tradebook.csv"))

    def test_gains_setoff_grandfathering(self):
        out = self.run_tool("india-tax", "tax.py", "gains", "--fy", "2026-27")
        self.assertIn("(grandfathered)", out)
        self.assertIn("net ₹1,000.00", out)                # ST: +4,000 - 3,000
        self.assertIn("exemption used ₹3,500.00", out)     # LT: (1,500 - 1,150) x 10
        self.assertIn("₹208.00", out)                      # 1,000 x 20 % x 1.04

    def test_long_term_boundary_and_fifo(self):
        tx = load_module("india-tax", "tax.py")
        self.assertFalse(tx.is_long(dt.date(2025, 1, 2), dt.date(2026, 1, 2)))   # exactly 12 months
        self.assertTrue(tx.is_long(dt.date(2025, 1, 2), dt.date(2026, 1, 3)))
        self.assertEqual(tx.fy_of(dt.date(2026, 3, 31)), "2025-26")
        self.assertEqual(tx.fy_of(dt.date(2026, 4, 1)), "2026-27")
        cfg = {"charges": {"stt_pct": 0.1}}
        trades = [{"date": "2026-01-01", "symbol": "X", "side": "BUY", "qty": 5, "price": 100},
                  {"date": "2026-02-01", "symbol": "X", "side": "BUY", "qty": 5, "price": 200},
                  {"date": "2026-03-01", "symbol": "X", "side": "SELL", "qty": 6, "price": 150}]
        r, lots, _ = tx.match(trades, cfg, {})
        self.assertEqual([(x["qty"], x["gain"]) for x in r], [(5, 250.0), (1, -50.0)])
        self.assertEqual(lots[0]["qty"], 4)

    def test_lt_loss_cannot_offset_st_gain(self):
        tx = load_module("india-tax", "tax.py")
        s = tx.setoff([{"term": "ST", "gain": 10000}, {"term": "LT", "gain": -4000}], tx.TAX_DEFAULTS)
        self.assertEqual(s["net_st"], 10000)
        self.assertEqual(s["carry_forward_lt"], 4000)
        s = tx.setoff([{"term": "LT", "gain": 200000}, {"term": "ST", "gain": -50000}], tx.TAX_DEFAULTS)
        self.assertEqual(s["net_lt"], 150000)
        self.assertEqual(s["taxable_lt"], 25000)

    def test_harvest(self):
        self.put("p.json", {"AXISBANK": 1100, "LT": 3600})
        out = self.run_tool("india-tax", "tax.py", "harvest", "--prices", "p.json")
        self.assertRegex(out, r"AXISBANK .*loss -₹3,000\.00.*Tax saved this year: ~₹208\.00")
        self.assertRegex(out, r"LT\s+sell 10 of 10")
        self.assertIn("no wash-sale rule", out)


class TestAlerts(Sandbox):
    def test_market_clock(self):
        al = load_module("india-alerts", "alerts.py")
        IST = al.IST
        st, why, nxt = al.status(dt.datetime(2026, 10, 20, 11, 0, tzinfo=IST))   # Dussehra
        self.assertEqual(st, "CLOSED")
        self.assertIn("Dussehra", why)
        self.assertEqual(nxt.date(), dt.date(2026, 10, 21))
        self.assertEqual(al.status(dt.datetime(2026, 10, 21, 9, 5, tzinfo=IST))[0], "PRE-OPEN")
        self.assertEqual(al.status(dt.datetime(2026, 10, 21, 15, 29, tzinfo=IST))[0], "OPEN")
        self.assertEqual(al.status(dt.datetime(2026, 10, 21, 15, 45, tzinfo=IST))[0], "POST-CLOSE")
        st, _, nxt = al.status(dt.datetime(2026, 11, 7, 10, 0, tzinfo=IST))   # Saturday before Diwali
        self.assertEqual(st, "CLOSED")
        self.assertEqual(nxt.date(), dt.date(2026, 11, 9))   # Monday; 10 Nov is a holiday

    def test_alerts_fire_once(self):
        self.run_tool("india-alerts", "alerts.py", "add", "RELIANCE", "below", "2800", "--kind", "stop")
        self.run_tool("india-alerts", "alerts.py", "add", "ITC", "above", "450", "--kind", "target")
        self.put("p.json", {"RELIANCE": 2790.5, "ITC": 441})
        out = self.run_tool("india-alerts", "alerts.py", "check", "--prices", "p.json", "--force")
        self.assertIn("ALERT A1 STOP", out)
        self.assertNotIn("A2", out)
        self.assertIn("nothing was sold", out)
        out = self.run_tool("india-alerts", "alerts.py", "check", "--prices", "p.json", "--force")
        self.assertIn("No alert hit", out)


if __name__ == "__main__":
    unittest.main()
