"""発火頻度の測定ロジック。パラメータを勘で決めないための道具のテスト。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parent.parent / "scripts" / "measure_intraday.py"
    spec = importlib.util.spec_from_file_location("measure_intraday", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure_intraday"] = module
    spec.loader.exec_module(module)
    return module


measure = load_module()


def oscillating(cycles: int = 20, amplitude: float = 0.0025) -> list:
    """1時間かけて下げ、1時間かけて戻す 15分足を作る。"""
    candles = []
    ts = 0
    price = 10_000_000.0
    for _ in range(cycles):
        for direction in (1 - amplitude, 1 + amplitude):
            for _ in range(4):
                price *= direction
                candles.append(
                    measure.Candle(high=price * 1.0005, low=price * 0.9995, close=price, ts=ts)
                )
                ts += 900_000
    return candles


class SimulateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.candles = oscillating()

    def test_浅い下落率はよく発火する(self):
        result = measure.simulate(self.candles, 15, 2, 0.5, 0.5, 30, 24)
        self.assertGreater(result.entries_per_day, 1)
        self.assertEqual(result.exit_rate, 1.0)

    def test_深い下落率は発火しない(self):
        result = measure.simulate(self.candles, 15, 2, 5.0, 0.5, 30, 24)
        self.assertEqual(result.entries, 0)
        self.assertEqual(result.exit_rate, 0.0)

    def test_下落率が深いほど発火は減る(self):
        shallow = measure.simulate(self.candles, 15, 4, 0.3, 0.5, 30, 24).entries
        deep = measure.simulate(self.candles, 15, 4, 1.0, 0.5, 30, 24).entries
        self.assertGreaterEqual(shallow, deep)

    def test_クールダウンは発火を減らす(self):
        few = measure.simulate(self.candles, 15, 2, 0.5, 0.5, 240, 24).entries
        many = measure.simulate(self.candles, 15, 2, 0.5, 0.5, 15, 24).entries
        self.assertLess(few, many)

    def test_届かない利確は到達率0(self):
        result = measure.simulate(self.candles, 15, 2, 0.5, 50.0, 30, 24)
        self.assertEqual(result.exit_rate, 0.0)
        self.assertIsNone(result.median_hours_to_exit)


if __name__ == "__main__":
    unittest.main()
