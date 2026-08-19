"""動きがあったときの通知。

1方向で送るだけ。**通知は判断に影響させない。** 送れなくても発注は続ける。

Webhook の URL は環境変数からのみ読む。リポジトリにも判断ログにも書かない
（CLAUDE.md「API キー・シークレット・プロファイル名は、ログにも記憶にも残さない」）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Sequence

import yaml

from . import timeutil
from .config import Config, REPO_ROOT

Poster = Callable[[str, str, int], None]


@dataclass(frozen=True)
class Previous:
    """前回の実行の様子。通知の差分をとるためだけに使う。"""

    at: datetime | None
    state: str | None


def _post(url: str, content: str, timeout: int) -> None:
    payload = json.dumps({"content": content}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(request, timeout=timeout).close()  # noqa: S310


def webhook_url(config: Config) -> str | None:
    """環境変数から URL を読む。無ければ通知しない。"""
    value = os.environ.get(config.notify_webhook_env, "").strip()
    return value or None


def read_previous(config: Config, root: Path | None = None) -> Previous:
    """前回書き出したスナップショットから、状態と時刻を読む。

    **判断には使わない。** 状態が変わったかを知るためだけに読む
    （memory-policy.md「判断の入力としてスナップショットを読まない」）。
    """
    base = root if root is not None else REPO_ROOT
    path = base / config.status_output
    if not path.is_file():
        return Previous(at=None, state=None)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return Previous(at=None, state=None)
    if not isinstance(document, dict):
        return Previous(at=None, state=None)
    updated_at = document.get("updated_at")
    at = None
    if isinstance(updated_at, str):
        try:
            at = timeutil.from_iso(updated_at, config.timezone)
        except ValueError:
            at = None
    state = document.get("state")
    return Previous(at=at, state=state if isinstance(state, str) else None)


def _jpy(value) -> str:
    if value is None:
        return "—"
    return f"{round(float(value)):,}"


def _btc(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def fill_lines(fills: Sequence[dict]) -> list[str]:
    lines = []
    for fill in fills:
        side = "買い" if fill.get("side") == "buy" else "売り"
        price = fill.get("fillPrice")
        amount = fill.get("amount")
        notional = float(price) * float(amount) if price and amount else None
        lines.append(
            f"約定 {side} {_jpy(price)} × {_btc(amount)}（{_jpy(notional)} JPY）"
        )
    return lines


def order_lines(orders: Sequence[dict]) -> list[str]:
    lines = []
    for order in orders:
        if not order.get("executed"):
            continue
        if order.get("op") == "cancel":
            lines.append(f"取消 {order.get('order_id')}")
            continue
        side = "買い" if order.get("side") == "buy" else "売り"
        lines.append(
            f"発注 {order.get('label')} {side} {_jpy(order.get('price'))} "
            f"× {_btc(order.get('amount'))}"
        )
    return lines


def error_streak(records: Sequence[dict]) -> int:
    """直近で何回続けて失敗したか。"""
    streak = 0
    for record in reversed(records):
        if record.get("error"):
            streak += 1
        else:
            break
    return streak


def crossed_report_times(
    config: Config, previous_at: datetime | None, now: datetime
) -> list[str]:
    """前回の実行から今回までに、レポートの時刻をまたいだか。

    寝ていて時刻を過ぎてしまっても、起きた最初の実行で送る。
    """
    if previous_at is None or previous_at >= now:
        return []
    crossed = []
    for slot in config.notify_report_at:
        hour, _, minute = slot.partition(":")
        try:
            target = now.replace(
                hour=int(hour), minute=int(minute), second=0, microsecond=0
            )
        except ValueError:
            continue
        if previous_at < target <= now:
            crossed.append(slot)
    return crossed


def build_report(config: Config, now: datetime, performance: dict, state) -> str:
    """半日ごとの振り返り。数値は実績の集計をそのまま使う。"""
    lines = [
        f"**{timeutil.to_iso(now)} の振り返り**",
        f"総資産 {_jpy(performance['current_equity_jpy'])} JPY "
        f"({performance['total_pnl_pct']:+.2f}%) / "
        f"24時間 {performance['pnl_24h_pct']:+.2f}%",
        f"最高からの下落 {performance['current_drawdown_from_peak_pct']:.2f}% / "
        f"24時間の約定 {performance['trades_24h']}件",
    ]
    if state is not None:
        if state.position.amount > 0:
            lines.append(
                f"建玉 {_btc(state.position.amount)} BTC / "
                f"平均取得単価 {_jpy(state.position.avg_cost_jpy)} / "
                f"{state.ladder.step} 段目"
            )
        else:
            lines.append("建玉なし")
        lines.append(
            f"板 買い {len(state.pending_buy)}本 / 売り {len(state.pending_sell)}本"
        )
    return "\n".join(lines)


def build_messages(
    config: Config,
    now: datetime,
    previous: Previous,
    decision_state: str,
    fills: Sequence[dict],
    orders: Sequence[dict],
    records: Sequence[dict],
) -> list[str]:
    """この回に知らせるべきことを組み立てる。何もなければ空。"""
    enabled = config.notify_on
    messages: list[str] = []

    if enabled.get("fill") and fills:
        messages.extend(fill_lines(fills))

    if enabled.get("order"):
        messages.extend(order_lines(orders))

    if (
        enabled.get("state_change")
        and previous.state is not None
        and previous.state != decision_state
    ):
        messages.append(f"状態 {previous.state} → {decision_state}")

    if enabled.get("error"):
        streak = error_streak(records)
        if streak >= config.notify_error_streak:
            last = records[-1].get("error") if records else ""
            messages.append(f"{streak}回続けて失敗しています: {last}")

    return messages


def send(
    config: Config,
    messages: Sequence[str],
    poster: Poster | None = None,
) -> str | None:
    """まとめて1通にして送る。失敗したら理由を返す（例外は投げない）。"""
    if not config.notify_enabled or not messages:
        return None
    url = webhook_url(config)
    if url is None:
        return f"{config.notify_webhook_env} が設定されていないため通知しません"
    body = "\n".join(messages)[:1900]
    try:
        (poster or _post)(url, body, config.notify_timeout_sec)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # URL は出力しない。理由だけ残す。
        return f"通知を送れませんでした: {type(exc).__name__}"
    return None
