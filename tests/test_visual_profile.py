"""表示用プロフィールの技の説明が、`agent.yaml` と食い違っていないこと。

`visual-profile.yaml` は表示のための要約であり、数値の正は `agent.yaml`
にある。要約なので自由に書けるぶん、戦略を変えたときに置き去りになりやすい。

実際に日中足へ切り替えたとき、階段の説明が「アンカーから -3%」のまま
残っていた（正しくは -0.5%）。人が読む説明だけが古い状態は、
食い違いに気づく手がかりが無いぶんたちが悪い。
"""

from __future__ import annotations

import unittest

import yaml

from nampinonychus.config import REPO_ROOT, load

PROFILE = REPO_ROOT / "visual-profile.yaml"


def _forms(value: float) -> set[str]:
    """説明文に現れうる書き方。地の文なので表記は一定ではない。

    階段は「-1.0%」と小数を残し、冬眠は「-15%」と落とす。どちらも
    間違いではないので、どちらかが含まれていればよいことにする。
    """
    return {f"{value:g}", f"{value:.1f}"}


class SkillDescriptionTest(unittest.TestCase):
    def assertNumberIn(self, value: float, text: str) -> None:
        forms = _forms(value)
        if not any(form in text for form in forms):
            self.fail(f"{sorted(forms)} のいずれも説明文に無い: {text}")

    def setUp(self) -> None:
        self.config = load()
        doc = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
        self.skills = {s["name"]: s["description"] for s in doc["skills"]}

    def test_階段の説明が段の数と下げ幅に合っている(self):
        text = self.skills["買い下がりの階段"]
        self.assertIn(f"{self.config.ladder_max_steps}段", text)
        for step in self.config.ladder_steps:
            with self.subTest(step=step.step):
                self.assertNumberIn(step.drop_pct, text)
        total = sum(s.budget_jpy for s in self.config.ladder_steps)
        self.assertIn(f"{int(total):,}", text)

    def test_アンカーの説明が観測範囲に合っている(self):
        text = self.skills["高値は追わない"]
        hours = self.config.anchor_lookback_minutes // 60
        self.assertIn(f"{hours}時間", text)

    def test_利確の説明が段の値に合っている(self):
        text = self.skills["二段階利確"]
        for level in self.config.take_profit:
            with self.subTest(level=level.level):
                self.assertNumberIn(level.gain_pct, text)

    def test_冬眠の説明が閾値に合っている(self):
        text = self.skills["冬眠"]
        self.assertNumberIn(abs(self.config.halt_new_buys_pct), text)
