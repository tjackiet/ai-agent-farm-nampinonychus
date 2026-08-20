"""記録の言語化。売買の判断には関与しない。"""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from nampinonychus import narrate, summary
from tests.helpers import load_config

BODY = f"""# 2026-08-19

- 状態: LADDERING
- 総資産: 1,000,712 JPY (+0.07%)
- 所感: {summary.UNWRITTEN}
"""


def fixed(text: str) -> narrate.Writer:
    def write(system: str, user: str) -> str:
        return text

    return write


class PromptTest(unittest.TestCase):
    def test_数値を作らせない(self):
        prompt = narrate.build_prompt("daily")
        self.assertIn("新しい数値を作らない", prompt)
        self.assertIn("予測を事実として書かない", prompt)
        self.assertIn("損失を言い換えない", prompt)

    def test_学びには追加の制約を付ける(self):
        prompt = narrate.build_prompt("lessons")
        self.assertIn("次の判断を変えうることだけ", prompt)
        self.assertIn("自分では変えない", prompt)

    def test_性格設定を渡す(self):
        self.assertIn("ナンピノニクス", narrate.build_prompt("daily"))


class FillTest(unittest.TestCase):
    def test_空欄を埋める(self):
        text, changed = fill = narrate.fill(BODY, fixed("まだ高いですね。待ちます。"), "p")
        self.assertTrue(changed)
        self.assertIn("- 所感: まだ高いですね。待ちます。", text)
        self.assertNotIn(summary.UNWRITTEN, text)

    def test_空欄が無ければ何もしない(self):
        called = []

        def writer(system, user):
            called.append(1)
            return "書いた"

        text, changed = narrate.fill("# 所感あり\n", writer, "p")
        self.assertFalse(changed)
        self.assertEqual(called, [])

    def test_空の返答なら空欄のまま(self):
        text, changed = narrate.fill(BODY, fixed("   "), "p")
        self.assertFalse(changed)
        self.assertIn(summary.UNWRITTEN, text)

    def test_1行に整える(self):
        text, _ = narrate.fill(BODY, fixed("- 前置き\n  改行あり"), "p")
        self.assertIn("- 所感: 前置き 改行あり", text)


class FillUnwrittenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.daily = self.root / self.config.daily_path.format(date="2026-08-19")
        self.daily.parent.mkdir(parents=True, exist_ok=True)
        self.daily.write_text(BODY, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_日誌の空欄を埋める(self):
        filled = narrate.fill_unwritten(self.config, fixed("待ちます。"), self.root)
        self.assertEqual(filled, [self.daily])
        self.assertIn("待ちます。", self.daily.read_text(encoding="utf-8"))

    def test_二度目は書き換えない(self):
        narrate.fill_unwritten(self.config, fixed("待ちます。"), self.root)
        again = narrate.fill_unwritten(self.config, fixed("別の文"), self.root)
        self.assertEqual(again, [])
        self.assertNotIn("別の文", self.daily.read_text(encoding="utf-8"))

    def test_無効なら何もしない(self):
        config = dataclasses.replace(self.config, narrate_enabled=False)
        self.assertEqual(narrate.fill_unwritten(config, fixed("x"), self.root), [])
        self.assertIn(summary.UNWRITTEN, self.daily.read_text(encoding="utf-8"))

    def test_対象から外せる(self):
        config = dataclasses.replace(self.config, narrate_targets={"daily": False})
        self.assertEqual(narrate.fill_unwritten(config, fixed("x"), self.root), [])


class WriterChoiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_既定はClaudeCode(self):
        self.assertEqual(self.config.narrate_writer, "claude_code")

    def test_apiも選べる(self):
        config = dataclasses.replace(self.config, narrate_writer="api")
        self.assertIsNotNone(narrate.writer_for(config))

    def test_不正な指定は落とす(self):
        config = dataclasses.replace(self.config, narrate_writer="なにか")
        with self.assertRaises(ValueError):
            narrate.writer_for(config)


class ClaudeCodeArgsTest(unittest.TestCase):
    """claude -p の組み立て。課金の向き先が変わるため bare の扱いを固定する。"""

    def setUp(self) -> None:
        self.config = load_config()
        self.calls: list[list[str]] = []

    def run_writer(self, config):
        import subprocess

        real = subprocess.run

        def fake(argv, **kwargs):
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "書いた", "")

        subprocess.run = fake
        try:
            narrate.claude_code_writer(config)("system", "user")
        finally:
            subprocess.run = real
        return self.calls[0]

    def test_既定ではbareを付けない(self):
        """--bare はサブスクリプションを使わなくなるため、既定では付けない。"""
        self.assertFalse(self.config.narrate_bare)
        argv = self.run_writer(self.config)
        self.assertNotIn("--bare", argv)
        self.assertIn("-p", argv)
        self.assertIn("--output-format", argv)

    def test_bareを選べる(self):
        config = dataclasses.replace(self.config, narrate_bare=True)
        self.assertIn("--bare", self.run_writer(config))

    def test_モデルと効力を渡す(self):
        argv = self.run_writer(self.config)
        self.assertIn(self.config.narrate_model, argv)
        self.assertIn(self.config.narrate_effort, argv)


class 失敗の伝えかたTest(unittest.TestCase):
    """所感が空欄のままなら、原因が分かる形で残っていること。

    launchd は自分の PATH しか見ない。claude が見つからないだけで所感は
    ずっと空欄になるが、例外の型名だけでは PATH が原因だと分からない。
    """

    def setUp(self) -> None:
        self.config = load_config()

    def _write(self, fake):
        import subprocess

        real = subprocess.run
        subprocess.run = fake
        try:
            narrate.claude_code_writer(self.config)("system", "user")
        finally:
            subprocess.run = real

    def test_コマンドが無ければ名前とPATHを言う(self):
        def missing(argv, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", argv[0])

        with self.assertRaises(narrate.NarrateError) as caught:
            self._write(missing)
        self.assertIn(self.config.narrate_command, str(caught.exception))
        self.assertIn("PATH", str(caught.exception))

    def test_時間切れならそう言う(self):
        import subprocess

        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, self.config.narrate_timeout_sec)

        with self.assertRaises(narrate.NarrateError) as caught:
            self._write(slow)
        self.assertIn(str(self.config.narrate_timeout_sec), str(caught.exception))

    def test_異常終了なら終了コードを言う(self):
        import subprocess

        def failed(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 3, "", "何か")

        with self.assertRaises(narrate.NarrateError) as caught:
            self._write(failed)
        self.assertIn("3", str(caught.exception))



class CommentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_一言を返す(self):
        self.assertEqual(
            narrate.comment(self.config, fixed("淡々と積んでいます。"), "レポート"),
            "淡々と積んでいます。",
        )

    def test_対象外なら空(self):
        config = dataclasses.replace(self.config, narrate_targets={"report": False})
        self.assertEqual(narrate.comment(config, fixed("x"), "レポート"), "")


if __name__ == "__main__":
    unittest.main()
