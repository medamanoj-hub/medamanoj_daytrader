"""Offline tests for the India trader skills. No network.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SK = os.path.join(ROOT, "skills")
FX = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, os.path.join(ROOT, "tests"))
import build_fixtures  # noqa: E402


def tool(skill, script):
    return os.path.join(SK, skill, script)


class Sandbox(unittest.TestCase):
    """Each test runs in a fresh folder with its own trader/ directory."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="itr-")
        os.makedirs(os.path.join(self.dir, "trader"))
        build_fixtures.build(self.dir)
        shutil.copy(os.path.join(FX, "EQUITY_L.csv"), os.path.join(self.dir, "trader", "nse_equity_list.csv"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_tool(self, skill, script, *args, ok=(0,)):
        p = subprocess.run([sys.executable, "-I", tool(skill, script)] + list(args), cwd=self.dir,
                           capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")})
        if p.returncode not in ok:
            self.fail("%s %s exited %d\nstdout:\n%s\nstderr:\n%s" % (
                script, " ".join(args), p.returncode, p.stdout, p.stderr))
        return p.stdout + p.stderr

    def j(self, rel):
        with open(os.path.join(self.dir, rel), encoding="utf-8") as f:
            return json.load(f)

    def put(self, rel, obj):
        with open(os.path.join(self.dir, rel), "w", encoding="utf-8") as f:
            json.dump(obj, f)


class TestShared(unittest.TestCase):
    def test_helper_copies_identical(self):
        for name, skills in (("trader_common.py", ("india-budget", "india-risk", "india-broker",
                                                   "india-rebalance", "india-tax", "india-alerts")),
                             ("nse_http.py", ("india-copycat", "india-insider", "india-news",
                                              "india-analyst", "india-alerts"))):
            first = os.path.join(SK, skills[0], name)
            for s in skills[1:]:
                self.assertTrue(filecmp.cmp(first, os.path.join(SK, s, name), shallow=False),
                                "%s differs in %s" % (name, s))

    def test_every_skill_has_frontmatter(self):
        for s in os.listdir(SK):
            with open(os.path.join(SK, s, "SKILL.md"), encoding="utf-8") as f:
                head = f.read(400)
            self.assertTrue(head.startswith("---\nname: %s\n" % s), s)
            self.assertIn("description:", head)


class TestCopycat(Sandbox):
    def test_mf_parse_percent_and_fraction(self):
        out = self.run_tool("india-copycat", "copycat.py", "mf", "amc_2026-08.xlsx", "--label", "flexi")
        self.assertIn("Sample Flexi Cap Fund", out)
        h = self.j("trader/holdings/flexi_2026-08-31.json")
        self.assertEqual(h["meta"]["as_of"], "2026-08-31")
        self.assertEqual([x["symbol"] for x in h["holdings"]][:3], ["HDFCBANK", "ICICIBANK", "RELIANCE"])
        self.assertEqual(len(h["holdings"]), 6)  # debt, T-bill and cash rows skipped
        self.assertAlmostEqual(h["meta"]["equity_pct_nav"], 30.05, places=2)

        # September file: two sheets, values stored as fractions (0.0945)
        out = self.run_tool("india-copycat", "copycat.py", "sheets", "amc_2026-09.xlsx")
        self.assertIn("SAMPLEFLEXI", out)
        self.run_tool("india-copycat", "copycat.py", "mf", "amc_2026-09.xlsx", "--sheet", "SAMPLEFLEXI",
                      "--label", "flexi")
        s = self.j("trader/holdings/flexi_2026-09-30.json")
        self.assertAlmostEqual(s["holdings"][0]["pct_nav"], 9.45, places=3)
        unl = [x for x in s["holdings"] if x["isin"] == "INE999Z01011"][0]
        self.assertIsNone(unl["symbol"])

    def test_wrong_sheet_is_explained(self):
        out = self.run_tool("india-copycat", "copycat.py", "mf", "amc_2026-09.xlsx", ok=(1,))
        self.assertIn("--sheet", out)

    def test_diff_and_copy(self):
        self.run_tool("india-copycat", "copycat.py", "mf", "amc_2026-08.xlsx", "--label", "f")
        self.run_tool("india-copycat", "copycat.py", "mf", "amc_2026-09.xlsx", "--sheet", "1", "--label", "f")
        out = self.run_tool("india-copycat", "copycat.py", "diff", "trader/holdings/f_2026-08-31.json",
                            "trader/holdings/f_2026-09-30.json")
        self.assertIn("+ LT", out)
        self.assertIn("- AXISBANK", out)
        self.assertRegex(out, r"ADDED[\s\S]*HDFCBANK")       # 1.2m -> 1.3m shares
        self.assertRegex(out, r"TRIMMED[\s\S]*RELIANCE")     # 400k -> 380k shares
        self.run_tool("india-copycat", "copycat.py", "copy", "trader/holdings/f_2026-09-30.json", "--top", "5")
        t = self.j("trader/targets.json")
        self.assertEqual(len(t["targets"]), 5)
        self.assertAlmostEqual(sum(x["weight"] for x in t["targets"]), 100.0, places=2)
        self.assertEqual(t["targets"][0]["symbol"], "HDFCBANK")
        self.assertAlmostEqual(t["targets"][0]["weight"], 9.45 / 30.75 * 100, delta=0.02)

    def test_investor_from_xbrl(self):
        self.put("prices.json", {"SMALLCO": 180})
        out = self.run_tool("india-copycat", "copycat.py", "investor", "kacholia",
                            "--xbrl", os.path.join(FX, "shp_SMALLCO.xml"), "--prices", "prices.json")
        self.assertIn("SMALLCO", out)
        self.assertIn("3.12 %", out)
        self.assertNotIn("SAMPLE MUTUAL FUND", out)


class TestInsider(Sandbox):
    def test_pit_json_filters_and_clusters(self):
        out = self.run_tool("india-insider", "insider.py", "pit", "--file", os.path.join(FX, "pit.json"),
                            "--market-only", "--insiders-only")
        self.assertIn("Insider trades: 3", out)          # ESOP row dropped
        self.assertIn("Cluster buying", out)
        self.assertIn("LT (2 people)", out)
        self.assertIn("₹1.80 cr", out)

    def test_pit_csv_download(self):
        out = self.run_tool("india-insider", "insider.py", "pit", "--file", os.path.join(FX, "pit.csv"),
                            "--save", "trader/p.json")
        rows = self.j("trader/p.json")
        self.assertEqual(rows[0]["symbol"], "INFY")
        self.assertEqual(rows[0]["side"], "BUY")
        self.assertEqual(rows[0]["shares"], 700)
        self.assertTrue(rows[0]["market"] and rows[0]["insider"])
        self.assertIn("Sample CFO", out)

    def test_deals_json_and_csv(self):
        out = self.run_tool("india-insider", "insider.py", "deals", "--kind", "bulk",
                            "--file", os.path.join(FX, "bulk.json"), "--who", "kacholia")
        self.assertIn("Bulk deals: 1", out)
        self.assertIn("SMALLCO", out)
        out = self.run_tool("india-insider", "insider.py", "deals", "--kind", "block",
                            "--file", os.path.join(FX, "block.csv"))
        self.assertIn("HDFCBANK", out)
        self.assertIn("₹205.14 cr", out)

    def test_new_alerts_once(self):
        self.put("trader/watchlist.json", {"symbols": ["LT", "HDFCBANK"]})
        files = ["--file", os.path.join(FX, "pit.json"), os.path.join(FX, "bulk.json"),
                 os.path.join(FX, "block.csv")]
        out = self.run_tool("india-insider", "insider.py", "new", "--who", "KACHOLIA", *files)
        self.assertIn("NEW since last check (4)", out)  # 2 LT insiders, 1 HDFCBANK block, 1 name match
        self.assertIn("name match", out)
        out = self.run_tool("india-insider", "insider.py", "new", "--who", "KACHOLIA", *files)
        self.assertIn("No new filings", out)


class TestPipeline(Sandbox):
    def setUp(self):
        super().setUp()
        self.put("trader/targets.json", {"source": "Sample", "targets": [
            {"symbol": "HDFCBANK", "weight": 30.67}, {"symbol": "ICICIBANK", "weight": 23.85},
            {"symbol": "RELIANCE", "weight": 17.56}, {"symbol": "INFY", "weight": 15.11},
            {"symbol": "ITC", "weight": 12.81}]})
        self.put("p.json", {"prices": {"HDFCBANK": 1712.4, "ICICIBANK": 1398.1, "RELIANCE": 2915.0,
                                       "INFY": 1520.55, "ITC": 412.3},
                            "as_of": dt.datetime.now().isoformat(timespec="seconds")})

    def size(self, *extra):
        return self.run_tool("india-budget", "size.py", "--budget", "50000", "--prices", "p.json",
                             "--write", *extra)

    def test_budget_rules(self):
        self.size()
        d = self.j("trader/orders.json")
        s = d["summary"]
        self.assertEqual(s["cash_buffer"], 5000)
        for o in d["orders"]:
            self.assertIsInstance(o["qty"], int)
            self.assertLessEqual(o["value"], 50000 * 0.25 + 0.01)       # cap
            self.assertEqual(o["order_type"], "LIMIT")
            self.assertGreater(o["limit_price"], o["last_price"])
        self.assertLessEqual(s["invested"] + s["est_charges"], 45000 + 0.01)  # buffer kept
        self.assertGreaterEqual(s["idle"], 0)

    def test_risk_blocks(self):
        self.put("trader/config.json", {"limits": {"max_order_value": 10000, "blocked_symbols": ["ITC"]}})
        self.put("trader/surveillance.json", {"ASM": ["INFY"]})
        self.size()
        out = self.run_tool("india-risk", "check.py", ok=(2,))
        d = self.j("trader/orders.json")
        st = {o["symbol"]: o["risk"] for o in d["orders"]}
        self.assertEqual(st["ITC"]["status"], "BLOCK")
        self.assertIn("blocked list", " ".join(st["ITC"]["reasons"]))
        self.assertIn("surveillance", " ".join(st["INFY"]["reasons"]))
        self.assertIn("max_order_value", " ".join(st["HDFCBANK"]["reasons"]))
        self.assertIn("blocked", out)

    def test_risk_rejects_intraday_market_and_price_drift(self):
        self.size()
        d = self.j("trader/orders.json")
        d["orders"][0]["product"] = "INTRADAY"
        d["orders"][1]["order_type"] = "MARKET"
        d["orders"][2]["limit_price"] = round(d["orders"][2]["last_price"] * 1.10, 2)
        d["orders"].append(dict(d["orders"][3], id="O9", side="SELL"))
        self.put("trader/orders.json", d)
        self.run_tool("india-risk", "check.py", ok=(2,))
        r = {o["id"]: " ".join(o["risk"]["reasons"]) for o in self.j("trader/orders.json")["orders"]}
        self.assertIn("delivery only", r["O1"])
        self.assertIn("market orders", r["O2"])
        self.assertIn("from last price", r["O3"])
        self.assertIn("no short selling", r["O9"])

    def test_broker_flow_paper(self):
        self.size()
        self.run_tool("india-risk", "check.py")
        self.run_tool("india-broker", "ticket.py", "master", "--file", os.path.join(FX, "scrip_master.json"))
        idx = self.j("trader/angel_eq_index.json")
        self.assertEqual(idx["instruments"]["NSE:RELIANCE"]["token"], "2885")
        self.assertNotIn("NFO:NIFTY", idx["instruments"])
        out = self.run_tool("india-broker", "ticket.py", "show", "O1")
        self.assertIn("READY", out)
        self.assertIn('"symboltoken": "1333"', out)
        self.assertIn('"producttype": "DELIVERY"', out)

        # no yes, no approval
        out = self.run_tool("india-broker", "ticket.py", "approve", "O1", "--confirm", "ok", ok=(1,))
        self.assertIn("--confirm yes", out)
        # live approval refused while paper is on
        self.run_tool("india-broker", "ticket.py", "approve", "O1", "--confirm", "yes", "--live", ok=(1,))
        # tampering after the risk check is caught
        d = self.j("trader/orders.json")
        d["orders"][0]["qty"] += 1
        self.put("trader/orders.json", d)
        out = self.run_tool("india-broker", "ticket.py", "approve", "O1", "--confirm", "yes", ok=(1,))
        self.assertIn("changed after the risk check", out)
        d["orders"][0]["qty"] -= 1
        self.put("trader/orders.json", d)

        self.run_tool("india-broker", "ticket.py", "approve", "O1", "--confirm", "yes")
        out = self.run_tool("india-broker", "ticket.py", "paper", "O1")
        self.assertIn("Paper fill", out)
        pf = self.j("trader/portfolio.json")
        o1 = [o for o in self.j("trader/orders.json")["orders"] if o["id"] == "O1"][0]
        self.assertEqual(pf["positions"]["HDFCBANK"]["qty"], o1["qty"])
        self.assertAlmostEqual(pf["cash"], 50000 - o1["value"] - o1["est_charges"], places=2)
        with open(os.path.join(self.dir, "trader", "approvals.jsonl")) as f:
            self.assertEqual(len(f.readlines()), 1)
        # re-running risk leaves the filled order alone
        self.run_tool("india-risk", "check.py")
        o1b = [o for o in self.j("trader/orders.json")["orders"] if o["id"] == "O1"][0]
        self.assertEqual(o1b["status"], "FILLED_PAPER")

    def test_retick(self):
        self.size()
        d = self.j("trader/orders.json")
        d["orders"][0]["limit_price"] = 1721.03  # not on a 0.10 tick
        self.put("trader/orders.json", d)
        self.run_tool("india-risk", "check.py")
        self.run_tool("india-broker", "ticket.py", "master", "--file", os.path.join(FX, "scrip_master.json"))
        out = self.run_tool("india-broker", "ticket.py", "show", "O1")
        self.assertIn("not a multiple of tick", out)
        self.run_tool("india-broker", "ticket.py", "retick", "O1")
        o = self.j("trader/orders.json")["orders"][0]
        self.assertEqual(o["limit_price"], 1721.1)
        self.assertEqual(o["status"], "PROPOSED")


if __name__ == "__main__":
    unittest.main()
