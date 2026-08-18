"""CLI ラッパ。応答の解釈と、禁止コマンドの拒否。"""

from __future__ import annotations

import json
import subprocess
import unittest

from nampinonychus.cli import Client, CliError, ForbiddenCommand, is_already_filled
from tests.helpers import load_config


def runner_returning(stdout: str, returncode: int = 0, stderr: str = ""):
    def run(argv, env, timeout):
        return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)

    return run


class ClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_エンベロープのdataを返す(self):
        client = Client(self.config, runner=runner_returning(json.dumps({"success": True, "data": {"mode": "NONE"}})))
        self.assertEqual(client.circuit_break("btc_jpy").data, {"mode": "NONE"})

    def test_出典と取得時刻を残す(self):
        client = Client(self.config, runner=runner_returning(json.dumps({"success": True, "data": []})))
        client.status()
        self.assertEqual(client.sources[0].cmd, "bitbank status --format=json --machine")
        self.assertTrue(client.sources[0].at)

    def test_成功でなければ例外(self):
        client = Client(self.config, runner=runner_returning(json.dumps({"success": False, "error": "boom", "exitCode": 1})))
        with self.assertRaises(CliError):
            client.status()

    def test_未初期化を見分ける(self):
        body = json.dumps({"success": False, "error": "paper state not initialized. Run ...", "exitCode": 1})
        client = Client(self.config, runner=runner_returning(body))
        with self.assertRaises(CliError) as caught:
            client.paper_assets()
        self.assertTrue(caught.exception.not_initialized)

    def test_JSONでなければ例外(self):
        client = Client(self.config, runner=runner_returning("not json"))
        with self.assertRaises(CliError):
            client.status()

    def test_応答が空でも例外(self):
        client = Client(self.config, runner=runner_returning(""))
        with self.assertRaises(CliError):
            client.status()

    def test_stderrの警告を集める(self):
        client = Client(
            self.config,
            runner=runner_returning(
                json.dumps({"success": True, "data": {}}), stderr="Warning: gap > 24h ...\n"
            ),
        )
        client.paper_tick()
        self.assertEqual(client.warnings, ["Warning: gap > 24h ..."])

    def test_tickにpairを付けない(self):
        calls: list[list[str]] = []

        def run(argv, env, timeout):
            calls.append(list(argv))
            return subprocess.CompletedProcess(list(argv), 0, json.dumps({"success": True, "data": {}}), "")

        Client(self.config, runner=run).paper_tick()
        self.assertNotIn("--pair=btc_jpy", calls[0])

    def test_取消はidだけを渡す(self):
        calls: list[list[str]] = []

        def run(argv, env, timeout):
            calls.append(list(argv))
            return subprocess.CompletedProcess(list(argv), 0, json.dumps({"success": True, "data": {}}), "")

        Client(self.config, runner=run).paper_cancel_order("abc")
        self.assertIn("--id=abc", calls[0])
        self.assertNotIn("--order-id=abc", calls[0])

    def test_状態ファイルを環境変数で渡す(self):
        seen: dict[str, str] = {}

        def run(argv, env, timeout):
            seen.update(env)
            return subprocess.CompletedProcess(list(argv), 0, json.dumps({"success": True, "data": []}), "")

        Client(self.config, runner=run).status()
        self.assertTrue(seen["BITBANK_PAPER_STATE_PATH"].endswith("var/paper-state.json"))


class ForbiddenTest(unittest.TestCase):
    """実資金・実績破壊のコマンドは組み立てない（CLAUDE.md）。"""

    def setUp(self) -> None:
        self.config = load_config()
        self.client = Client(self.config, runner=runner_returning(json.dumps({"success": True, "data": {}})))

    def test_実資金の発注を拒否する(self):
        with self.assertRaises(ForbiddenCommand):
            self.client.call("trade", "create-order", "--pair=btc_jpy")

    def test_実資金の取消を拒否する(self):
        with self.assertRaises(ForbiddenCommand):
            self.client.call("trade", "cancel-order", "--pair=btc_jpy")

    def test_実績の破壊を拒否する(self):
        with self.assertRaises(ForbiddenCommand):
            self.client.call("paper", "reset", "--confirm")

    def test_ペーパーの発注は拒否しない(self):
        self.client.call("paper", "create-order", "--pair=btc_jpy")


class AlreadyFilledTest(unittest.TestCase):
    def test_約定済みの取消を見分ける(self):
        error = CliError("open order not found: abc (may have already filled)", cmd="x")
        self.assertTrue(is_already_filled(error))

    def test_ほかのエラーは約定ではない(self):
        self.assertFalse(is_already_filled(CliError("insufficient jpy", cmd="x")))


if __name__ == "__main__":
    unittest.main()
