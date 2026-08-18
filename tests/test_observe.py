"""アンカーの算出。日中足は UTC 日ごとに区切られるため、取得の仕方も見る。"""

from __future__ import annotations

import unittest
from decimal import Decimal

from nampinonychus import cli, observe
from tests import helpers
from tests.helpers import FakeCli, default_responses, load_config

NOW = helpers.at("2026-08-18T09:00:00+09:00")


class AnchorTest(unittest.TestCase):
    def test_直近の高値をとる(self):
        candles = helpers.intraday_candles([10_000_000, 10_100_000, 10_050_000] * 4)
        self.assertEqual(
            observe.anchor_price(candles, 120, 15, NOW), Decimal("10100000")
        )

    def test_窓の外の高値は無視する(self):
        """2時間より前の高値をアンカーにしない。"""
        highs = [99_000_000] + [10_000_000] * 11
        candles = helpers.intraday_candles(highs)
        self.assertEqual(
            observe.anchor_price(candles, 120, 15, NOW), Decimal("10000000")
        )

    def test_進行中の足も含める(self):
        highs = [10_000_000] * 11 + [10_500_000]
        candles = helpers.intraday_candles(highs)
        self.assertEqual(
            observe.anchor_price(candles, 120, 15, NOW), Decimal("10500000")
        )

    def test_足が足りなければ判断しない(self):
        """欠損したデータで狭い高値を掴まない。"""
        candles = helpers.intraday_candles([10_000_000, 10_010_000])
        with self.assertRaises(ValueError):
            observe.anchor_price(candles, 120, 15, NOW)

    def test_空なら判断しない(self):
        with self.assertRaises(ValueError):
            observe.anchor_price([], 120, 15, NOW)


class IntervalTest(unittest.TestCase):
    def test_足の分数(self):
        self.assertEqual(observe.interval_minutes("15min"), 15)
        self.assertEqual(observe.interval_minutes("1day"), 1440)

    def test_扱えない足は落とす(self):
        with self.assertRaises(ValueError):
            observe.interval_minutes("3min")


class FetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_日中足は前日から取る(self):
        """UTC の日境界をまたいでも lookback を満たせるようにする。"""
        fake = FakeCli(default_responses())
        client = cli.Client(self.config, runner=fake)
        observe.fetch_anchor_candles(client, self.config, NOW)
        called = [c for c in fake.calls if " candles " in c][0]
        # NOW は UTC では 2026-08-18T00:00。前日 08-17 から取る。
        self.assertIn("--from=20260817", called)
        self.assertIn("--to=20260818", called)
        self.assertIn("--type=15min", called)

    def test_日足は期間を指定しない(self):
        import dataclasses

        config = dataclasses.replace(self.config, anchor_candle_type="1day")
        fake = FakeCli(default_responses())
        client = cli.Client(config, runner=fake)
        observe.fetch_anchor_candles(client, config, NOW)
        called = [c for c in fake.calls if " candles " in c][0]
        self.assertNotIn("--from=", called)


if __name__ == "__main__":
    unittest.main()
