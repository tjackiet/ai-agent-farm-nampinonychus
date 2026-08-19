"""日次サマリと lessons の生成。

決定的に作れる部分だけをここで書く。「所感」と「学び」は空欄として残し、
あとで言語化する（docs/IMPLEMENTATION_PLAN.md Phase 6「LLM の位置づけ」）。

観測していない値は書かない。数値はすべて判断ログと約定履歴に由来する。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from . import journal, performance, timeutil
from .config import Config, REPO_ROOT
from .state import Round, Trade, rounds

# 未記入であることを示す印。あとで言語化するときはこの行を置き換える。
UNWRITTEN = "（未記入）"


def _jpy(value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    return f"{round(float(value)):,}"


def _btc(value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _paths(config: Config, root: Path | None):
    base = root if root is not None else REPO_ROOT
    return base


def daily_path(config: Config, date: str, root: Path | None = None) -> Path:
    return _paths(config, root) / config.daily_path.format(date=date)


def lessons_path(config: Config, root: Path | None = None) -> Path:
    return _paths(config, root) / config.lessons_path


def build_daily(
    config: Config, date: str, records: Sequence[dict], trades: Sequence[Trade]
) -> str:
    """その日の判断ログと約定から、日次サマリの本文を作る。"""
    prices = [r["price"] for r in records if isinstance(r.get("price"), (int, float))]
    actions = [r.get("action") for r in records]
    day_trades = [t for t in trades if timeutil.date_key(t.filled_at) == date]
    last = records[-1] if records else {}
    position = last.get("position") or {}
    amount = position.get("amount")
    avg_cost = position.get("avg_cost")

    lines = [f"# {date}", ""]
    lines.append(f"- 状態: {last.get('state', '—')}")

    if prices:
        lines.append(
            f"- 価格: 高値 {_jpy(max(prices))} / 安値 {_jpy(min(prices))} / 終値 {_jpy(prices[-1])}"
            f"（15分ごとの観測値。足の高安ではない）"
        )
    else:
        lines.append("- 価格: 観測できていない")

    counts = {name: actions.count(name) for name in ("BUY", "SELL", "HOLD")}
    lines.append(
        f"- 判断: BUY {counts['BUY']}件 / SELL {counts['SELL']}件 / HOLD {counts['HOLD']}件"
        f"（計 {len(records)}回）"
    )

    if day_trades:
        lines.append(f"- 約定: {len(day_trades)}件")
        for trade in day_trades:
            side = "買い" if trade.side == "buy" else "売り"
            notional = trade.fill_price * trade.amount
            lines.append(
                f"  - {trade.filled_at.strftime('%H:%M')} {side} "
                f"{_jpy(trade.fill_price)} × {_btc(trade.amount)}"
                f"（{_jpy(notional)} JPY / 手数料 {_jpy(trade.fee_quote)}）"
            )
    else:
        lines.append("- 約定: なし")

    if amount:
        lines.append(f"- 建玉: {_btc(amount)} BTC / 平均取得単価 {_jpy(avg_cost)}")
        if prices and avg_cost:
            unrealized = (Decimal(str(prices[-1])) - Decimal(str(avg_cost))) * Decimal(str(amount))
            basis = Decimal(str(avg_cost)) * Decimal(str(amount))
            pct = unrealized / basis * Decimal(100) if basis else Decimal(0)
            lines.append(f"- 含み損益: {_jpy(unrealized)} JPY ({float(pct):+.2f}%)（終値による評価）")
    else:
        lines.append("- 建玉: なし")

    closed = [r for r in rounds(trades) if r.is_closed and timeutil.date_key(r.closed_at) == date]
    if closed:
        total = sum((r.realized_pnl_jpy for r in closed), Decimal(0))
        lines.append(f"- 決済: {len(closed)}回 / 実現損益 {_jpy(total)} JPY")

    holds = [r.get("reason") for r in records if r.get("action") == "HOLD" and r.get("reason")]
    if holds:
        top = max(set(holds), key=holds.count)
        lines.append(f"- HOLD の主な理由: {top}（{holds.count(top)}回）")

    lines.extend(_evaluation(config, records, trades))
    lines.append(f"- 所感: {UNWRITTEN}")
    lines.append("")
    return "\n".join(lines)


def _evaluation(config: Config, records: Sequence[dict], trades: Sequence[Trade]) -> list[str]:
    """総資産と、同じ資金を持ち続けた場合との比較。

    「勝ったかどうか」は損益の絶対額では決まらない。買って持っていただけの場合と
    比べて初めて、この戦略に意味があったかが分かる。
    """
    points = performance.equity_series(config, records, trades, config.timezone)
    if not points:
        return []

    initial = Decimal(str(config.initial_jpy))
    equity = points[-1].equity_jpy
    strategy_pct = (equity - initial) / initial * Decimal(100)
    lines = [
        f"- 総資産: {_jpy(equity)} JPY ({float(strategy_pct):+.2f}%)",
        f"- 最大ドローダウン: {float(performance.max_drawdown_pct(points)):.2f}%（過去最高資産から）",
    ]

    hold = performance.buy_and_hold_equity(config, points)
    if hold is not None:
        hold_pct = (hold - initial) / initial * Decimal(100)
        diff = strategy_pct - hold_pct
        lines.append(
            f"- Buy&Hold 比較: 戦略 {float(strategy_pct):+.2f}% / "
            f"買って持つだけ {float(hold_pct):+.2f}% → 差 {float(diff):+.2f}%"
            f"（起点 {points[0].at.strftime('%m-%d %H:%M')} の観測価格 {_jpy(points[0].price)}）"
        )
    return lines


def build_lesson(round_: Round) -> str:
    """建玉1回ぶんの記録。学びの欄は空けておく。"""
    opened = round_.opened_at.strftime("%Y-%m-%d")
    closed = round_.closed_at.strftime("%Y-%m-%d") if round_.closed_at else "—"
    held = (
        (round_.closed_at - round_.opened_at).total_seconds() / 3600
        if round_.closed_at
        else 0
    )
    sign = "+" if round_.realized_pnl_jpy >= 0 else ""
    return "\n".join(
        [
            f"## {opened} 〜 {closed} / btc_jpy / "
            f"{sign}{_jpy(round_.realized_pnl_jpy)} JPY ({float(round_.return_pct):+.2f}%)",
            "",
            f"- 使った段: {round_.steps}",
            f"- 平均取得単価: {_jpy(round_.avg_cost_jpy)}",
            f"- 取得原価: {_jpy(round_.cost_jpy)} JPY / 数量 {_btc(round_.amount)} BTC",
            f"- 保有時間: {held:.1f} 時間",
            f"- 学び: {UNWRITTEN}",
            "",
        ]
    )


def _lesson_heading(round_: Round) -> str:
    return build_lesson(round_).splitlines()[0]


def ensure(
    config: Config,
    now: datetime,
    trades: Sequence[Trade],
    root: Path | None = None,
) -> list[Path]:
    """書けるようになった日次サマリと lessons を書き出す。

    既にあるファイルは触らない。あとで書き足した所感や学びを消さないため。
    """
    written: list[Path] = []
    today = timeutil.date_key(now)
    write_at = config.daily_write_at

    for date in journal.recorded_dates(config, root):
        # その日が終わっているか、書き出し時刻を過ぎていれば作る。
        if date == today and now.strftime("%H:%M") < write_at:
            continue
        path = daily_path(config, date, root)
        if path.exists():
            continue
        records = journal.read_day(config, date, root)
        if not records:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_daily(config, date, records, trades), encoding="utf-8")
        written.append(path)

    closed = [r for r in rounds(trades) if r.is_closed]
    if closed:
        path = lessons_path(config, root)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        additions = [
            build_lesson(r) for r in closed if _lesson_heading(r) not in existing
        ]
        if additions:
            path.parent.mkdir(parents=True, exist_ok=True)
            header = "" if existing else "# 学び — ナンピノニクス\n\n建玉が完結したときにだけ書く。日々の値動きへの感想は書かない。\n\n"
            path.write_text(existing + header + "\n".join(additions), encoding="utf-8")
            written.append(path)

    return written
