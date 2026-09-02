"""状態の導出。status.yaml ではなく bitbank paper の実測から組み立てる。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from nampinonychus.state import (
    build_status,
    last_tick_at,
    stopped_hours,
    count_closed_positions,
    current_round,
    derive,
    derive_ladder,
    parse_trades,
    sellable,
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


class 売れる数量Test(unittest.TestCase):
    """口座の残高より多く売ろうとしないこと。

    2026-08-22 に本番で止まった。`paper pnl` は約定履歴から建玉を計算し、
    `paper assets` は口座が持つ残高を返す。後者は浮動小数点で積まれるため、
    13ラウンド売買を重ねたところで前者からずれた。

        pnl    position  0.0032
        assets available 0.0031999999999999967

    決定的コードは pnl の 0.0032 を成行で売ろうとし、CLI に
    `insufficient btc: need 0.0032, have 0.0031999999999999967` と
    拒否され続けた。15分ごとに同じ判断を繰り返し、11日間・400回以上。
    """

    UNIT = Decimal("0.0001")
    HELD = Decimal("0.0031999999999999967")

    def test_残高までしか売らない(self):
        self.assertEqual(
            sellable(Decimal("0.0032"), self.HELD, self.UNIT), Decimal("0.0031")
        )

    def test_取引単位の倍数に切り捨てる(self):
        self.assertEqual(
            sellable(Decimal("1"), Decimal("0.00125"), self.UNIT), Decimal("0.0012")
        )

    def test_一単位に満たない残高は建玉として数えない(self):
        """0.0031 を売ったあとに残る端数。CLI が受け付けないので売れない。

        建玉として数え続けると、ラウンドが閉じず1段目が再武装しない。
        """
        self.assertEqual(
            sellable(Decimal("0.0001"), Decimal("0.0000999999999999967"), self.UNIT),
            Decimal(0),
        )

    def test_単位が分からなければ残高だけで抑える(self):
        self.assertEqual(sellable(Decimal("0.0032"), self.HELD, None), self.HELD)



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

    def test_残高が建玉にわずかに足りなくても超えて売らない(self):
        """本番で止まった状態そのもの。pnl 0.0032 / 残高 0.0031999999999999967。"""
        state = self.derive(
            assets_rows=[
                {"asset": "jpy", "total": 967920, "locked": 0, "available": 967920},
                {
                    "asset": "btc",
                    "total": 0.0031999999999999967,
                    "locked": 0,
                    "available": 0.0031999999999999967,
                },
            ],
            pnl_report={
                "perPair": {
                    "btc_jpy": {
                        "pair": "btc_jpy",
                        "position": 0.0032,
                        "avgCost": 12438813,
                        "currentPrice": 12415315,
                        "realizedPnl": 7726.4,
                        "unrealizedPnl": -75.19,
                        "totalPnl": 7651.2,
                    }
                }
            },
            history_rows=[trade("buy", "0.0032", "12438813", "2026-08-22T00:00:00.000Z")],
            unit_amount=Decimal("0.0001"),
        )
        self.assertEqual(state.position.amount, Decimal("0.0031"))
        self.assertLessEqual(state.position.amount, state.account.base_available)
        # 端数のずれは異常ではない。1単位を超えて食い違ったときだけ立てる。
        self.assertFalse(state.position_mismatch)

    def test_売れない端数だけになったらラウンドを畳む(self):
        """建玉として数え続けると1段目が再武装せず、次のラウンドが始まらない。"""
        state = self.derive(
            assets_rows=[
                {"asset": "jpy", "total": 999000, "locked": 0, "available": 999000},
                {
                    "asset": "btc",
                    "total": 0.0000999999999999967,
                    "locked": 0,
                    "available": 0.0000999999999999967,
                },
            ],
            pnl_report={
                "perPair": {
                    "btc_jpy": {
                        "pair": "btc_jpy",
                        "position": 0.0001,
                        "avgCost": 12438813,
                        "currentPrice": 12415315,
                        "realizedPnl": 7726.4,
                        "unrealizedPnl": -2,
                        "totalPnl": 7724,
                    }
                }
            },
            history_rows=[
                trade("buy", "0.0032", "12438813", "2026-08-22T00:00:00.000Z"),
                trade("sell", "0.0031", "12500000", "2026-08-22T01:00:00.000Z"),
            ],
            unit_amount=Decimal("0.0001"),
        )
        self.assertEqual(state.position.amount, Decimal(0))
        self.assertIsNone(state.position.avg_cost_jpy)
        self.assertEqual(state.ladder.step, 0)
        self.assertIsNone(state.ladder.last_fill_price_jpy)

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


class LastTickTest(unittest.TestCase):
    """停止していた時間の測定。ペーパー口座に触る前に読む必要がある。"""

    def setUp(self) -> None:
        self.config = load_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "var").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_state(self, body: str) -> None:
        (self.root / self.config.state_path).write_text(body, encoding="utf-8")

    def test_最後の約定判定の時刻を読む(self):
        self.write_state(json.dumps({"lastTickAt": "2026-08-18T00:00:00.000Z"}))
        self.assertEqual(
            last_tick_at(self.config, self.root), helpers.at("2026-08-18T09:00:00+09:00")
        )

    def test_停止していた時間を測る(self):
        self.write_state(json.dumps({"lastTickAt": "2026-08-17T00:00:00.000Z"}))
        self.assertAlmostEqual(stopped_hours(self.config, NOW, self.root), 24.0, places=3)

    def test_ファイルが無ければ分からない(self):
        self.assertIsNone(stopped_hours(self.config, NOW, self.root))

    def test_壊れていれば分からない(self):
        self.write_state("{壊れている")
        self.assertIsNone(stopped_hours(self.config, NOW, self.root))

    def test_値が無ければ分からない(self):
        self.write_state(json.dumps({"version": 3}))
        self.assertIsNone(stopped_hours(self.config, NOW, self.root))


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
