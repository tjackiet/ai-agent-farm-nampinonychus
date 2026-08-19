"""買いを止める権利だけを LLM に渡す層（段階2）。

見ているのは1点だけ。**この層は止める方向にしか働かないか。**
通す答え・壊れた答え・呼べなかったときのどれでも、買いが増えないことを確かめる。
"""

from __future__ import annotations

import dataclasses
import unittest
from decimal import Decimal

from nampinonychus import veto
from nampinonychus.config import ConfigError, load
from nampinonychus.decide import BUY, HOLD, SELL, Decision
from nampinonychus.orders import PlaceOrder
from tests import helpers
from tests.helpers import load_config

NOW = helpers.at("2026-08-18T09:00:00+09:00")


def buy_decision() -> Decision:
    return Decision(
        action=BUY,
        state="IDLE",
        reason="1 段目の買い指値を置く（基準 anchor = 15000000、-0.5%）",
        place=(
            PlaceOrder(
                side="buy",
                order_type="limit",
                amount=Decimal("0.0053"),
                price=Decimal("14925000"),
                label="step-1",
            ),
        ),
    )


def answering(text: str):
    def write(system: str, user: str) -> str:
        return text

    return write


def failing(exc: Exception):
    def write(system: str, user: str) -> str:
        raise exc

    return write


class ParseTest(unittest.TestCase):
    def test_通す(self):
        self.assertEqual(veto.parse("PROCEED"), (False, ""))

    def test_止める(self):
        stopped, reason = veto.parse("STOP: 直近1時間で3回買っている")
        self.assertTrue(stopped)
        self.assertEqual(reason, "直近1時間で3回買っている")

    def test_全角のコロンも読む(self):
        self.assertEqual(veto.parse("STOP：理由")[1], "理由")

    def test_考えを書いたあとの結論を読む(self):
        """前置きを書いてしまう応答でも、最後の判定を拾う。"""
        stopped, _ = veto.parse("まず状況を整理します。\n段は1段目です。\nPROCEED")
        self.assertFalse(stopped)

    def test_地の文のSTOPは判定にしない(self):
        """行頭でなければ判定として読まない。"""
        self.assertEqual(veto.parse("ここで STOP すべきか迷う\nPROCEED"), (False, ""))

    def test_飾りが付いていても読む(self):
        """書式の揺れだけで失敗扱いにしない。"""
        self.assertEqual(veto.parse("**STOP**: 速すぎる"), (True, "速すぎる"))
        self.assertEqual(veto.parse("- PROCEED"), (False, ""))
        self.assertEqual(veto.parse("`PROCEED`"), (False, ""))

    def test_読めない応答は落とす(self):
        for text in ("", "わかりません", "はい", "STOPPING now"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    veto.parse(text)


class ReviewableTest(unittest.TestCase):
    """諮る対象は買いだけ。売りや取消は諮らない。"""

    def test_買いは諮る(self):
        self.assertTrue(veto.is_reviewable(buy_decision()))

    def test_HOLDは諮らない(self):
        self.assertFalse(veto.is_reviewable(Decision(action=HOLD, state="IDLE", reason="x")))

    def test_利確の売りは諮らない(self):
        """売りを止めるのは建玉を抱え続ける方向であり、安全側ではない。"""
        self.assertFalse(veto.is_reviewable(Decision(action=SELL, state="HOLDING", reason="x")))


class ReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = dataclasses.replace(load_config(), veto_enabled=True)
        self.market = helpers.market()
        self.state = helpers.state()

    def review(self, writer, config=None, decision=None):
        return veto.review(
            config if config is not None else self.config,
            decision if decision is not None else buy_decision(),
            self.market,
            self.state,
            NOW,
            writer,
        )

    def test_無効なら呼ばない(self):
        config = dataclasses.replace(self.config, veto_enabled=False)
        called = []

        def writer(system, user):
            called.append(user)
            return "STOP: 止める"

        result = self.review(writer, config=config)
        self.assertEqual(called, [])
        self.assertFalse(result.consulted)
        self.assertFalse(result.stopped)

    def test_HOLDの回は呼ばない(self):
        called = []

        def writer(system, user):
            called.append(user)
            return "STOP: 止める"

        result = self.review(
            writer, decision=Decision(action=HOLD, state="IDLE", reason="x")
        )
        self.assertEqual(called, [])
        self.assertFalse(result.consulted)

    def test_通せばそのまま(self):
        result = self.review(answering("PROCEED"))
        self.assertTrue(result.consulted)
        self.assertFalse(result.stopped)

    def test_止めれば理由を残す(self):
        result = self.review(answering("STOP: 落ち方が速い"))
        self.assertTrue(result.stopped)
        self.assertEqual(result.reason, "落ち方が速い")

    def test_理由なしで止めても止める(self):
        """止める方向は安全側なので、理由が無くても受け入れる。"""
        result = self.review(answering("STOP"))
        self.assertTrue(result.stopped)
        self.assertEqual(result.reason, "理由が書かれていない")

    def test_失敗時はon_failureに従う_hold(self):
        config = dataclasses.replace(self.config, veto_on_failure="hold")
        result = self.review(failing(RuntimeError("claude が異常終了しました")), config=config)
        self.assertTrue(result.stopped)
        self.assertIn("RuntimeError", result.error)

    def test_失敗時はon_failureに従う_proceed(self):
        config = dataclasses.replace(self.config, veto_on_failure="proceed")
        result = self.review(failing(RuntimeError("落ちた")), config=config)
        self.assertFalse(result.stopped)
        self.assertIsNotNone(result.error)

    def test_読めない応答も失敗として扱う(self):
        result = self.review(answering("たぶん大丈夫です"))
        self.assertTrue(result.stopped)
        self.assertIn("ValueError", result.error)

    def test_観測できていなければ諮らない(self):
        """market や state が無い回は決定的コードが既に HOLD にしている。"""
        result = veto.review(
            self.config, buy_decision(), None, None, NOW, answering("STOP: x")
        )
        self.assertFalse(result.consulted)


class BriefTest(unittest.TestCase):
    """諮るときに渡す事実。ここに無い数値を使わせない。"""

    def setUp(self) -> None:
        self.config = load_config()
        self.brief = veto.build_brief(
            self.config,
            buy_decision(),
            helpers.market(),
            helpers.state(fills_today=2, step=1, avg_cost="14900000", position="0.005"),
            NOW,
            records=[
                {"run_id": "r1", "action": "BUY", "price": 14800000, "reason": "1 段目"},
                {"run_id": "r2", "action": "HOLD", "price": 14750000, "reason": "様子見"},
            ],
        )

    def test_価格とアンカーを渡す(self):
        self.assertIn("14700000", self.brief)
        self.assertIn("15000000", self.brief)

    def test_段と当日の約定回数を渡す(self):
        self.assertIn(f"1 / {self.config.ladder_max_steps}", self.brief)
        self.assertIn(f"2 / {self.config.max_fills_per_day}", self.brief)

    def test_出そうとしている注文を渡す(self):
        self.assertIn("buy limit 0.0053 @ 14925000", self.brief)

    def test_直近の判断を渡す(self):
        self.assertIn("r1 BUY", self.brief)
        self.assertIn("r2 HOLD", self.brief)

    def test_件数を超えて渡さない(self):
        config = dataclasses.replace(self.config, veto_read_last_n=1)
        brief = veto.build_brief(
            config,
            buy_decision(),
            helpers.market(),
            helpers.state(),
            NOW,
            records=[
                {"run_id": "r1", "action": "BUY", "price": 1, "reason": "古い"},
                {"run_id": "r2", "action": "BUY", "price": 2, "reason": "新しい"},
            ],
        )
        self.assertNotIn("r1 BUY", brief)
        self.assertIn("r2 BUY", brief)


class ApplyTest(unittest.TestCase):
    def test_通した回は何も変えない(self):
        decision = buy_decision()
        self.assertIs(veto.apply(decision, veto.SKIPPED), decision)
        self.assertIs(
            veto.apply(decision, veto.Veto(consulted=True, stopped=False)), decision
        )

    def test_止めた回は発注も取消も残さない(self):
        """板の状態を変えないまま次の回へ持ち越す。"""
        decision = dataclasses.replace(buy_decision(), cancel=("old-order",))
        applied = veto.apply(
            decision, veto.Veto(consulted=True, stopped=True, reason="落ち方が速い")
        )
        self.assertEqual(applied.action, HOLD)
        self.assertEqual(applied.place, ())
        self.assertEqual(applied.cancel, ())
        self.assertIn("落ち方が速い", applied.reason)

    def test_状態名は変えない(self):
        applied = veto.apply(buy_decision(), veto.Veto(consulted=True, stopped=True))
        self.assertEqual(applied.state, "IDLE")

    def test_呼べなかったことは理由に書く(self):
        applied = veto.apply(
            buy_decision(), veto.Veto(consulted=True, stopped=True, error="Timeout: 120s")
        )
        self.assertIn("諮れなかった", applied.reason)
        self.assertIn("Timeout", applied.reason)


class ConfigTest(unittest.TestCase):
    def test_既定は無効(self):
        """本番稼働中の挙動を勝手に変えない。有効にするのは人間が決める。"""
        self.assertFalse(load_config().veto_enabled)

    def test_既定の失敗時はHOLD(self):
        self.assertEqual(load_config().veto_on_failure, "hold")

    def test_on_failureの値を検証する(self):
        raw = dict(load_config().raw)
        raw["veto"] = dict(raw["veto"], on_failure="なにか")
        with self.assertRaises(ConfigError):
            _load_from(raw)

    def test_Fable系のモデルは弾く(self):
        raw = dict(load_config().raw)
        raw["veto"] = dict(raw["veto"], model="claude-fable-5")
        with self.assertRaises(ConfigError):
            _load_from(raw)


def _load_from(raw: dict):
    import tempfile
    from pathlib import Path

    import yaml

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "agent.yaml"
        path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return load(path)


if __name__ == "__main__":
    unittest.main()
