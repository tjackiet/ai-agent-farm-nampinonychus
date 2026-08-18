"""1周の流れ。観測に失敗した回は status.yaml を更新しない。"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from nampinonychus import cli
from nampinonychus.run import run_once
from tests import helpers
from tests.helpers import FakeCli, default_responses, load_config

NOW = helpers.at("2026-08-18T09:00:00+09:00")

POSITION_PNL = {
    "perPair": {
        "btc_jpy": {
            "pair": "btc_jpy",
            "position": 0.0054,
            "avgCost": 14550000,
            "currentPrice": 14700000,
            "realizedPnl": 0,
            "unrealizedPnl": 810,
            "totalPnl": 810,
        }
    },
    "total": {"realizedPnl": 0, "unrealizedPnl": 810, "totalPnl": 810},
}

BUY_HISTORY = [
    {
        "id": "t1",
        "pair": "btc_jpy",
        "side": "buy",
        "type": "limit",
        "amount": 0.0054,
        "fillPrice": 14550000,
        "feeQuote": 0,
        "filledAt": "2026-08-15T00:00:00.000Z",
    }
]


class CycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cycle(self, fake: FakeCli, config=None):
        cfg = config if config is not None else self.config
        client = cli.Client(cfg, runner=fake)
        return run_once(cfg, client, NOW, repo_root=self.root)

    def read_journal(self) -> list[dict]:
        path = self.root / "var" / "memory" / "decisions" / "2026-08-18.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_dry_runでは発注コマンドを組み立てるだけ(self):
        fake = FakeCli(default_responses())
        cycle = self.run_cycle(fake)
        self.assertEqual(cycle.action, "BUY")
        self.assertTrue(cycle.dry_run)
        self.assertEqual(len(cycle.orders), 1)
        self.assertFalse(cycle.orders[0]["executed"])
        self.assertIn("--price=14925000", str(cycle.orders[0]["cmd"]))
        self.assertNotIn("paper create-order", " ".join(fake.calls))

    def test_判断ログをHOLDでも残す(self):
        fake = FakeCli(default_responses(last=15_000_000))
        cycle = self.run_cycle(fake)
        self.assertEqual(cycle.action, "HOLD")
        records = self.read_journal()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "HOLD")
        self.assertTrue(records[0]["sources"])

    def test_status_yamlを書き出す(self):
        self.run_cycle(FakeCli(default_responses()))
        document = yaml.safe_load((self.root / "var" / "status.yaml").read_text(encoding="utf-8"))
        self.assertEqual(document["state"], "IDLE")
        self.assertEqual(document["market"]["anchor_price"], 15000000.0)
        self.assertEqual(document["account"]["cash_jpy"], 1000000.0)

    def test_リポジトリのstatus_yamlは書き換えない(self):
        """実行のたびに作業ツリーが汚れると、運用中に git の操作が止まる。"""
        sample = self.root / "status.yaml"
        sample.write_text("# スキーマの見本\n", encoding="utf-8")
        self.run_cycle(FakeCli(default_responses()))
        self.assertEqual(sample.read_text(encoding="utf-8"), "# スキーマの見本\n")
        self.assertTrue((self.root / "var" / "status.yaml").is_file())

    def test_価格の出典はtickerである(self):
        """判断に使った数値には、その数値を返したコマンドを添える（CLAUDE.md）。"""
        self.run_cycle(FakeCli(default_responses()))
        document = yaml.safe_load((self.root / "var" / "status.yaml").read_text(encoding="utf-8"))
        self.assertIn("ticker", document["market"]["source"])
        self.assertNotIn("status", document["market"]["source"])

    def test_観測に失敗したらstatusを更新しない(self):
        fake = FakeCli(default_responses(), errors={"ticker": "upstream 503"})
        cycle = self.run_cycle(fake)
        self.assertEqual(cycle.action, "HOLD")
        self.assertIsNotNone(cycle.error)
        self.assertFalse(cycle.status_written)
        self.assertFalse((self.root / "var" / "status.yaml").exists())
        # 失敗しても判断ログは残す
        self.assertEqual(len(self.read_journal()), 1)

    def test_口座が未初期化なら何もしない(self):
        fake = FakeCli(
            default_responses(),
            errors={"paper tick": "paper state not initialized. Run 'bitbank paper init --jpy=<amount>' first."},
        )
        cycle = self.run_cycle(fake)
        self.assertEqual(cycle.action, "HOLD")
        self.assertEqual(cycle.state, "NOT_INITIALIZED")

    def test_サーキットブレイク中は口座を見にいかない(self):
        responses = default_responses()
        responses["circuit-break"] = {"mode": "CIRCUIT_BREAK", "fee_type": "NORMAL", "timestamp": 1}
        cycle = self.run_cycle(FakeCli(responses))
        self.assertEqual(cycle.action, "HOLD")
        self.assertIn("サーキットブレイク", cycle.reason)

    def test_実行モードでは発注する(self):
        config = dataclasses.replace(self.config, dry_run=False)
        fake = FakeCli(default_responses())
        cycle = self.run_cycle(fake, config)
        self.assertEqual(cycle.action, "BUY")
        self.assertTrue(cycle.orders[0]["executed"])
        self.assertIn("paper create-order", " ".join(fake.calls))

    def test_取消が約定済みで返ったら発注しない(self):
        """取消より約定が優先される（risk-policy.md「発注と取消の競合」）。"""
        config = dataclasses.replace(self.config, dry_run=False)
        responses = default_responses(
            pnl=POSITION_PNL,
            history=BUY_HISTORY,
            assets=[
                {"asset": "jpy", "total": 921430, "locked": 0, "available": 921430},
                {"asset": "btc", "total": 0.0054, "locked": 0, "available": 0.0054},
            ],
            active_orders=[
                {
                    "id": "s-old",
                    "pair": "btc_jpy",
                    "side": "sell",
                    "type": "limit",
                    "price": 14000000,
                    "amount": 0.0054,
                    "createdAt": "2026-08-15T00:00:00.000Z",
                }
            ],
        )
        fake = FakeCli(
            responses,
            errors={"paper cancel-order": "open order not found: s-old (may have already filled)"},
        )
        cycle = self.run_cycle(fake, config)
        self.assertEqual(cycle.action, "HOLD")
        self.assertIn("約定していた", cycle.reason)
        self.assertNotIn("paper create-order", " ".join(fake.calls))

    def test_発注に失敗したらstatusを更新しない(self):
        config = dataclasses.replace(self.config, dry_run=False)
        fake = FakeCli(default_responses(), errors={"paper create-order": "insufficient jpy"})
        cycle = self.run_cycle(fake, config)
        self.assertEqual(cycle.action, "HOLD")
        self.assertIsNotNone(cycle.error)
        self.assertFalse((self.root / "var" / "status.yaml").exists())


if __name__ == "__main__":
    unittest.main()
