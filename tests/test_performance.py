"""運用実績の集計。総資産の推移は判断ログと約定履歴から復元する。"""

from __future__ import annotations

import unittest
from decimal import Decimal

from nampinonychus import performance
from nampinonychus.state import parse_trades, rounds
from tests import helpers
from tests.helpers import load_config
from tests.test_summary import record, trade

NOW = helpers.at("2026-08-19T12:00:00+09:00")


def at_record(hhmm: str, price: float, **kwargs):
    row = record("HOLD", price, **kwargs)
    row["run_id"] = f"2026-08-19T{hhmm}:00+09:00"
    return row


class EquityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        # 10:00 に 10,000,000 で 0.01 買う（手数料 0）
        self.trades = parse_trades(
            [trade("buy", "0.01", "10000000", "2026-08-19T01:00:00.000Z")],
            "btc_jpy",
            helpers.TZ,
        )

    def test_約定前は現金だけ(self):
        points = performance.equity_series(
            self.config, [at_record("09", 10_000_000.0)], self.trades, helpers.TZ
        )
        self.assertEqual(points[0].equity_jpy, Decimal(1000000))
        self.assertEqual(points[0].position, Decimal(0))

    def test_約定後は現金と建玉の合計(self):
        """買った直後は総資産が変わらない（現金が建玉に変わっただけ）。"""
        points = performance.equity_series(
            self.config, [at_record("11", 10_000_000.0)], self.trades, helpers.TZ
        )
        self.assertEqual(points[0].cash_jpy, Decimal(900000))
        self.assertEqual(points[0].position, Decimal("0.01"))
        self.assertEqual(points[0].equity_jpy, Decimal(1000000))

    def test_値上がりで総資産が増える(self):
        points = performance.equity_series(
            self.config, [at_record("11", 10_100_000.0)], self.trades, helpers.TZ
        )
        self.assertEqual(points[0].equity_jpy, Decimal(1001000))

    def test_価格が無い記録は飛ばす(self):
        records = [at_record("09", None), at_record("11", 10_000_000.0)]
        self.assertEqual(len(performance.equity_series(self.config, records, self.trades, helpers.TZ)), 1)


class DrawdownTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.trades = parse_trades(
            [trade("buy", "0.1", "10000000", "2026-08-19T00:30:00.000Z")],
            "btc_jpy",
            helpers.TZ,
        )

    def series(self, prices):
        records = [at_record(f"{9 + i:02d}", p) for i, p in enumerate(prices)]
        return performance.equity_series(self.config, records, self.trades, helpers.TZ)

    def test_最大ドローダウンは最も深い下落(self):
        # 建玉 0.1 BTC。価格が 1,000万 → 1,100万 → 990万 と動く
        points = self.series([10_000_000.0, 11_000_000.0, 9_900_000.0])
        # 過去最高 1,100,000 から 990,000 へ → 10.0%
        self.assertAlmostEqual(float(performance.max_drawdown_pct(points)), 10.0, places=2)

    def test_現在のドローダウンは最高からの差(self):
        points = self.series([10_000_000.0, 11_000_000.0, 10_500_000.0])
        self.assertAlmostEqual(
            float(performance.drawdown_from_peak_pct(points)), 4.55, places=2
        )

    def test_最高値のときは0(self):
        points = self.series([10_000_000.0, 11_000_000.0])
        self.assertEqual(performance.drawdown_from_peak_pct(points), Decimal(0))


class BuyAndHoldTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_同じ資金を持ち続けた場合(self):
        points = performance.equity_series(
            self.config,
            [at_record("09", 10_000_000.0), at_record("11", 10_500_000.0)],
            (),
            helpers.TZ,
        )
        hold = performance.buy_and_hold_equity(self.config, points)
        self.assertEqual(hold, Decimal(1050000))

    def test_記録が無ければ出さない(self):
        self.assertIsNone(performance.buy_and_hold_equity(self.config, []))


class StreakTest(unittest.TestCase):
    def build(self, results):
        rows = []
        for index, gain in enumerate(results):
            day = 10 + index
            rows.append(trade("buy", "0.01", "10000000", f"2026-08-{day:02d}T01:00:00.000Z"))
            rows.append(
                trade("sell", "0.01", str(10000000 + gain), f"2026-08-{day:02d}T05:00:00.000Z")
            )
        trades = parse_trades(rows, "btc_jpy", helpers.TZ)
        return [r for r in rounds(trades) if r.is_closed]

    def test_連勝を数える(self):
        self.assertEqual(performance.streaks(self.build([100000, 100000, 100000])), (3, 0))

    def test_連敗を数える(self):
        self.assertEqual(performance.streaks(self.build([-100000, -100000])), (0, 2))

    def test_直近だけを見る(self):
        self.assertEqual(performance.streaks(self.build([-100000, 100000])), (1, 0))

    def test_同時に立たない(self):
        wins, losses = performance.streaks(self.build([100000, -100000]))
        self.assertEqual((wins, losses), (0, 1))


class BuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.trades = parse_trades(
            [
                trade("buy", "0.01", "10000000", "2026-08-19T01:00:00.000Z"),
                trade("sell", "0.01", "10100000", "2026-08-19T02:00:00.000Z"),
            ],
            "btc_jpy",
            helpers.TZ,
        )
        self.records = [at_record("09", 10_000_000.0), at_record("11", 10_100_000.0)]

    def test_サンプルと同じ形式で出す(self):
        document = performance.build(self.config, NOW, self.records, self.trades)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["agent_id"], "nampinonychus")
        self.assertEqual(document["source"], "paper")
        self.assertEqual(document["initial_equity_jpy"], 1000000.0)
        self.assertEqual(document["current_equity_jpy"], 1001000)
        self.assertEqual(document["total_pnl_pct"], 0.1)
        self.assertEqual(document["trades_24h"], 2)
        self.assertEqual(document["consecutive_wins"], 1)
        self.assertEqual(document["consecutive_losses"], 0)

    def test_判定結果は書かない(self):
        """表情は表示側が mood-rules.yaml から毎回算出する。"""
        document = performance.build(self.config, NOW, self.records, self.trades)
        for key in ("mood", "emote", "normal", "down", "up"):
            self.assertNotIn(key, document)


if __name__ == "__main__":
    unittest.main()
