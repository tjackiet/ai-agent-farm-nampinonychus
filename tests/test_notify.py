"""通知。1方向に送るだけで、判断には影響させない。"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path

from nampinonychus import notify
from tests import helpers
from tests.helpers import load_config

NOW = helpers.at("2026-08-19T09:05:00+09:00")


def fill(side="buy", price=10282738, amount=0.0077):
    return {"side": side, "fillPrice": price, "amount": amount}


def placed(label="step-1", side="buy", price=10282738.0, amount=0.0077, executed=True):
    return {
        "op": "place",
        "label": label,
        "side": side,
        "price": price,
        "amount": amount,
        "executed": executed,
    }


class MessageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.previous = notify.Previous(at=helpers.at("2026-08-19T08:50:00+09:00"), state="IDLE")

    def build(self, **kwargs):
        args = {
            "config": self.config,
            "now": NOW,
            "previous": self.previous,
            "decision_state": "IDLE",
            "fills": [],
            "orders": [],
            "records": [],
        }
        args.update(kwargs)
        return notify.build_messages(**args)

    def test_何もなければ送らない(self):
        self.assertEqual(self.build(), [])

    def test_約定を知らせる(self):
        messages = self.build(fills=[fill()])
        self.assertEqual(messages, ["約定 買い 10,282,738 × 0.0077（79,177 JPY）"])

    def test_発注を知らせる(self):
        messages = self.build(orders=[placed()])
        self.assertIn("発注 step-1 買い 10,282,738 × 0.0077", messages[0])

    def test_dry_runの注文は知らせない(self):
        self.assertEqual(self.build(orders=[placed(executed=False)]), [])

    def test_状態の変化を知らせる(self):
        messages = self.build(decision_state="LADDERING")
        self.assertEqual(messages, ["状態 IDLE → LADDERING"])

    def test_同じ状態なら知らせない(self):
        self.assertEqual(self.build(decision_state="IDLE"), [])

    def test_連続失敗を知らせる(self):
        records = [{"error": "boom"} for _ in range(3)]
        messages = self.build(records=records)
        self.assertIn("3回続けて失敗しています", messages[0])

    def test_失敗が続いていなければ知らせない(self):
        records = [{"error": "boom"}, {"error": None}, {"error": "boom"}]
        self.assertEqual(self.build(records=records), [])

    def test_HOLDそのものは知らせない(self):
        """1日 96 回になるため、判断そのものは送らない。"""
        self.assertEqual(self.build(decision_state="IDLE", fills=[], orders=[]), [])


class ReportTimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def crossed(self, previous, now):
        return notify.crossed_report_times(
            self.config, helpers.at(previous) if previous else None, helpers.at(now)
        )

    def test_時刻をまたいだら送る(self):
        self.assertEqual(
            self.crossed("2026-08-19T08:50:00+09:00", "2026-08-19T09:05:00+09:00"), ["09:00"]
        )

    def test_またいでいなければ送らない(self):
        self.assertEqual(
            self.crossed("2026-08-19T09:05:00+09:00", "2026-08-19T09:20:00+09:00"), []
        )

    def test_寝ていて過ぎてしまっても起きたら送る(self):
        crossed = self.crossed("2026-08-19T07:00:00+09:00", "2026-08-19T12:00:00+09:00")
        self.assertEqual(crossed, ["09:00"])

    def test_前回が分からなければ送らない(self):
        self.assertEqual(self.crossed(None, "2026-08-19T09:05:00+09:00"), [])


class SendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.sent: list[tuple[str, str, int]] = []
        os.environ[self.config.notify_webhook_env] = "https://example.invalid/hook"

    def tearDown(self) -> None:
        os.environ.pop(self.config.notify_webhook_env, None)

    def poster(self, url, content, timeout):
        self.sent.append((url, content, timeout))

    def test_まとめて1通にする(self):
        notify.send(self.config, ["約定 …", "状態 …"], poster=self.poster)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1], "約定 …\n状態 …")

    def test_URLが無ければ送らない(self):
        os.environ.pop(self.config.notify_webhook_env, None)
        failure = notify.send(self.config, ["約定 …"], poster=self.poster)
        self.assertEqual(self.sent, [])
        self.assertIn("設定されていない", failure)

    def test_無効なら送らない(self):
        config = dataclasses.replace(self.config, notify_enabled=False)
        self.assertIsNone(notify.send(config, ["約定 …"], poster=self.poster))
        self.assertEqual(self.sent, [])

    def test_失敗しても例外にしない(self):
        def broken(url, content, timeout):
            raise OSError("接続できません")

        failure = notify.send(self.config, ["約定 …"], poster=broken)
        self.assertIn("通知を送れませんでした", failure)

    def test_失敗の理由にURLを含めない(self):
        def broken(url, content, timeout):
            raise OSError("https://example.invalid/hook へ接続できません")

        failure = notify.send(self.config, ["約定 …"], poster=broken)
        self.assertNotIn("example.invalid", failure)


class PreviousTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "var").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_前回の状態と時刻を読む(self):
        (self.root / self.config.status_output).write_text(
            "updated_at: '2026-08-19T08:50:00+09:00'\nstate: LADDERING\n", encoding="utf-8"
        )
        previous = notify.read_previous(self.config, self.root)
        self.assertEqual(previous.state, "LADDERING")
        self.assertEqual(previous.at, helpers.at("2026-08-19T08:50:00+09:00"))

    def test_無ければ空(self):
        previous = notify.read_previous(self.config, self.root)
        self.assertIsNone(previous.state)
        self.assertIsNone(previous.at)

    def test_壊れていても落ちない(self):
        (self.root / self.config.status_output).write_text("{壊れている", encoding="utf-8")
        self.assertIsNone(notify.read_previous(self.config, self.root).state)


if __name__ == "__main__":
    unittest.main()
