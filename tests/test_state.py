"""状態の導出。status.yaml ではなく bitbank paper の実測から組み立てる。"""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from nampinonychus.state import (
    build_status,
    count_closed_positions,
    current_round,
    derive,
    derive_ladder,
    parse_trades,
)
from tests import helpers
from tests.helpers import load_config, market

NOW = helpers.at("2026-08-18T09:00:00+09:00")


def trade(side: str, amount: str, price: str, filled_at: str, fee: str = "0", pair: str = "btc_jpy"):
    return {
        "id": f"{side}-{filled_at}",
        "pair": pair,
        "side": side,
        "type": "limit",
        "amount": float(amount),
        "fillPrice": float(price),
        "feeQuote": float(fee),
        "filledAt": filled_at,
    }


class RoundTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_建玉がゼロに戻ったら数え直す(self):
        rows = [
            trade("buy", "0.0054", "14550000", "2026-08-01T00:00:00.000Z"),
            trade("sell", "0.0054", "14986500", "2026-08-05T00:00:00.000Z"),
            trade("buy", "0.0060", "14000000", "2026-08-17T23:30:00.000Z"),
        ]
        trades = parse_trades(rows, "btc_jpy", helpers.TZ)
        round_trades = current_round(trades)
        self.assertEqual(len(round_trades), 1)
        self.assertEqual(round_trades[0].fill_price, Decimal("14000000"))

    def test_段数はラウンド内の買い件数(self):
        rows = [
            trade("buy", "0.0054", "14550000", "2026-08-16T00:00:00.000Z"),
            trade("buy", "0.0070", "14113500", "2026-08-17T00:00:00.000Z"),
        ]
        ladder = derive_ladder(parse_trades(rows, "btc_jpy", helpers.TZ), self.config, NOW)
        self.assertEqual(ladder.step, 2)
        self.assertEqual(ladder.last_fill_price_jpy, Decimal("14113500"))

    def test_クールダウンは直近の買いから設定ぶん後(self):
        rows = [trade("buy", "0.0054", "14550000", "2026-08-18T00:00:00.000Z")]
        ladder = derive_ladder(parse_trades(rows, "btc_jpy", helpers.TZ), self.config, NOW)
        expected = helpers.at("2026-08-18T09:00:00+09:00") + timedelta(
            hours=self.config.cooldown_hours_after_fill
        )
        self.assertEqual(ladder.cooldown_until, expected)

    def test_当日の約定回数は日本時間で数える(self):
        rows = [
            # JST では 2026-08-17 23:00 → 当日ではない
            trade("buy", "0.0001", "14550000", "2026-08-17T14:00:00.000Z"),
            # JST では 2026-08-18 08:30 → 当日
            trade("buy", "0.0001", "14500000", "2026-08-17T23:30:00.000Z"),
        ]
        ladder = derive_ladder(parse_trades(rows, "btc_jpy", helpers.TZ), self.config, NOW)
        self.assertEqual(ladder.fills_today, 1)

    def test_他のペアの約定は数えない(self):
        rows = [trade("buy", "1", "500", "2026-08-18T00:00:00.000Z", pair="xrp_jpy")]
        self.assertEqual(len(parse_trades(rows, "btc_jpy", helpers.TZ)), 0)

    def test_決済回数を数える(self):
        rows = [
            trade("buy", "0.0054", "14550000", "2026-08-01T00:00:00.000Z"),
            trade("sell", "0.0054", "14986500", "2026-08-05T00:00:00.000Z"),
            trade("buy", "0.0060", "14000000", "2026-08-10T00:00:00.000Z"),
        ]
        self.assertEqual(count_closed_positions(parse_trades(rows, "btc_jpy", helpers.TZ)), 1)


class DeriveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def derive(self, **kwargs):
        args = {
            "assets_rows": [
                {"asset": "jpy", "total": 921430, "locked": 0, "available": 921430},
                {"asset": "btc", "total": 0.0054, "locked": 0, "available": 0.0054},
            ],
            "pnl_report": {
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
            },
            "order_rows": [],
            "history_rows": [trade("buy", "0.0054", "14550000", "2026-08-18T00:00:00.000Z")],
        }
        args.update(kwargs)
        return derive(
            config=self.config,
            now=NOW,
            last_price=Decimal("14700000"),
            **args,
        )

    def test_建玉と平均取得単価をpnlから取る(self):
        state = self.derive()
        self.assertEqual(state.position.amount, Decimal("0.0054"))
        self.assertEqual(state.position.avg_cost_jpy, Decimal("14550000"))
        self.assertFalse(state.position_mismatch)

    def test_pnlにペアが無ければ建玉なし(self):
        """建玉ゼロかつ実現損益ゼロのペアは pnl に出力されない。"""
        state = self.derive(
            pnl_report={"perPair": {}, "total": {"realizedPnl": 0, "unrealizedPnl": 0, "totalPnl": 0}},
            assets_rows=[{"asset": "jpy", "total": 1000000, "locked": 0, "available": 1000000}],
            history_rows=[],
        )
        self.assertEqual(state.position.amount, Decimal(0))
        self.assertIsNone(state.position.avg_cost_jpy)
        self.assertFalse(state.position_mismatch)

    def test_残高と建玉が食い違えば印を付ける(self):
        state = self.derive(
            assets_rows=[
                {"asset": "jpy", "total": 921430, "locked": 0, "available": 921430},
                {"asset": "btc", "total": 0.0100, "locked": 0, "available": 0.0100},
            ]
        )
        self.assertTrue(state.position_mismatch)

    def test_未約定注文を売買で分ける(self):
        state = self.derive(
            order_rows=[
                {
                    "id": "b1",
                    "pair": "btc_jpy",
                    "side": "buy",
                    "type": "limit",
                    "price": 14113500,
                    "amount": 0.007,
                    "createdAt": "2026-08-18T00:00:00.000Z",
                },
                {
                    "id": "s1",
                    "pair": "btc_jpy",
                    "side": "sell",
                    "type": "limit",
                    "price": 14986500,
                    "amount": 0.0027,
                    "createdAt": "2026-08-18T00:00:00.000Z",
                },
            ]
        )
        self.assertEqual(len(state.pending_buy), 1)
        self.assertEqual(len(state.pending_sell), 1)

    def test_総資産は現金と建玉評価額の合計(self):
        state = self.derive()
        self.assertEqual(state.account.equity_jpy, Decimal("921430") + Decimal("0.0054") * Decimal("14700000"))


class StatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_観測できなかった値はnullのまま(self):
        document = build_status(
            config=self.config,
            now=NOW,
            run_id="run",
            state_label="NOT_INITIALIZED",
            market=None,
            state=None,
            trades=(),
            action="HOLD",
            reason="口座が未初期化",
            price_source=None,
        )
        self.assertIsNone(document["account"]["cash_jpy"])
        self.assertIsNone(document["market"]["last_price"])
        self.assertEqual(document["mood"], "待機")
        self.assertEqual(document["schema_version"], 1)

    def test_状態に応じた気分を入れる(self):
        document = build_status(
            config=self.config,
            now=NOW,
            run_id="run",
            state_label="LADDERING",
            market=market(),
            state=None,
            trades=(),
            action="BUY",
            reason="2段目",
            price_source="bitbank ticker btc_jpy --format=json --machine",
        )
        self.assertEqual(document["mood"], "満足")
        self.assertEqual(document["market"]["last_price"], 14700000.0)
        self.assertEqual(document["market"]["anchor_price"], 15000000.0)


if __name__ == "__main__":
    unittest.main()
