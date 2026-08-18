"""数量と価格の丸め。CLI は丸めてくれないため、ここが誤ると発注が弾かれる。"""

from __future__ import annotations

import unittest
from decimal import Decimal

from nampinonychus.orders import (
    buy_amount,
    floor_price,
    floor_to_unit,
    format_number,
    sell_amount,
    to_decimal,
)
from tests.helpers import pair_spec


class FloorTest(unittest.TestCase):
    def test_数量は刻みへ切り捨てる(self):
        self.assertEqual(floor_to_unit(Decimal("0.0054987"), Decimal("0.0001")), Decimal("0.0054"))

    def test_刻みちょうどはそのまま(self):
        self.assertEqual(floor_to_unit(Decimal("0.0055"), Decimal("0.0001")), Decimal("0.0055"))

    def test_価格は桁数へ切り捨てる(self):
        self.assertEqual(floor_price(Decimal("14550000.9"), 0), Decimal("14550000"))
        self.assertEqual(floor_price(Decimal("123.4567"), 2), Decimal("123.45"))

    def test_指数表記にしない(self):
        self.assertEqual(format_number(Decimal("0.00010000")), "0.0001")


class BuyAmountTest(unittest.TestCase):
    def test_予算から数量を求める(self):
        amount = buy_amount(Decimal(80000), Decimal(14550000), pair_spec())
        self.assertEqual(amount, Decimal("0.0054"))

    def test_手数料ぶんを差し引く(self):
        """maker が正のときは、ロック額が予算を超えないように数量を減らす。"""
        spec = pair_spec(maker_fee_rate_quote=Decimal("0.0012"))
        price = Decimal(14550000)
        amount = buy_amount(Decimal(80000), price, spec)
        locked = price * amount * (Decimal(1) + spec.maker_fee_rate_quote)
        self.assertLessEqual(locked, Decimal(80000))

    def test_リベートは0として扱う(self):
        """maker が負でも数量を増やさない（CLI 側のフォールバックで弾かれないため）。"""
        rebate = buy_amount(Decimal(80000), Decimal(14550000), pair_spec(maker_fee_rate_quote=Decimal("-0.0002")))
        flat = buy_amount(Decimal(80000), Decimal(14550000), pair_spec(maker_fee_rate_quote=Decimal(0)))
        self.assertEqual(rebate, flat)

    def test_刻みに満たなければ0(self):
        self.assertEqual(buy_amount(Decimal(100), Decimal(14550000), pair_spec()), Decimal(0))

    def test_上限を超えない(self):
        spec = pair_spec(limit_max_amount=Decimal("0.001"))
        self.assertEqual(buy_amount(Decimal(80000), Decimal(1000000), spec), Decimal("0.001"))

    def test_予算が0以下なら発注しない(self):
        self.assertEqual(buy_amount(Decimal(0), Decimal(14550000), pair_spec()), Decimal(0))
        self.assertEqual(buy_amount(Decimal(-1), Decimal(14550000), pair_spec()), Decimal(0))


class SellAmountTest(unittest.TestCase):
    def test_比率から数量を求める(self):
        self.assertEqual(sell_amount(Decimal("0.0055"), Decimal("0.5"), pair_spec()), Decimal("0.0027"))

    def test_刻みに満たなければ0(self):
        self.assertEqual(sell_amount(Decimal("0.00005"), Decimal("0.5"), pair_spec()), Decimal(0))


class ToDecimalTest(unittest.TestCase):
    def test_floatの誤差を持ち込まない(self):
        self.assertEqual(to_decimal(0.1), Decimal("0.1"))

    def test_真偽値は受け付けない(self):
        with self.assertRaises(TypeError):
            to_decimal(True)


if __name__ == "__main__":
    unittest.main()
