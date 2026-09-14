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


# 名乗らないと Discord の前段にいる Cloudflare が弾く（403 / error code 1010）。
# urllib の既定は `Python-urllib/3.x` で、これが署名として拒否される。
USER_AGENT = "Nampinonychus/1.0 (paper-trading agent)"


def _post(url: str, content: str, timeout: int) -> None:
    payload = json.dumps({"content": content}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
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
    """直近で何回続けて失敗したか。

    数えるのは観測と発注の失敗（`record["error"]`）だけ。拒否権を諮れなかった
    失敗は `veto.error` にあり、意味が違うのでここには混ぜない（`veto_streak`）。
    """
    streak = 0
    for record in reversed(records):
        if record.get("error"):
            streak += 1
        else:
            break
    return streak


@dataclass(frozen=True)
class VetoStreak:
    """拒否権で買いを見送った回が、直近から何回続いているか。

    `error` は直近の回の `veto.error`。入っていれば**諮れていない**（認証切れなど、
    直すべき異常）。無ければ LLM が意図して見送っている。運用者の対応が変わるため、
    文面で区別する。

    `extended` は、この回そのものが見送りだったか。数は諮った回だけで増えるので、
    諮らない回が続くと数が止まる。止まった数が発報の点に当たっていると、同じ通知が
    毎回出る。**知らせるのは数が伸びた回だけにする。**
    """

    count: int
    error: str | None = None
    reason: str = ""
    extended: bool = False


def veto_streak(records: Sequence[dict]) -> VetoStreak:
    """直近から連続して拒否権で見送った回を数える。

    通した回（`veto.stopped` が false）で切れる。諮っていない回（`veto` が無い回）は
    読み飛ばす。**諮るのは買いを出す回だけ**なので、間に挟まる HOLD で切っては
    見送りが続いていることが分からない。
    """
    count = 0
    error: str | None = None
    reason = ""
    latest = records[-1].get("veto") if records else None
    extended = isinstance(latest, dict) and bool(latest.get("stopped"))
    for record in reversed(records):
        veto = record.get("veto")
        if not isinstance(veto, dict):
            continue
        if not veto.get("stopped"):
            break
        if count == 0:
            last_error = veto.get("error")
            error = last_error if isinstance(last_error, str) and last_error else None
            last_reason = veto.get("reason")
            reason = last_reason if isinstance(last_reason, str) else ""
        count += 1
    return VetoStreak(count=count, error=error, reason=reason, extended=extended)


def veto_streak_line(streak: VetoStreak) -> str:
    """見送りが続いていることを1行で。"""
    if streak.error is not None:
        return (
            f"{streak.count}回続けて拒否権を諮れず、買いを見送っています: {streak.error}"
        )
    reason = streak.reason or "理由は記録にありません"
    return f"{streak.count}回続けて LLM の判断で買いを見送っています: {reason}"


def streak_due(streak: int, threshold: int, repeat_every: int) -> bool:
    """閾値に達した回と、そのあとは `repeat_every` 回ごとにだけ真。

    閾値を超えている間ずっと送ると、15分ごとの運用では1日 96 通になる。
    11 日続いた凍結ではおよそ 1,000 通ぶんに相当した。
    """
    if streak <= 0 or streak < threshold:
        return False
    if repeat_every <= 0:
        return streak == threshold
    return (streak - threshold) % repeat_every == 0


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
        if streak_due(
            streak, config.notify_error_streak, config.notify_streak_repeat_every
        ):
            last = records[-1].get("error") if records else ""
            messages.append(f"{streak}回続けて失敗しています: {last}")

    if enabled.get("veto_streak"):
        stopped = veto_streak(records)
        if stopped.extended and streak_due(
            stopped.count, config.notify_veto_streak, config.notify_streak_repeat_every
        ):
            messages.append(veto_streak_line(stopped))

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
    except urllib.error.HTTPError as exc:
        # URL は出力しない。状態コードは秘密ではなく、原因の切り分けに要る。
        return f"通知を送れませんでした: HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"通知を送れませんでした: {type(exc).__name__}"
    return None
