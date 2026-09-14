"""通知。1方向に送るだけで、判断には影響させない。"""

from __future__ import annotations

import dataclasses
import json
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


def stopped(error=None, reason=""):
    """拒否権で見送った回の判断ログ。`error` があれば諮れていない。"""
    return {
        "error": None,
        "veto": {"consulted": True, "stopped": True, "reason": reason, "error": error},
    }


def proceeded():
    """諮って通した回。"""
    return {
        "error": None,
        "veto": {"consulted": True, "stopped": False, "reason": "", "error": None},
    }


def not_consulted():
    """諮らなかった回。買いを出す回だけ諮るため、ほとんどの回はこれ。"""
    return {"error": None, "veto": None}


UNREACHABLE = "NarrateError: claude が異常終了しました（終了コード 1）"


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


class VetoStreakTest(unittest.TestCase):
    """買いが止まり続けていることを知らせる。

    2026-09-04 から 09-13 まで、`claude` の OAuth 期限切れで拒否権を諮れず、
    買いが9日間すべて HOLD になっていた。判断ログの `error` は null のままで
    （理由は `veto.error` にしか入らない）、`error_streak` には乗らないため
    通知が一度も出なかった。誰も気づけなかった。
    """

    def setUp(self) -> None:
        self.config = load_config()
        self.previous = notify.Previous(
            at=helpers.at("2026-08-19T08:50:00+09:00"), state="IDLE"
        )
        self.threshold = self.config.notify_veto_streak

    def build(self, records):
        return notify.build_messages(
            config=self.config,
            now=NOW,
            previous=self.previous,
            decision_state="IDLE",
            fills=[],
            orders=[],
            records=records,
        )

    def test_諮れなかった回が続いたら知らせる(self):
        records = [stopped(error=UNREACHABLE) for _ in range(self.threshold)]
        messages = self.build(records)
        self.assertEqual(len(messages), 1)
        self.assertIn(f"{self.threshold}回続けて", messages[0])
        self.assertIn("諮れず", messages[0])
        self.assertIn("claude が異常終了しました", messages[0])

    def test_閾値に届かなければ知らせない(self):
        records = [stopped(error=UNREACHABLE) for _ in range(self.threshold - 1)]
        self.assertEqual(self.build(records), [])

    def test_通した回が挟まると数え直す(self):
        records = [stopped(error=UNREACHABLE) for _ in range(self.threshold)]
        records.append(proceeded())
        records += [stopped(error=UNREACHABLE) for _ in range(self.threshold - 1)]
        self.assertEqual(self.build(records), [])

    def test_数え直したあとに閾値へ達したら知らせる(self):
        records = [stopped(error=UNREACHABLE) for _ in range(10)]
        records.append(proceeded())
        records += [stopped(error=UNREACHABLE) for _ in range(self.threshold)]
        messages = self.build(records)
        self.assertEqual(len(messages), 1)
        self.assertIn(f"{self.threshold}回続けて", messages[0])

    def test_諮っていない回は数え直さない(self):
        """諮るのは買いを出す回だけ。間に挟まる HOLD で切れては続いたと分からない。"""
        records = []
        for _ in range(self.threshold):
            records += [not_consulted(), not_consulted(), stopped(error=UNREACHABLE)]
        records.append(not_consulted())
        messages = self.build(records)
        self.assertEqual(len(messages), 1)
        self.assertIn(f"{self.threshold}回続けて", messages[0])

    def test_意図した見送りと諮れなかった失敗で文面が変わる(self):
        intended = self.build(
            [stopped(reason="下落が浅い") for _ in range(self.threshold)]
        )
        unreachable = self.build(
            [stopped(error=UNREACHABLE) for _ in range(self.threshold)]
        )
        self.assertNotEqual(intended, unreachable)
        self.assertIn("LLM の判断で", intended[0])
        self.assertIn("下落が浅い", intended[0])
        self.assertNotIn("諮れず", intended[0])
        self.assertIn("諮れず", unreachable[0])
        self.assertNotIn("LLM の判断で", unreachable[0])

    def test_閾値を超え続けている間は毎回は送らない(self):
        """15分ごとの運用で毎回送ると、11 日続いた凍結でおよそ 1,000 通になる。"""
        every = self.config.notify_streak_repeat_every
        sent = []
        for count in range(self.threshold, self.threshold + every * 2 + 1):
            records = [stopped(error=UNREACHABLE) for _ in range(count)]
            if self.build(records):
                sent.append(count)
        self.assertEqual(
            sent,
            [self.threshold, self.threshold + every, self.threshold + every * 2],
        )

    def test_観測の失敗とは別に数える(self):
        """`record["error"]` に veto の失敗を混ぜない。意味が違う。"""
        records = [stopped(error=UNREACHABLE) for _ in range(self.threshold)]
        self.assertEqual(notify.error_streak(records), 0)
        failures = [{"error": "boom", "veto": None} for _ in range(self.threshold)]
        self.assertEqual(notify.veto_streak(failures).count, 0)

    def test_無効なら知らせない(self):
        config = dataclasses.replace(
            self.config, notify_on={**self.config.notify_on, "veto_streak": False}
        )
        records = [stopped(error=UNREACHABLE) for _ in range(self.threshold)]
        messages = notify.build_messages(
            config=config,
            now=NOW,
            previous=self.previous,
            decision_state="IDLE",
            fills=[],
            orders=[],
            records=records,
        )
        self.assertEqual(messages, [])


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

    def test_状態コードを理由に残す(self):
        """404（Webhook が無い）と 403（弾かれた）を記録から区別できること。

        型名だけでは原因が追えない。状態コードは秘密ではない。
        """
        import urllib.error

        def rejected(url, content, timeout):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

        failure = notify.send(self.config, ["約定 …"], poster=rejected)
        self.assertIn("HTTP 403", failure)
        self.assertNotIn("example.invalid", failure)

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


class PostTest(unittest.TestCase):
    """Discord へ送る HTTP リクエストの形。

    名乗らずに送ると、Discord の前段にいる Cloudflare が
    `403 / error code 1010`（署名による拒否）を返す。urllib の既定の
    User-Agent が `Python-urllib/3.x` で、これが弾かれる。本番で実際に起きた。
    """

    def test_名乗ってから送る(self):
        import urllib.request

        captured = {}

        class FakeResponse:
            def close(self):
                pass

        real = urllib.request.urlopen

        def fake(request, timeout=None):
            captured["headers"] = dict(request.header_items())
            captured["body"] = request.data
            return FakeResponse()

        urllib.request.urlopen = fake
        try:
            notify._post("https://example.invalid/hook", "こんにちは", 10)
        finally:
            urllib.request.urlopen = real

        agent = captured["headers"].get("User-agent", "")
        self.assertTrue(agent)
        self.assertNotIn("Python-urllib", agent)
        self.assertIn("content", json.loads(captured["body"].decode("utf-8")))
