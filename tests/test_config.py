"""agent.yaml の読み込み。値の欠落は黙って補わず、必ず落とす。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from nampinonychus.config import ConfigError, load
from tests.helpers import load_config


class LoadTest(unittest.TestCase):
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

    def test_禁止コマンドが定義されている(self):
        config = load_config()
        for command in ("bitbank trade create-order", "bitbank trade cancel-order", "bitbank paper reset"):
            self.assertIn(command, config.forbidden)

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
