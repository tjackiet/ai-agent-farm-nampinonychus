"""判断ロジック。本番では滅多に起きない条件も、ここでは必ず踏む。"""

from __future__ import annotations

import dataclasses
import unittest
from decimal import ROUND_FLOOR, Decimal

from nampinonychus.decide import BUY, HOLD, SELL, decide, desired_sell_orders, state_name
from nampinonychus.state import derive as derive_state
from tests import helpers
from tests.helpers import (
    guards,
    load_config,
    market,
    open_order,
    pair_spec,
    state,
    step_price,
    take_profit_orders,
)

NOW = helpers.at("2026-08-18T09:00:00+09:00")


class GuardTest(unittest.TestCase):
    """判断できないときは HOLD（risk-policy.md）。"""

    def setUp(self) -> None:
        self.config = load_config()

    def decide(self, **kwargs):
        args = {
            "guards": guards(),
            "market": market(),
            "spec": pair_spec(),
            "state": state(),
        }
        args.update(kwargs)
        return decide(self.config, args["guards"], args["market"], args["spec"], args["state"], NOW)

    def test_取引所がメンテナンス中なら判断しない(self):
        decision = self.decide(guards=guards(exchange="HALT"))
        self.assertEqual(decision.action, HOLD)
        self.assertIn("HALT", decision.reason)

    def test_未知の稼働状態でも判断しない(self):
        self.assertEqual(self.decide(guards=guards(exchange="UNKNOWN")).action, HOLD)

    def test_サーキットブレイク中は判断しない(self):
        decision = self.decide(guards=guards(circuit="CIRCUIT_BREAK"))
        self.assertEqual(decision.action, HOLD)
        self.assertIn("サーキットブレイク", decision.reason)

    def test_古いデータでは判断しない(self):
        decision = self.decide(market=market(age_sec=600))
        self.assertEqual(decision.action, HOLD)
        self.assertIn("古い", decision.reason)

    def test_取引できないペアでは判断しない(self):
        self.assertEqual(self.decide(spec=pair_spec(is_enabled=False)).action, HOLD)

    def test_建玉と残高が食い違えば人間を呼ぶ(self):
        decision = self.decide(state=state(mismatch=True))
        self.assertEqual(decision.action, HOLD)
        self.assertIn("人間", decision.reason)

    def test_口座が未初期化なら何もしない(self):
        decision = decide(self.config, guards(), market(), pair_spec(), None, NOW)
        self.assertEqual(decision.action, HOLD)
        self.assertEqual(decision.state, "NOT_INITIALIZED")


class BuyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def decide(self, current):
        return decide(self.config, guards(), market(), pair_spec(), current, NOW)

    def test_1段目はアンカーから1段目の下落率ぶん下に置く(self):
        decision = self.decide(state())
        self.assertEqual(decision.action, BUY)
        self.assertEqual(decision.state, "IDLE")
        order = decision.place[0]
        self.assertEqual(order.price, step_price("15000000"))
        # 数量は 1段目の予算から決まる。予算を変えるたびに書き換えないよう計算する。
        budget = Decimal(str(self.config.ladder_steps[0].budget_jpy))
        unit = pair_spec().unit_amount
        expected = (budget / order.price / unit).to_integral_value(
            rounding=ROUND_FLOOR
        ) * unit
        self.assertEqual(order.amount, expected)
        self.assertEqual(order.label, "step-1")

    def test_アンカー以上では追わない(self):
        decision = decide(
            self.config, guards(), market(last="15000000"), pair_spec(), state(), NOW
        )
        self.assertEqual(decision.action, HOLD)
        self.assertIn("追わない", decision.reason)

    def test_未約定の段があるうちは次の段を出さない(self):
        """2段目の基準は直前の約定価格なので、1段目が約定するまで価格が決まらない。"""
        # アンカーどおりの価格に置いてある（置き直しは起きない）状態にする。
        current = state(
            pending_buy=[open_order("o1", "buy", str(step_price("15000000")), "0.0053")]
        )
        decision = self.decide(current)
        self.assertEqual(decision.action, HOLD)
        self.assertIn("基準となる約定がまだない", decision.reason)

    def test_クールダウン中は発注しない(self):
        current = state(
            position="0.0054",
            avg_cost="14550000",
            step=1,
            last_fill_price="14550000",
            last_fill_at="2026-08-18T06:00:00+09:00",
            cooldown_until="2026-08-18T12:00:00+09:00",
            pending_sell=take_profit_orders("0.0054", "14550000"),
        )
        decision = self.decide(current)
        self.assertEqual(decision.action, HOLD)
        self.assertIn("クールダウン", decision.reason)

    def test_当日の約定上限に達したら発注しない(self):
        current = state(fills_today=16, cooldown_until="2026-08-18T08:00:00+09:00")
        decision = self.decide(current)
        self.assertEqual(decision.action, HOLD)
        self.assertIn("本日すでに", decision.reason)

    def test_階段を使い切ったら発注しない(self):
        current = state(
            position="0.0455",
            avg_cost="13175000",
            step=load_config().ladder_max_steps,
            used_budget=str(int(load_config().ladder_total_budget_jpy)),
            last_fill_price="12099221",
            cash="400000",
            pending_sell=take_profit_orders("0.0455", "13175000"),
        )
        decision = self.decide(current)
        self.assertEqual(decision.action, HOLD)
        self.assertEqual(decision.state, "HOLDING")
        self.assertIn("使い切っている", decision.reason)

    def _laddering(self, cash_available: str, pending_buy=()):
        """1段目が約定し、板に買い指値がない状態。次に出せるのは2段目。"""
        return state(
            position="0.0054",
            avg_cost="14550000",
            step=1,
            used_budget="78570",
            last_fill_price="14550000",
            last_fill_at="2026-08-17T00:00:00+09:00",
            cooldown_until="2026-08-17T06:00:00+09:00",
            cash="921430",
            cash_available=cash_available,
            pending_buy=pending_buy,
            pending_sell=take_profit_orders("0.0054", "14550000"),
        )

    def _cash_floor(self) -> Decimal:
        """常に維持する現金。手をつけられない額（risk-policy.md）。"""
        return Decimal(str(self.config.initial_jpy)) * Decimal(
            str(self.config.min_cash_reserve_ratio)
        )

    def test_現金の下限を割る買いはしない(self):
        """常に維持する現金は使わない。使える額が 0 なら発注しない。"""
        decision = self.decide(self._laddering(str(self._cash_floor())))
        self.assertEqual(decision.action, HOLD)
        self.assertIn("予算が残っていない", decision.reason)

    def test_残り予算に合わせて数量を減らす(self):
        usable = Decimal("60000")  # 段の予算より小さい額しか使えない状態にする
        decision = self.decide(self._laddering(str(self._cash_floor() + usable)))
        self.assertEqual(decision.action, BUY)
        self.assertEqual(decision.place[0].label, "step-2")
        order = decision.place[0]
        self.assertEqual(order.price, step_price("14550000", 2))
        unit = pair_spec().unit_amount
        self.assertEqual(
            order.amount,
            (usable / order.price / unit).to_integral_value(rounding=ROUND_FLOOR) * unit,
        )

    def test_直前の段が約定するまで次の段を出さない(self):
        """古い約定を基準にすると、段の間隔が設計より詰まる。"""
        current = self._laddering(
            "921430", pending_buy=[open_order("b2", "buy", "14477250", "0.0069")]
        )
        decision = self.decide(current)
        self.assertEqual(decision.action, HOLD)
        self.assertIn("3 段目の基準となる約定がまだない", decision.reason)

    def test_買い指値が2本あれば余分を取り消す(self):
        """段の基準にできない注文が残っている状態を自分で直す。"""
        current = self._laddering(
            "800000",
            pending_buy=[
                open_order("b2", "buy", "14477250", "0.0069"),
                open_order("b3", "buy", "14448150", "0.0083"),
            ],
        )
        decision = self.decide(current)
        self.assertEqual(decision.action, HOLD)
        self.assertEqual(decision.cancel, ("b3",))
        self.assertIn("14477250 の1本を残して", decision.reason)

    def test_建玉が上限に達したら買わない(self):
        """取得原価の合計は `max_position_ratio` を超えない。"""
        # 総予算を使い切り、残る現金は下限ぴったり。建玉はそのぶんの取得原価を持つ。
        total = Decimal(str(self.config.ladder_total_budget_jpy))
        avg = Decimal("13186813")
        unit = pair_spec().unit_amount
        position = (total / avg / unit).to_integral_value(rounding=ROUND_FLOOR) * unit
        current = state(
            position=str(position),
            avg_cost=str(avg),
            step=4,
            used_budget=str(int(total)),
            last_fill_price="12871512",
            last_fill_at="2026-08-17T00:00:00+09:00",
            cooldown_until="2026-08-17T06:00:00+09:00",
            cash=str(int(Decimal(str(self.config.initial_jpy)) - total)),
            pending_sell=take_profit_orders(str(position), str(avg)),
        )
        decision = self.decide(current)
        self.assertEqual(decision.action, HOLD)
        self.assertIn("予算が残っていない", decision.reason)

    def test_2段目は直前の約定価格を基準にする(self):
        current = state(
            position="0.0054",
            avg_cost="14550000",
            step=1,
            used_budget="78570",
            last_fill_price="14550000",
            last_fill_at="2026-08-17T00:00:00+09:00",
            cooldown_until="2026-08-17T06:00:00+09:00",
            cash="921430",
            pending_sell=take_profit_orders("0.0054", "14550000"),
        )
        decision = self.decide(current)
        self.assertEqual(decision.action, BUY)
        self.assertEqual(decision.state, "LADDERING")
        self.assertEqual(decision.place[0].price, step_price("14550000", 2))
        self.assertEqual(decision.place[0].label, "step-2")


class RepriceTest(unittest.TestCase):
    """1段目の買い指値をアンカーへ追従させる。"""

    def setUp(self) -> None:
        self.config = load_config()

    def decide(self, current, anchor="15000000", last="14700000"):
        return decide(
            self.config, guards(), market(last=last, anchor=anchor), pair_spec(), current, NOW
        )

    def test_アンカーが動いたら置き直す(self):
        current = state(pending_buy=[open_order("o1", "buy", "14700000", "0.0054")])
        decision = self.decide(current)
        self.assertEqual(decision.action, BUY)
        self.assertEqual(decision.cancel, ("o1",))
        self.assertEqual(decision.place[0].price, step_price("15000000"))
        self.assertEqual(decision.place[0].label, "step-1")
        self.assertIn("置き直す", decision.reason)

    def test_ずれが小さければ触らない(self):
        """しきい値（0.1%）未満のずれでは、無駄な取消をしない。"""
        near = step_price("15000000") - Decimal("5000")  # 0.04% ほどのずれ
        current = state(pending_buy=[open_order("o1", "buy", str(near), "0.0053")])
        decision = self.decide(current)
        self.assertEqual(decision.action, HOLD)
        self.assertEqual(decision.cancel, ())

    def test_アンカーが下がっても置き直す(self):
        """上に取り残されると、ルールより高い位置で買うことになる。"""
        current = state(
            pending_buy=[open_order("o1", "buy", str(step_price("15000000")), "0.0053")]
        )
        decision = self.decide(current, anchor="14000000", last="13900000")
        self.assertEqual(decision.action, BUY)
        self.assertEqual(decision.cancel, ("o1",))
        self.assertEqual(decision.place[0].price, step_price("14000000"))

    def test_2段目以降は動かさない(self):
        """直前の約定価格が基準なので、アンカーが動いても関係ない。"""
        current = state(
            position="0.0054",
            avg_cost="14550000",
            step=1,
            used_budget="78570",
            last_fill_price="14550000",
            last_fill_at="2026-08-17T00:00:00+09:00",
            cooldown_until="2026-08-17T06:00:00+09:00",
            cash="921430",
            pending_buy=[open_order("b2", "buy", "14477250", "0.0069")],
            pending_sell=take_profit_orders("0.0054", "14550000"),
        )
        decision = self.decide(current)
        self.assertEqual(decision.cancel, ())
        self.assertNotIn("置き直す", decision.reason)

    def test_予算が足りなければ取り消さない(self):
        """置き直せないなら、いまの注文を残す。取り消しただけで終わらせない。"""
        # 現金の下限に阻まれる状態。取り消しで戻るぶんを足しても予算が出ない。
        floor_cash = Decimal(str(self.config.initial_jpy)) * Decimal(
            str(self.config.min_cash_reserve_ratio)
        )
        current = state(
            cash="1000000",
            # 取り消しで戻る 79,380 JPY を足しても、下限まで 620 JPY 足りない。
            cash_available=str(floor_cash - Decimal("80000")),
            pending_buy=[open_order("o1", "buy", "14700000", "0.0054")],
        )
        decision = self.decide(current)
        self.assertEqual(decision.cancel, ())

    def test_置き直した数量は予算に収まる(self):
        current = state(pending_buy=[open_order("o1", "buy", "14700000", "0.0054")])
        order = self.decide(current).place[0]
        self.assertLessEqual(
            order.price * order.amount,
            Decimal(str(self.config.ladder_steps[0].budget_jpy)),
        )


class SellTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_建玉ができたら利確の指値を2本置く(self):
        """価格と数量は agent.yaml の利確段から計算して確かめる。

        数字を直接書くと、利確幅を変えるたびに落ちる。ここで見たいのは
        「設定どおりの2本が置かれること」であって、特定の価格ではない。
        """
        avg, position = Decimal("14550000"), Decimal("0.0055")
        current = state(position=str(position), avg_cost=str(avg), step=1, cash="920000")
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.action, SELL)

        first, second = self.config.take_profit
        unit = pair_spec().unit_amount
        first_amount = (position * Decimal(str(first.sell_ratio)) / unit).to_integral_value(
            rounding=ROUND_FLOOR
        ) * unit
        expected = [
            (
                (avg * (Decimal(1) + Decimal(str(first.gain_pct)) / 100)).to_integral_value(
                    rounding=ROUND_FLOOR
                ),
                first_amount,
                "tp-1",
            ),
            (
                (avg * (Decimal(1) + Decimal(str(second.gain_pct)) / 100)).to_integral_value(
                    rounding=ROUND_FLOOR
                ),
                position - first_amount,
                "tp-2",
            ),
        ]
        self.assertEqual([(o.price, o.amount, o.label) for o in decision.place], expected)

    def test_置き直すときは既存を取り消す(self):
        current = state(
            position="0.0055",
            avg_cost="14550000",
            step=1,
            cash="920000",
            pending_sell=[open_order("old", "sell", "14000000", "0.0055")],
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.cancel, ("old",))

    def test_一致していれば触らない(self):
        current = state(
            position="0.0055",
            avg_cost="14550000",
            step=1,
            cash="920000",
            cooldown_until="2026-08-18T23:00:00+09:00",
            pending_sell=take_profit_orders("0.0055", "14550000"),
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.action, HOLD)
        self.assertEqual(decision.place, ())

    def test_1段目が約定済みなら残り全量を2段目の価格で置く(self):
        current = state(
            position="0.0028",
            avg_cost="14550000",
            step=1,
            sold_in_round="0.0027",
            cash="960000",
        )
        orders = desired_sell_orders(self.config, pair_spec(), current)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].label, "tp-2")
        self.assertEqual(orders[0].amount, Decimal("0.0028"))


class RiskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_冬眠中は新規買いを止める(self):
        current = state(
            position="0.0055",
            avg_cost="14550000",
            step=1,
            cash="760000",
            equity="840000",
            pending_sell=take_profit_orders("0.0055", "14550000"),
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.state, "HIBERNATING")
        self.assertEqual(decision.action, HOLD)
        self.assertIn("新規買いを停止", decision.reason)

    def test_冬眠中は板に残った買い指値を取り消す(self):
        """放置すると約定して建玉が増え、「新規買いを全面停止」が守れない。"""
        current = state(
            position="0.0055",
            avg_cost="14550000",
            step=1,
            cash="760000",
            equity="840000",
            pending_buy=[open_order("b1", "buy", "14477250", "0.0069")],
            pending_sell=take_profit_orders("0.0055", "14550000"),
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.state, "HIBERNATING")
        self.assertEqual(decision.action, HOLD)
        self.assertEqual(decision.cancel, ("b1",))
        self.assertEqual(decision.place, ())

    def test_冬眠中でも利確の売り指値は維持する(self):
        current = state(
            position="0.0055", avg_cost="14550000", step=1, cash="760000", equity="840000"
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.state, "HIBERNATING")
        self.assertEqual(decision.action, SELL)
        self.assertEqual(len(decision.place), 2)

    def test_総資産が25パーセント減ったら全量を成行で手仕舞う(self):
        current = state(
            position="0.0055",
            avg_cost="14550000",
            step=1,
            cash="660000",
            equity="740000",
            pending_buy=[open_order("b1", "buy", "14113500", "0.0070")],
            pending_sell=[open_order("s1", "sell", "14593650", "0.0027")],
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.state, "HALTED")
        self.assertEqual(decision.action, SELL)
        self.assertEqual(set(decision.cancel), {"b1", "s1"})
        self.assertEqual(decision.place[0].order_type, "market")
        self.assertEqual(decision.place[0].amount, Decimal("0.0055"))

    def test_保有上限を超えたら手仕舞う(self):
        current = state(
            position="0.0055",
            avg_cost="14550000",
            step=1,
            cash="920000",
            age_days=4.0,
            opened_at="2026-08-14T09:00:00+09:00",
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.action, SELL)
        self.assertEqual(decision.place[0].order_type, "market")
        self.assertIn("上限を超えた", decision.reason)


class RecoveryTest(unittest.TestCase):
    """24時間を超えて止まったあとの復帰（risk-policy.md）。"""

    def setUp(self) -> None:
        self.config = load_config()

    def decide(self, current, stopped_hours):
        return decide(
            self.config, guards(), market(), pair_spec(), current, NOW, stopped_hours
        )

    def test_長く止まっていたら指値を全部取り消す(self):
        current = state(
            position="0.0054",
            avg_cost="14550000",
            step=1,
            cash="921430",
            pending_buy=[open_order("b1", "buy", "14477250", "0.0069")],
            pending_sell=[open_order("s1", "sell", "14593650", "0.0027")],
        )
        decision = self.decide(current, 30.0)
        self.assertEqual(decision.action, HOLD)
        self.assertEqual(set(decision.cancel), {"b1", "s1"})
        self.assertEqual(decision.place, ())
        self.assertIn("30.0 時間停止", decision.reason)

    def test_取り消す注文がなくても何もしない(self):
        decision = self.decide(state(), 30.0)
        self.assertEqual(decision.action, HOLD)
        self.assertEqual(decision.cancel, ())
        self.assertIn("取り消す注文はなし", decision.reason)

    def test_上限内の停止では通常どおり判断する(self):
        decision = self.decide(state(), 5.0)
        self.assertEqual(decision.action, BUY)

    def test_停止時間が分からなければ通常どおり判断する(self):
        decision = self.decide(state(), None)
        self.assertEqual(decision.action, BUY)

    def test_強制手仕舞いのほうが優先される(self):
        """撤退の水準に達していれば、復帰より手仕舞いが先。"""
        current = state(
            position="0.0055",
            avg_cost="14550000",
            step=1,
            cash="660000",
            equity="740000",
        )
        decision = self.decide(current, 30.0)
        self.assertEqual(decision.state, "HALTED")
        self.assertEqual(decision.action, SELL)


class 時間切れの手仕舞いTest(unittest.TestCase):
    """成行の手仕舞いが残した端数で、時間切れが繰り返し発火しないこと。

    2026-09-02 15:19 に保有上限を超えた建玉を成行で手仕舞ったところ、
    `_exit_all` の切り捨てで 0.0001 が売れ残った。端数を建玉として数え続けた
    ため `opened_at` が 2026-08-21 のまま固定され、新しく買った建玉が
    その回のうちに時間切れで投げられた（09-02〜09-04 に22往復、
    taker 手数料を含めて約 -6,100 JPY）。
    """

    UNIT = Decimal("0.0001")
    EXIT_HISTORY = [
        {
            "id": "buy-1",
            "pair": "btc_jpy",
            "side": "buy",
            "type": "limit",
            "amount": 0.0032,
            "fillPrice": 12438813,
            "feeQuote": 0,
            "filledAt": "2026-08-21T00:00:00.000Z",
        },
        {
            "id": "exit-1",
            "pair": "btc_jpy",
            "side": "sell",
            "type": "market",
            "amount": 0.0031,
            "fillPrice": 12415315,
            "feeQuote": 46.2,
            "filledAt": "2026-09-02T06:19:00.000Z",
        },
    ]
    NEW_BUY = {
        "id": "buy-2",
        "pair": "btc_jpy",
        "side": "buy",
        "type": "limit",
        "amount": 0.0032,
        "fillPrice": 12400000,
        "feeQuote": 0,
        "filledAt": "2026-09-04T00:00:00.000Z",
    }

    def setUp(self) -> None:
        self.config = load_config()

    def derive(self, now, jpy, btc, position, history):
        return derive_state(
            config=self.config,
            now=now,
            last_price=Decimal("12415315"),
            assets_rows=[
                {"asset": "jpy", "total": jpy, "locked": 0, "available": jpy},
                {"asset": "btc", "total": btc, "locked": 0, "available": btc},
            ],
            pnl_report={
                "perPair": {
                    "btc_jpy": {
                        "pair": "btc_jpy",
                        "position": position,
                        "avgCost": 12400000,
                        "currentPrice": 12415315,
                        "realizedPnl": -6100,
                        "unrealizedPnl": 0,
                        "totalPnl": -6100,
                    }
                }
            },
            order_rows=[],
            history_rows=history,
            unit_amount=self.UNIT,
        )

    def test_端数だけ残った回はIDLEから始まる(self):
        now = helpers.at("2026-09-04T09:10:00+09:00")
        current = self.derive(
            now=now,
            jpy=960000,
            btc=0.0000999999999999967,
            position=0.0001,
            history=self.EXIT_HISTORY,
        )
        self.assertEqual(current.position.amount, Decimal(0))
        self.assertIsNone(current.position.age_days)

        decision = decide(self.config, guards(), market(), pair_spec(), current, now)
        self.assertEqual(decision.state, "IDLE")
        self.assertEqual(decision.action, BUY)
        self.assertEqual(decision.place[0].label, "step-1")

    def test_買った直後の回で時間切れが発火しない(self):
        now = helpers.at("2026-09-04T09:10:00+09:00")
        current = self.derive(
            now=now,
            jpy=920000,
            btc=0.0032999999999999967,
            position=0.0033,
            history=[*self.EXIT_HISTORY, self.NEW_BUY],
        )
        self.assertEqual(
            current.position.opened_at, helpers.at("2026-09-04T09:00:00+09:00")
        )
        self.assertLess(current.position.age_days, self.config.time_stop_days)

        decision = decide(self.config, guards(), market(), pair_spec(), current, now)
        self.assertEqual(decision.state, "LADDERING")
        self.assertNotIn("上限を超えた", decision.reason)
        self.assertTrue(all(o.order_type == "limit" for o in decision.place))


class 利確でロックされた建玉Test(unittest.TestCase):
    """利確の売り指値が建玉の全量を押さえていても、建玉として扱うこと。

    2026-09-14 21:06 に 0.0091 を買い、tp-1 0.0027 と tp-2 0.0064 を置いた。
    `available` が 0 になったのを建玉ゼロと読み、21:55 に売りを2本抱えたまま
    `LADDERING → IDLE` として1段目の買いを新規に出した。さらに
    `max_position_ratio` の判定に取得原価が載らず、ロックされている量が
    変わるたびに tp-2 の数量が 0.0091 / 0.0064 と振れて取消と再発注を
    繰り返した（22:41〜23:11）。
    """

    UNIT = Decimal("0.0001")
    FILLED_AT = "2026-09-14T12:06:00.000Z"  # 21:06 JST

    def setUp(self) -> None:
        self.config = load_config()

    def sells(self, position: str, avg_cost: str) -> list[dict]:
        """いまの設定どおりの利確売り指値を、`paper active-orders` の形で。"""
        return [
            {
                "id": o.id,
                "pair": "btc_jpy",
                "side": "sell",
                "type": "limit",
                "price": float(o.price),
                "amount": float(o.amount),
                "createdAt": self.FILLED_AT,
            }
            for o in take_profit_orders(position, avg_cost)
        ]

    def derive(self, now, *, jpy, position, avg_cost, available, order_rows, config=None):
        return derive_state(
            config=config or self.config,
            now=now,
            last_price=Decimal("12050000"),
            assets_rows=[
                {"asset": "jpy", "total": jpy, "locked": 0, "available": jpy},
                {
                    "asset": "btc",
                    "total": float(position),
                    "locked": float(Decimal(position) - Decimal(available)),
                    "available": float(available),
                },
            ],
            pnl_report={
                "perPair": {
                    "btc_jpy": {
                        "pair": "btc_jpy",
                        "position": float(position),
                        "avgCost": float(avg_cost),
                        "currentPrice": 12050000,
                        "realizedPnl": 0,
                        "unrealizedPnl": 0,
                        "totalPnl": 0,
                    }
                }
            },
            order_rows=order_rows,
            history_rows=[
                {
                    "id": "buy-1",
                    "pair": "btc_jpy",
                    "side": "buy",
                    "type": "limit",
                    "amount": float(position),
                    "fillPrice": float(avg_cost),
                    "feeQuote": 0,
                    "filledAt": self.FILLED_AT,
                }
            ],
            unit_amount=self.UNIT,
        )

    def test_全量ロック中でもIDLEに戻らず1段目を出さない(self):
        """21:55 の回。クールダウンは明けているので、出すなら2段目。"""
        now = helpers.at("2026-09-14T21:55:00+09:00")
        current = self.derive(
            now,
            jpy=890350,
            position="0.0091",
            avg_cost="12049450",
            available="0",
            order_rows=self.sells("0.0091", "12049450"),
        )
        self.assertEqual(current.position.amount, Decimal("0.0091"))
        self.assertEqual(state_name(self.config, current), "LADDERING")

        decision = decide(self.config, guards(), market(), pair_spec(), current, now)
        self.assertEqual(decision.state, "LADDERING")
        self.assertEqual(decision.cancel, ())
        self.assertNotIn("step-1", [o.label for o in decision.place])
        self.assertEqual(decision.action, BUY)
        self.assertEqual(decision.place[0].label, "step-2")

    def test_ロック中でも取得原価が建玉上限の判定に載る(self):
        """`max_position_ratio` は `cost_basis_jpy` を見る。ロック中に 0 になると
        現金比率だけが歯止めになる。階段の総予算を外し、建玉上限だけが効く
        条件で、残り枠ぶんだけの数量になることを確かめる。
        """
        config = dataclasses.replace(self.config, ladder_total_budget_jpy=2_000_000)
        now = helpers.at("2026-09-14T21:55:00+09:00")
        limit = Decimal(str(config.initial_jpy)) * Decimal(str(config.max_position_ratio))
        # 建玉上限まで残り 20,000 JPY。段の予算より小さいので、効くのは上限のほう。
        avg = Decimal("8800000")
        position = ((limit - Decimal("20000")) / avg / self.UNIT).to_integral_value(
            rounding=ROUND_FLOOR
        ) * self.UNIT
        current = self.derive(
            now,
            jpy=300000,
            position=str(position),
            avg_cost=str(avg),
            available="0",
            order_rows=self.sells(str(position), str(avg)),
            config=config,
        )
        self.assertEqual(current.position.cost_basis_jpy, position * avg)

        decision = decide(config, guards(), market(), pair_spec(), current, now)
        self.assertEqual(decision.action, BUY)
        self.assertEqual(decision.place[0].label, "step-2")
        order = decision.place[0]
        remaining = limit - current.position.cost_basis_jpy
        self.assertLess(remaining, Decimal(str(config.ladder_steps[1].budget_jpy)))
        self.assertLessEqual(order.price * order.amount, remaining)
        self.assertGreater(order.price * (order.amount + self.UNIT), remaining)

    def test_ロック状況が変わっても利確の売り指値は変わらない(self):
        """22:41〜23:11 の往復。板の売りを全部取り消してから出し直すので、
        ロックされている量が違っても望む指値は同じで、置き直しは起きない。
        """
        now = helpers.at("2026-09-14T22:41:00+09:00")
        wanted = self.sells("0.0091", "12049450")
        both = tuple(Decimal(str(o["amount"])) for o in wanted)
        cases = {
            "全量ロック": ("0", wanted),
            "tp-2だけ": (str(both[0]), wanted[1:]),
            "ロックなし": ("0.0091", []),
        }
        desired_by_case = {}
        for label, (available, orders) in cases.items():
            current = self.derive(
                now,
                jpy=890350,
                position="0.0091",
                avg_cost="12049450",
                available=available,
                order_rows=orders,
            )
            desired_by_case[label] = desired_sell_orders(self.config, pair_spec(), current)
        self.assertEqual(len(set(desired_by_case.values())), 1, desired_by_case)
        self.assertEqual(
            sum((o.amount for o in desired_by_case["全量ロック"]), Decimal(0)),
            Decimal("0.0091"),
        )

        # 板が望みどおりなら、取消も再発注もしない。
        current = self.derive(
            now,
            jpy=890350,
            position="0.0091",
            avg_cost="12049450",
            available="0",
            order_rows=wanted,
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, now)
        self.assertEqual(decision.cancel, ())
        self.assertFalse(any(o.side == "sell" for o in decision.place))

    def test_取り消す売り指値の解放ぶんを含めて発注数量を決める(self):
        """時間切れの手仕舞い。売りを全部取り消してから、全量を成行で売る。"""
        current = state(
            position="0.0091",
            avg_cost="12049450",
            step=1,
            cash="890350",
            age_days=3.5,
            base_available="0",
            pending_sell=take_profit_orders("0.0091", "12049450"),
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.action, SELL)
        self.assertEqual(set(decision.cancel), {"s1", "s2"})
        self.assertEqual(decision.place[0].order_type, "market")
        self.assertEqual(decision.place[0].amount, Decimal("0.0091"))

    def test_解放ぶんが無ければロックされていない量までしか売らない(self):
        """取り消す注文がないのに `available` を超える売りは、CLI に弾かれる。"""
        current = state(
            position="0.0091",
            avg_cost="12049450",
            step=1,
            cash="890350",
            age_days=3.5,
            base_available="0.0027",
        )
        decision = decide(self.config, guards(), market(), pair_spec(), current, NOW)
        self.assertEqual(decision.action, SELL)
        self.assertEqual(decision.place[0].amount, Decimal("0.0027"))


class StateNameTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_建玉なしはIDLE(self):
        self.assertEqual(state_name(self.config, state()), "IDLE")

    def test_建玉ありはLADDERING(self):
        self.assertEqual(state_name(self.config, state(position="0.0054", step=1)), "LADDERING")

    def test_未初期化(self):
        self.assertEqual(state_name(self.config, None), "NOT_INITIALIZED")


if __name__ == "__main__":
    unittest.main()
