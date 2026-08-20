"""運用実績の集計。

総資産の推移は、判断ログ（各回の観測価格）と約定履歴から復元する。
どちらも観測済みの記録なので、推測値を持ち込まずに過去へ遡って計算できる。

出力は `records/performance.sample.yaml` と同じ形式にする。
表示側は `mood-rules.yaml` でこの実績から表情を決める（判定結果は保存しない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import yaml

from . import journal, timeutil
from .config import Config, REPO_ROOT
from .state import Round, Trade, rounds


@dataclass(frozen=True)
class Point:
    """ある時点の総資産。"""

    at: datetime
    price: Decimal
    cash_jpy: Decimal
    position: Decimal

    @property
    def equity_jpy(self) -> Decimal:
        return self.cash_jpy + self.position * self.price


def equity_series(
    config: Config, records: Sequence[dict], trades: Sequence[Trade], tz_name: str
) -> list[Point]:
    """判断ログの各時点における総資産を復元する。

    現金＝初期資金 − 買いの支払（手数料込み）＋ 売りの受取（手数料差引後）。
    `bitbank paper` の残高の動きと同じ勘定にする。
    """
    initial = Decimal(str(config.initial_jpy))
    points: list[Point] = []
    index = 0
    cash = initial
    position = Decimal(0)
    ordered = sorted(trades, key=lambda t: t.filled_at)

    for record in records:
        price = record.get("price")
        run_id = record.get("run_id")
        if not isinstance(price, (int, float)) or not isinstance(run_id, str):
            continue
        at = timeutil.from_iso(run_id, tz_name)
        while index < len(ordered) and ordered[index].filled_at <= at:
            trade = ordered[index]
            notional = trade.fill_price * trade.amount
            if trade.side == "buy":
                cash -= notional + trade.fee_quote
                position += trade.amount
            else:
                cash += notional - trade.fee_quote
                position -= trade.amount
            index += 1
        points.append(
            Point(at=at, price=Decimal(str(price)), cash_jpy=cash, position=position)
        )
    return points


def settled(points: Sequence[Point], trades: Sequence[Trade]) -> list[Point]:
    """最後の観測より後の約定を、終端の1点として点列へ足す。

    `equity_series` は観測した時刻の資産しか復元できない。実行が途切れた
    時間帯に指値が約定していると、点列は約定前で止まる。約定履歴には
    残っているのに総資産へ反映されず、決済した日が「建玉を抱えたまま」に
    見えてしまう。

    価格は最後に観測した値をそのまま使う。約定後の価格は観測していないので、
    新しい価格を作らない（`CLAUDE.md`「観測していない値を書かない」）。
    """
    if not points:
        return []
    last = points[-1]
    later = sorted(
        (t for t in trades if t.filled_at > last.at), key=lambda t: t.filled_at
    )
    if not later:
        return list(points)

    cash = last.cash_jpy
    position = last.position
    for trade in later:
        notional = trade.fill_price * trade.amount
        if trade.side == "buy":
            cash -= notional + trade.fee_quote
            position += trade.amount
        else:
            cash += notional - trade.fee_quote
            position -= trade.amount
    return [
        *points,
        Point(
            at=later[-1].filled_at,
            price=last.price,
            cash_jpy=cash,
            position=position,
        ),
    ]


def max_drawdown_pct(points: Sequence[Point]) -> Decimal:
    """過去最高資産からの最大下落率。正の数で返す。"""
    peak = Decimal(0)
    worst = Decimal(0)
    for point in points:
        equity = point.equity_jpy
        if equity > peak:
            peak = equity
        if peak > 0:
            drop = (peak - equity) / peak * Decimal(100)
            worst = max(worst, drop)
    return worst


def drawdown_from_peak_pct(points: Sequence[Point]) -> Decimal:
    """いまが過去最高からどれだけ下がっているか。正の数で返す。"""
    if not points:
        return Decimal(0)
    peak = max((p.equity_jpy for p in points), default=Decimal(0))
    current = points[-1].equity_jpy
    if peak <= 0:
        return Decimal(0)
    return max(Decimal(0), (peak - current) / peak * Decimal(100))


def buy_and_hold_equity(config: Config, points: Sequence[Point]) -> Decimal | None:
    """同じ資金を最初の観測価格で買って持ち続けた場合の総資産。"""
    if not points or points[0].price <= 0:
        return None
    initial = Decimal(str(config.initial_jpy))
    return initial / points[0].price * points[-1].price


def streaks(closed: Sequence[Round]) -> tuple[int, int]:
    """直近の連勝・連敗。同時に 0 より大きくなることはない。"""
    wins = losses = 0
    for round_ in reversed(closed):
        if round_.realized_pnl_jpy > 0:
            if losses:
                break
            wins += 1
        elif round_.realized_pnl_jpy < 0:
            if wins:
                break
            losses += 1
        else:
            break
    return wins, losses


def _pct(value: Decimal, digits: int = 2) -> float:
    return round(float(value), digits)


def build(
    config: Config,
    now: datetime,
    records: Sequence[dict],
    trades: Sequence[Trade],
) -> dict:
    """records/performance.sample.yaml と同じ形式の実績を組み立てる。"""
    points = equity_series(config, records, trades, config.timezone)
    initial = Decimal(str(config.initial_jpy))
    current = points[-1].equity_jpy if points else initial

    since = now - timedelta(hours=24)
    window = [p for p in points if p.at >= since]
    base = window[0].equity_jpy if window else (points[0].equity_jpy if points else initial)
    pnl_24h = (current - base) / base * Decimal(100) if base > 0 else Decimal(0)

    closed = [r for r in rounds(trades) if r.is_closed]
    wins, losses = streaks(closed)

    return {
        "schema_version": 1,
        "agent_id": config.agent_id,
        "agent_version": config.version,
        "recorded_at": timeutil.to_iso(now),
        "source": "paper",
        "initial_equity_jpy": float(initial),
        "current_equity_jpy": round(float(current)),
        "total_pnl_pct": _pct((current - initial) / initial * Decimal(100)),
        "pnl_24h_pct": _pct(pnl_24h),
        "current_drawdown_from_peak_pct": _pct(drawdown_from_peak_pct(points)),
        "trades_24h": sum(1 for t in trades if t.filled_at >= since),
        "consecutive_wins": wins,
        "consecutive_losses": losses,
    }


def write(document: dict, path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# ペーパートレード実績（運用の産物。Git 管理外）\n"
        "#\n"
        "# 実行のたびに書き出します。判定結果（normal / down / up）は書きません。\n"
        "# 表示側が mood-rules.yaml を使って毎回算出します。\n"
        "#\n"
        "# 総資産の推移は、判断ログの観測価格と約定履歴から復元しています。\n"
    )
    body = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    target.write_text(f"{header}\n{body}", encoding="utf-8")
    return target


def all_records(config: Config, root: Path | None = None) -> list[dict]:
    """残っているすべての判断ログを、古い順に読む。"""
    records: list[dict] = []
    for date in journal.recorded_dates(config, root):
        records.extend(journal.read_day(config, date, root))
    return records


def refresh(
    config: Config,
    now: datetime,
    trades: Sequence[Trade],
    root: Path | None = None,
) -> Path:
    """実績を組み立てて書き出す。"""
    base = root if root is not None else REPO_ROOT
    document = build(config, now, all_records(config, root), trades)
    return write(document, base / config.performance_output)
