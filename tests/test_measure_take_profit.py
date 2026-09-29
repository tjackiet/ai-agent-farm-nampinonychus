"""利確幅の測定。数字が戦略の判断材料になるため、計算部分を押さえる。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from nampinonychus import cli as cli_module
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

    def test_利確は指値の価格で約定する(self):
        """15分の間に飛んでも、板の指値はその価格で約定する。

        観測価格で数えると、大きく動いた回だけ不当に儲かったことになり、
        幅を広げるほど得に見える。実績（毎回きっかり +0.45%）と合わなくなる。
        """
        points = [point(60, int(self.avg * Decimal("1.05")))]  # いきなり +5%
        result = self.run_with(points, Decimal("0.6"))
        self.assertEqual(result["exit_kind"], "take_profit")
        # 0.5 を +0.3%、残り 0.5 を +0.6% で売ったぶんだけ
        expected = self.avg * self.round.amount * Decimal("0.0045")
        self.assertAlmostEqual(float(result["pnl_jpy"]), float(expected), places=4)

    def test_価格の記録が無ければ判定しない(self):
        """止まっていた区間を、都合よく埋めない。"""
        self.assertFalse(self.run_with([], Decimal("0.6"))["decided"])


# 2026-08-22〜09-02 の実例。成行の手仕舞いが取引単位へ切り捨てるため、
# 0.0064 のうち 0.0063 しか売れず、0.0001 が残った。
LEFTOVER_ROWS = [
    trade_row("buy", "0.0064", 12_438_813, "2026-08-22T07:31:00+09:00"),
    trade_row("sell", "0.0032", 12_476_129, "2026-08-24T20:36:00+09:00"),
    trade_row("sell", "0.0031", 12_408_000, "2026-09-02T15:19:02+09:00", "market", 38),
    trade_row("buy", "0.0064", 12_478_296, "2026-09-02T15:29:00+09:00"),
    trade_row("sell", "0.0064", 12_407_999, "2026-09-02T15:29:24+09:00", "market", 79),
]


class UnitTest(unittest.TestCase):
    """ラウンドの区切りを本体と揃える。"""

    def setUp(self) -> None:
        self.trades = state_module.parse_trades(LEFTOVER_ROWS, "btc_jpy", helpers.TZ)

    def test_端数しか残らないラウンドは閉じる(self):
        unit, _ = measure.resolve_unit(None, helpers.pair_spec())
        closed = [r for r in state_module.rounds(self.trades, unit) if r.is_closed]
        self.assertEqual(len(closed), 2)
        self.assertEqual(closed[1].opened_at, helpers.at("2026-09-02T15:29:00+09:00"))

    def test_単位が無いと端数で次のラウンドとつながる(self):
        """直す前の測定。8/21〜9/18 が1つのラウンドに見えていた。"""
        closed = [r for r in state_module.rounds(self.trades) if r.is_closed]
        self.assertEqual(closed, [])

    def test_指定した単位を優先する(self):
        self.assertEqual(
            measure.resolve_unit("0.001", helpers.pair_spec()), (Decimal("0.001"), "--unit")
        )

    def test_仕様も指定も無ければ単位は分からない(self):
        self.assertEqual(measure.resolve_unit(None, None), (None, None))


class ReadTradesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = helpers.load_config()
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        path = Path(self.work.name) / "trade-history.json"
        path.write_text(json.dumps({"data": LEFTOVER_ROWS}), encoding="utf-8")
        self.args = argparse.Namespace(trades=str(path))

    def read_with(self, fake: helpers.FakeCli):
        client = cli_module.Client(config=self.cfg, runner=fake)
        return measure.read_trades(self.args, self.cfg, client)

    def test_ファイルから読んでも銘柄の仕様は取る(self):
        """ペーパー口座には触れず、公開の pairs だけを叩く。"""
        fake = helpers.FakeCli({"pairs": [helpers.PAIR_ROW]})
        trades, spec = self.read_with(fake)
        self.assertEqual(len(trades), len(LEFTOVER_ROWS))
        self.assertEqual(spec.unit_amount, Decimal("0.0001"))
        self.assertEqual([helpers.FakeCli.key_of(c.split()) for c in fake.calls], ["pairs"])

    def test_仕様が取れなくても約定履歴は読む(self):
        fake = helpers.FakeCli({}, errors={"pairs": "HTTP 403"})
        trades, spec = self.read_with(fake)
        self.assertEqual(len(trades), len(LEFTOVER_ROWS))
        self.assertIsNone(spec)


if __name__ == "__main__":
    unittest.main()
