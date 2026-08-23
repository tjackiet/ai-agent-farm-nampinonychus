"""日次サマリと lessons。決定的に作れる部分だけを書き、所感は空欄で残す。"""

from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nampinonychus import journal, summary
from nampinonychus.state import parse_trades, rounds
from tests import helpers
from tests.helpers import load_config

NOW = helpers.at("2026-08-19T23:55:00+09:00")


def trade(side, amount, price, filled_at, fee="0"):
    return {
        "id": f"{side}-{filled_at}",
        "pair": "btc_jpy",
        "side": side,
        "type": "limit",
        "amount": float(amount),
        "fillPrice": float(price),
        "feeQuote": float(fee),
        "filledAt": filled_at,
    }


def record(action, price, reason="", state="IDLE", amount=None, avg_cost=None, at="2026-08-19T10:00:00+09:00"):
    return {
        "run_id": at,
        "state": state,
        "pair": "btc_jpy",
        "price": price,
        "anchor": 10334411.0,
        "position": {"amount": amount, "avg_cost": avg_cost},
        "action": action,
        "reason": reason,
        "orders": [],
        "sources": [],
    }


class 観測が途切れた日のサマリTest(unittest.TestCase):
    """実行が止まっている間に決済が済んだ日。

    launchd は Mac がスリープしている間は動かない。判断ログはその時点で
    止まるが、`bitbank paper tick` は起床後に遡って約定を確定させるため、
    約定履歴にだけ売却が現れる。判断ログの最終レコードを信じると、
    売り切った日が建玉を抱えたまま負けている日に見える。
    """

    def setUp(self) -> None:
        self.config = load_config()
        self.trades = parse_trades(
            [
                trade("buy", "0.0077", "10282738", "2026-08-19T01:06:00.000Z"),
                trade("sell", "0.0038", "10313586", "2026-08-19T13:32:00.000Z"),
                trade("sell", "0.0039", "10344434", "2026-08-19T14:07:00.000Z"),
            ],
            "btc_jpy",
            helpers.TZ,
        )
        # 最後の観測は 20:12。2本の売りはそれより後に約定している。
        self.records = [
            record("BUY", 10297382.0, "1 段目", at="2026-08-19T09:17:00+09:00"),
            record(
                "HOLD",
                10266925.0,
                "3 段目の基準となる約定がまだない",
                state="LADDERING",
                amount=0.0077,
                avg_cost=10282738.0,
                at="2026-08-19T20:12:00+09:00",
            ),
        ]

    def body(self):
        return summary.build_daily(self.config, "2026-08-19", self.records, self.trades)

    def test_売り切った日を建玉なしと書く(self):
        self.assertIn("- 建玉: なし", self.body())

    def test_含み損益を書かない(self):
        self.assertNotIn("含み損益", self.body())

    def test_総資産に決済を反映する(self):
        # 79,177 で買い、79,535 で売った。初期資金 1,000,000 に +358。
        self.assertIn("- 総資産: 1,000,358 JPY (+0.04%)", self.body())

    def test_観測より後の約定があったことを書く(self):
        self.assertIn("- 注記: 最終観測より後に 2件の約定があった", self.body())

    def test_最終観測の時刻を書く(self):
        self.assertIn("（最終観測 20:12）", self.body())

    def test_決済を数える(self):
        self.assertIn("- 決済: 1回 / 実現損益 358 JPY", self.body())



class DailyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.trades = parse_trades(
            [trade("buy", "0.0077", "10282738", "2026-08-19T01:06:00.000Z")],
            "btc_jpy",
            helpers.TZ,
        )
        self.records = [
            record("BUY", 10297382.0, "1 段目"),
            record("HOLD", 10334411.0, "クールダウン中", state="LADDERING"),
            record("HOLD", 10250000.0, "クールダウン中", state="LADDERING"),
            record("SELL", 10277964.0, "置き直す", state="LADDERING", amount=0.0077, avg_cost=10282738.0),
        ]

    def body(self):
        return summary.build_daily(self.config, "2026-08-19", self.records, self.trades)

    def test_判断の内訳を数える(self):
        self.assertIn("BUY 1件 / SELL 1件 / HOLD 2件（計 4回）", self.body())

    def test_価格は観測値であることを明記する(self):
        text = self.body()
        self.assertIn("高値 10,334,411", text)
        self.assertIn("安値 10,250,000", text)
        self.assertIn("最終観測 10,277,964", text)
        self.assertIn("足の高安ではない", text)

    def test_約定を時刻つきで並べる(self):
        self.assertIn("10:06 買い 10,282,738 × 0.0077", self.body())

    def test_建玉と含み損益を書く(self):
        text = self.body()
        self.assertIn("平均取得単価 10,282,738", text)
        self.assertIn("含み損益: -37 JPY (-0.05%)", text)

    def test_HOLDの主な理由を書く(self):
        self.assertIn("HOLD の主な理由: クールダウン中（2回）", self.body())

    def test_所感は空欄で残す(self):
        self.assertIn(f"- 所感: {summary.UNWRITTEN}", self.body())

    def test_価格が観測できていなければそう書く(self):
        text = summary.build_daily(self.config, "2026-08-19", [record("HOLD", None)], ())
        self.assertIn("価格: 観測できていない", text)


class LessonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        rows = [
            trade("buy", "0.0077", "10000000", "2026-08-18T01:00:00.000Z"),
            trade("buy", "0.0100", "9900000", "2026-08-18T03:00:00.000Z"),
            trade("sell", "0.0177", "10100000", "2026-08-18T09:00:00.000Z"),
        ]
        self.trades = parse_trades(rows, "btc_jpy", helpers.TZ)

    def test_完結したラウンドを1件にまとめる(self):
        closed = [r for r in rounds(self.trades) if r.is_closed]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].steps, 2)
        self.assertGreater(closed[0].realized_pnl_jpy, 0)

    def test_学びの欄は空けておく(self):
        closed = [r for r in rounds(self.trades) if r.is_closed][0]
        text = summary.build_lesson(closed)
        self.assertIn("使った段: 2", text)
        self.assertIn("保有時間: 8.0 時間", text)
        self.assertIn(f"- 学び: {summary.UNWRITTEN}", text)

    def test_未完結のラウンドは書かない(self):
        rows = [trade("buy", "0.0077", "10000000", "2026-08-19T01:00:00.000Z")]
        trades = parse_trades(rows, "btc_jpy", helpers.TZ)
        self.assertEqual([r for r in rounds(trades) if r.is_closed], [])


class EnsureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.trades = parse_trades(
            [
                trade("buy", "0.0077", "10000000", "2026-08-18T01:00:00.000Z"),
                trade("sell", "0.0077", "10100000", "2026-08-18T09:00:00.000Z"),
            ],
            "btc_jpy",
            helpers.TZ,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_journal(self, date: str, records) -> None:
        path = self.root / self.config.decisions_path.format(date=date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8",
        )

    def test_終わった日のサマリを作る(self):
        self.write_journal("2026-08-18", [record("BUY", 10000000.0)])
        written = summary.ensure(self.config, NOW, self.trades, self.root)
        daily = summary.daily_path(self.config, "2026-08-18", self.root)
        self.assertTrue(daily.is_file())
        self.assertIn(daily, written)

    def test_書き出し時刻より前の当日は作らない(self):
        self.write_journal("2026-08-19", [record("BUY", 10000000.0)])
        early = helpers.at("2026-08-19T10:00:00+09:00")
        summary.ensure(self.config, early, self.trades, self.root)
        self.assertFalse(summary.daily_path(self.config, "2026-08-19", self.root).is_file())

    def test_既にあるサマリは上書きしない(self):
        self.write_journal("2026-08-18", [record("BUY", 10000000.0)])
        daily = summary.daily_path(self.config, "2026-08-18", self.root)
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_text("# 手で書いた所感\n", encoding="utf-8")
        summary.ensure(self.config, NOW, self.trades, self.root)
        self.assertEqual(daily.read_text(encoding="utf-8"), "# 手で書いた所感\n")

    def test_lessonsは重複して書かない(self):
        self.write_journal("2026-08-18", [record("BUY", 10000000.0)])
        summary.ensure(self.config, NOW, self.trades, self.root)
        summary.ensure(self.config, NOW, self.trades, self.root)
        text = summary.lessons_path(self.config, self.root).read_text(encoding="utf-8")
        self.assertEqual(text.count("使った段:"), 1)

    def test_判断ログが無ければ何も書かない(self):
        self.assertEqual(summary.ensure(self.config, NOW, (), self.root), [])


if __name__ == "__main__":
    unittest.main()
