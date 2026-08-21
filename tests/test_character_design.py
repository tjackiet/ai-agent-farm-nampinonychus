"""外見設計。根拠のない造形を持ち込まないための検査。

値そのものは人間が判断する。ここで見るのは「項目が揃っているか」
「根拠が書かれているか」だけで、良し悪しは判定しない。
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from nampinonychus.config import REPO_ROOT, load

DESIGN = REPO_ROOT / "character-design.yaml"

# docs/IMPLEMENTATION_PLAN.md「責務の分離／キャラクターデザイン」の9項目。
REQUIRED = (
    "motif",          # 1. 生物モチーフ
    "silhouette",     # 2. 基本シルエット
    "build",          # 3. 体格
    "colors",         # 4. 色
    "markings",       # 5. 模様
    "identity_anchors",  # 6. 固定すべき識別要素
    "evolution",      # 7-8. 進化時に維持する要素 / 変更できる要素
    "expressions",    # 9. normal / down / up の方針
)


class CharacterDesignTest(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = yaml.safe_load(DESIGN.read_text(encoding="utf-8"))

    def test_9項目が揃っている(self):
        for key in REQUIRED:
            with self.subTest(key=key):
                self.assertIn(key, self.doc["design"])

    def test_造形には根拠を添える(self):
        """`basis` の無い造形を許さない。

        「なんとなくかっこいいから」で決めた要素は、進化のときに
        何を残すべきか判断できなくなる。
        """
        for key in ("motif", "silhouette", "build", "colors", "markings"):
            with self.subTest(key=key):
                self.assertTrue(self.doc["design"][key].get("basis"))

    def test_識別要素にも根拠を添える(self):
        anchors = self.doc["design"]["identity_anchors"]
        self.assertTrue(anchors)
        for anchor in anchors:
            with self.subTest(value=anchor.get("value")):
                self.assertTrue(anchor.get("basis"))

    def test_表情は3種類とも方針がある(self):
        for mood in ("normal", "down", "up"):
            with self.subTest(mood=mood):
                self.assertTrue(self.doc["design"]["expressions"][mood]["value"].strip())

    def test_階段縞の本数は段数と一致する(self):
        """模様は戦略と結びついている。段数が変わったら模様も見直す。"""
        config = load()
        self.assertIn(str(config.ladder_max_steps), self.doc["design"]["markings"]["value"])

    def test_採用は人間が決める(self):
        """生成した候補は人間が確認してから反映する（REPOSITORY_PLAN 項目16）。"""
        self.assertIn("adopted", self.doc)
        self.assertIsInstance(self.doc["adopted"], bool)

    def test_画像の指示文がある(self):
        prompt = self.doc["image_prompt"]
        self.assertTrue(prompt["common"].strip())
        for mood in ("normal", "down", "up"):
            with self.subTest(mood=mood):
                self.assertTrue(prompt[mood].strip())
