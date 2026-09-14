"""利確幅の測定。数字が戦略の判断材料になるため、計算部分を押さえる。"""

from __future__ import annotations

import importlib.util
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from nampinonychus import state as state_module
from tests import helpers

_SPEC = importlib.util.spec_from_file_location(
    "measure_take_profit",
    Path(__file__).resolve().parent.parent / "scripts" / "measure_take_profit.py",
)
measure = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(measure)

START = helpers.at("2026-08-01T00:00:00+09:00")


def point(minutes: int, price: int):
    return (START + timedelta(minutes=minutes), Decimal(price))


def trade_row(side, amount, price, at, type_="limit", fee=0):
    return {
        "id": f"{side}-{at}",
        "pair": "btc_jpy",
        "side": side,
        "type": type_,
        "amount": str(amount),
        "fillPrice": str(price),
        "feeQuote": str(fee),
        "filledAt": at,
    }


class PricePointTest(unittest.TestCase):
    def test_価格のある回だけを古い順に取る(self):
        records = [
            {"run_id": "2026-08-01T00:30:00+09:00", "price": 200},
            {"run_id": "2026-08-01T00:00:00+09:00", "price": 100},
            {"run_id": "2026-08-01T01:00:00+09:00", "price": None},  # 観測に失敗した回
            {"run_id": "壊れている", "price": 300},
            {"price": 400},
        ]
        points = measure.price_points(records, helpers.TZ)
        self.assertEqual([int(p) for _, p in points], [100, 200])

    def test_期間で切る(self):
        points = [point(0, 100), point(15, 200), point(30, 300)]
        seen = measure.between(points, START, START + timedelta(minutes=15))
        self.assertEqual([int(p) for _, p in seen], [200])


class ReachTest(unittest.TestCase):
    def test_届いた最初の点を返す(self):
        points = [point(0, 100), point(15, 120), point(30, 150)]
        self.assertEqual(measure.first_at_or_above(points, Decimal(120))[1], Decimal(120))
        self.assertIsNone(measure.first_at_or_above(points, Decimal(200)))

    def test_終わりの直前の点を返す(self):
        points = [point(0, 100), point(15, 120), point(60, 150)]
        last = measure.last_at_or_before(points, START + timedelta(minutes=30))
        self.assertEqual(last[1], Decimal(120))


class RoundTest(unittest.TestCase):
    def setUp(self) -> None:
        rows = [
            trade_row("buy", "0.008", 10_000_000, "2026-08-01T00:00:00+09:00"),
            trade_row("buy", "0.010", 9_900_000, "2026-08-01T01:00:00+09:00"),
            trade_row("sell", "0.018", 10_000_000, "2026-08-01T10:00:00+09:00"),
        ]
        self.trades = state_module.parse_trades(rows, "btc_jpy", helpers.TZ)
        self.round = state_module.rounds(self.trades)[0]

    def test_最後に買った時刻を返す(self):
        """平均取得単価が確定するのは最後の買い。そこから戻りを測る。"""
        self.assertEqual(
            measure.last_buy_at(self.round, self.trades),
            helpers.at("2026-08-01T01:00:00+09:00"),
        )

    def test_到達率は最後の買いから数える(self):
        avg = self.round.avg_cost_jpy
        points = [
            point(30, int(avg * Decimal("1.02"))),  # 最後の買いより前。数えない
            point(120, int(avg * Decimal("1.004"))),
        ]
        table = measure.reach_table(
            [self.round], self.trades, points, [Decimal("0.3"), Decimal("1.0")], [24]
        )
        self.assertEqual(table[0]["total"], 1)
        self.assertEqual(table[0]["hit"][Decimal("0.3")], 1)
        self.assertEqual(table[0]["hit"][Decimal("1.0")], 0)


class SimulateTest(unittest.TestCase):
    def setUp(self) -> None:
        rows = [
            trade_row("buy", "0.010", 10_000_000, "2026-08-01T00:00:00+09:00"),
            trade_row("sell", "0.010", 10_060_000, "2026-08-01T10:00:00+09:00"),
        ]
        self.trades = state_module.parse_trades(rows, "btc_jpy", helpers.TZ)
        self.round = state_module.rounds(self.trades)[0]
        self.avg = self.round.avg_cost_jpy

    def run_with(self, points, tp2):
        return measure.simulate(
            self.round,
            self.trades,
            points,
            Decimal("0.3"),
            Decimal("0.5"),
            tp2,
            3,
            Decimal("0.0012"),
        )

    def test_2段目まで届けば利確で閉じる(self):
        points = [
            point(60, int(self.avg * Decimal("1.004"))),
            point(120, int(self.avg * Decimal("1.007"))),
        ]
        result = self.run_with(points, Decimal("0.6"))
        self.assertEqual(result["exit_kind"], "take_profit")
        self.assertTrue(result["tp1_hit"])
        self.assertGreater(result["pnl_jpy"], 0)

    def test_届かなければ保有上限の成行になる(self):
        """幅を広げると、利確ではなく3日後の成行で出ることになる。"""
        points = [
            point(60, int(self.avg * Decimal("1.004"))),
            point(60 * 71, int(self.avg * Decimal("0.99"))),
        ]
        result = self.run_with(points, Decimal("2.0"))
        self.assertEqual(result["exit_kind"], "time_stop")
        self.assertLess(result["pnl_jpy"], 0)

    def test_成行には手数料がかかる(self):
        """同じ価格で出ても、成行のぶんだけ手取りが減る。"""
        price = int(self.avg * Decimal("1.004"))
        points = [point(60, price), point(60 * 71, price)]
        forced = self.run_with(points, Decimal("2.0"))
        reached = self.run_with(points, Decimal("0.3"))
        self.assertEqual(forced["exit_kind"], "time_stop")
        self.assertEqual(reached["exit_kind"], "take_profit")
        self.assertLess(forced["pnl_jpy"], reached["pnl_jpy"])

    def test_価格の記録が無ければ判定しない(self):
        """止まっていた区間を、都合よく埋めない。"""
        self.assertFalse(self.run_with([], Decimal("0.6"))["decided"])


if __name__ == "__main__":
    unittest.main()
