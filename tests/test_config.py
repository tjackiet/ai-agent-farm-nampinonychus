"""agent.yaml の読み込み。値の欠落は黙って補わず、必ず落とす。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from nampinonychus import config as config_module
from nampinonychus.config import ConfigError, load
from tests.helpers import load_config


class LoadTest(unittest.TestCase):
    def write_config(self, overrides: dict) -> str:
        """agent.yaml の一部を差し替えた一時ファイルを作る。"""
        raw = yaml.safe_load(Path("agent.yaml").read_text(encoding="utf-8"))
        for section, values in overrides.items():
            raw[section].update(values)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", encoding="utf-8", delete=False
        ) as handle:
            yaml.safe_dump(raw, handle, allow_unicode=True)
            return handle.name

    def test_正本を読める(self):
        config = load_config()
        self.assertEqual(config.pair, "btc_jpy")
        self.assertEqual(config.ladder_max_steps, len(config.ladder_steps))
        self.assertEqual(config.state_path, "var/paper-state.json")

    def test_階段の合計は総予算と一致する(self):
        config = load_config()
        total = sum(step.budget_jpy for step in config.ladder_steps)
        self.assertEqual(total, config.ladder_total_budget_jpy)

    def test_1回の発注上限は最大の段以上(self):
        config = load_config()
        self.assertGreaterEqual(
            config.per_order_max_jpy, max(step.budget_jpy for step in config.ladder_steps)
        )

    def test_総予算は建玉の上限に収まる(self):
        config = load_config()
        self.assertLessEqual(
            config.ladder_total_budget_jpy, config.initial_jpy * config.max_position_ratio
        )

    def test_戦略を変えると指紋が変わる(self):
        """ペーパーで値を試すと、同じ口座に違う設定の結果が並ぶ。

        どの回がどの設定だったかが判断ログに残らないと、あとの測定が
        設定違いのラウンドを平均した数字を出す。
        """
        import copy

        config = load_config()
        base = config_module.fingerprint(config.raw)

        changed = copy.deepcopy(config.raw)
        changed["strategy"]["exit"]["take_profit"][1]["gain_pct"] = 1.5
        self.assertNotEqual(config_module.fingerprint(changed), base)

        risk = copy.deepcopy(config.raw)
        risk["risk"]["max_position_ratio"] = 0.9
        self.assertNotEqual(config_module.fingerprint(risk), base)

    def test_戦略に関係ない設定では指紋が変わらない(self):
        """通知やモデルを変えただけで、別の戦略として数えない。"""
        import copy

        config = load_config()
        unrelated = copy.deepcopy(config.raw)
        unrelated["notify"]["error_streak"] = 99
        unrelated["narrate"]["effort"] = "low"
        self.assertEqual(
            config_module.fingerprint(unrelated), config_module.fingerprint(config.raw)
        )

    def test_通知の設定を読める(self):
        """見送りの連続と繰り返し間隔は、戦略やリスクの値ではなく通知の設定。"""
        config = load_config()
        self.assertTrue(config.notify_on["veto_streak"])
        self.assertEqual(config.notify_veto_streak, 4)
        self.assertEqual(config.notify_streak_repeat_every, 24)

    def test_通知の値が欠けていれば落とす(self):
        for key in ("veto_streak", "streak_repeat_every"):
            with self.subTest(key=key):
                raw = yaml.safe_load(Path("agent.yaml").read_text(encoding="utf-8"))
                del raw["notify"][key]
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".yaml", encoding="utf-8", delete=False
                ) as handle:
                    yaml.safe_dump(raw, handle, allow_unicode=True)
                    path = handle.name
                with self.assertRaises(ConfigError) as caught:
                    load(path)
                self.assertIn(f"notify.{key}", str(caught.exception))

    def test_禁止コマンドが定義されている(self):
        config = load_config()
        for command in ("bitbank trade create-order", "bitbank trade cancel-order", "bitbank paper reset"):
            self.assertIn(command, config.forbidden)

    def test_言語化のモデルと効力を持つ(self):
        config = load_config()
        self.assertEqual(config.narrate_model, "claude-sonnet-5")
        self.assertEqual(config.narrate_effort, "high")

    def test_Fable系のモデルは受け付けない(self):
        """料金が上位帯のため、事故で選ばれないように弾く。"""
        for name in ("claude-fable-5", "claude-mythos-5", "CLAUDE-FABLE-5"):
            with self.subTest(name=name):
                path = self.write_config({"narrate": {"model": name}})
                with self.assertRaises(ConfigError) as caught:
                    load(path)
                self.assertIn("narrate.model", str(caught.exception))

    def test_他のモデルは受け付ける(self):
        for name in ("claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"):
            with self.subTest(name=name):
                path = self.write_config({"narrate": {"model": name}})
                self.assertEqual(load(path).narrate_model, name)

    def test_値が欠けていれば落とす(self):
        raw = yaml.safe_load(Path("agent.yaml").read_text(encoding="utf-8"))
        del raw["risk"]["max_position_ratio"]
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as handle:
            yaml.safe_dump(raw, handle, allow_unicode=True)
            path = handle.name
        with self.assertRaises(ConfigError) as caught:
            load(path)
        self.assertIn("risk.max_position_ratio", str(caught.exception))

    def test_ファイルが無ければ落とす(self):
        with self.assertRaises(ConfigError):
            load("/nonexistent/agent.yaml")


if __name__ == "__main__":
    unittest.main()
