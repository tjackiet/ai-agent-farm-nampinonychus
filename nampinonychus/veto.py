"""買いを止める権利だけを LLM に渡す。

判断を LLM へ移す段階2（docs/IMPLEMENTATION_PLAN.md「判断を LLM へ移すこと」）。

**関与するのは止める方向だけ。** 決定的コードが BUY と決めたあとにだけ呼び、
返せる答えは「そのまま通す」か「この回はやめる」の二択にする。買う・増やす・
価格や数量を変える方向には一切関与させない。したがって、この層を足しても
最悪ケース（どこまで買い下がるか、どこで冬眠・撤退するか）は変わらない。

止めた回は**取消も発注もしない**。板の状態を変えないまま次の回へ持ち越し、
決定的コードがまた同じ判断をすれば、また諮る。

呼べなかったとき・答えを読めなかったときの扱いは `agent.yaml` の
`veto.on_failure` で決める。既定は `hold`（CLAUDE.md「判断できないときは HOLD」）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from .config import Config
from .decide import BUY, HOLD, Decision
from .narrate import Writer
from .observe import Market
from .state import State

STOP = "STOP"
PROCEED = "PROCEED"

# 行頭の判定だけを拾う。地の文に出てくる同じ語は判定にしない。
_VERDICT = re.compile(rf"^({STOP}|{PROCEED})\b[*_`]*[:：]?\s*(.*)$")
# 太字や箇条書きで飾られた行も判定として読む。読めない答えは失敗扱いになるため、
# 書式の揺れだけで失敗させない。
_DECORATION = re.compile(r"^[\s*_`#>-]+|[\s*_`]+$")

RULES = f"""あなたはペーパートレードを行うエージェント「ナンピノニクス」です。
戦略に沿った買い注文が組み上がりました。あなたの役割は**この回だけ見送るかどうか**です。

できることは1つだけです。

- {PROCEED} … そのまま出す
- {STOP} … この回は見送る

守ること:

- **買う方向には関与できません。** 数量・価格・段数を変える提案はしないでください。
  それらは agent.yaml の値であり、あなたが動かすものではありません
- 渡された事実だけで判断してください。相場の予測を根拠にしないでください
- 迷ったら {PROCEED} です。戦略はすでに下落率・段数・クールダウン・
  ドローダウン・現金比率の制約を通っています。{STOP} は「この注文を出すと、
  渡された事実から見て明らかにおかしい」と言えるときだけです
- 出力は1行だけ。`{PROCEED}` か `{STOP}: 見送る理由` のどちらかにしてください。
  前置きも補足も書かないでください"""


@dataclass(frozen=True)
class Veto:
    """拒否権を諮った結果。諮らなかった回も記録に残す。"""

    consulted: bool
    stopped: bool
    reason: str = ""
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "consulted": self.consulted,
            "stopped": self.stopped,
            "reason": self.reason,
            "error": self.error,
        }


SKIPPED = Veto(consulted=False, stopped=False)


def is_reviewable(decision: Decision) -> bool:
    """諮る対象か。

    買いを出す回だけ。利確の売り・手仕舞い・冬眠の取消は諮らない。
    それらを止めるのは建玉を抱え続ける方向であり、安全側ではない。
    """
    return decision.action == BUY


def _fmt(value: Decimal | float | None) -> str:
    return "不明" if value is None else f"{value}"


def _order_lines(decision: Decision) -> list[str]:
    return [
        f"- {o.side} {o.order_type} {o.amount} @ {o.price}（{o.label}）"
        for o in decision.place
    ]


def _history_lines(records: Sequence[dict], limit: int) -> list[str]:
    lines = []
    for record in list(records)[-limit:]:
        lines.append(
            f"- {record.get('run_id')} {record.get('action')} "
            f"価格 {record.get('price')} / {record.get('reason')}"
        )
    return lines


def build_brief(
    config: Config,
    decision: Decision,
    market: Market,
    state: State,
    now: datetime,
    records: Sequence[dict] = (),
) -> str:
    """諮るための事実。ここに無い数値を使わせない。"""
    position = state.position
    ladder = state.ladder
    account = state.account
    lines = [
        f"時刻: {now.isoformat(timespec='seconds')}",
        f"銘柄: {config.pair}",
        f"状態: {decision.state}",
        "",
        "## 市場",
        f"- 現在価格: {market.last}",
        f"- アンカー（直近{config.anchor_lookback_minutes}分の高値）: {market.anchor}",
        f"- アンカーからの下落: {market.drop_from_anchor_pct:.2f}%",
        "",
        "## 口座",
        f"- 総資産: {account.equity_jpy}（初期 {account.initial_jpy}）",
        f"- 初期比: {account.drawdown_pct:.2f}%",
        f"- 使える現金: {account.cash_available_jpy}",
        f"- 建玉: {position.amount}（平均取得単価 {_fmt(position.avg_cost_jpy)}）",
        f"- 保有日数: {_fmt(position.age_days)}",
        f"- 約定済みの段: {ladder.step} / {config.ladder_max_steps}",
        f"- 本日の約定回数: {ladder.fills_today} / {config.max_fills_per_day}",
        "",
        "## 戦略が決めたこと",
        f"- 理由: {decision.reason}",
        "- 出そうとしている注文:",
        *_order_lines(decision),
    ]
    history = _history_lines(records, config.veto_read_last_n)
    if history:
        lines += ["", "## 直近の判断", *history]
    return "\n".join(lines)


def parse(text: str) -> tuple[bool, str]:
    """応答から判定を読む。読めなければ ValueError。

    考えを書いてから結論を置く応答もあるため、後ろから探す。
    """
    for line in reversed((text or "").splitlines()):
        matched = _VERDICT.match(_DECORATION.sub("", line))
        if matched is None:
            continue
        verdict, reason = matched.group(1), matched.group(2).strip()
        return verdict == STOP, reason
    raise ValueError(f"{PROCEED} か {STOP} を読み取れません: {(text or '')[:200]!r}")


def review(
    config: Config,
    decision: Decision,
    market: Market | None,
    state: State | None,
    now: datetime,
    writer: Writer,
    records: Sequence[dict] = (),
) -> Veto:
    """買いを出す前に諮る。諮らない回は SKIPPED を返す。"""
    if not config.veto_enabled or not is_reviewable(decision):
        return SKIPPED
    if market is None or state is None:
        return SKIPPED

    brief = build_brief(config, decision, market, state, now, records)
    try:
        answer = writer(RULES, brief)
        stopped, reason = parse(answer)
    except Exception as exc:  # noqa: BLE001 - LLM の失敗で1周を落とさない
        return Veto(
            consulted=True,
            stopped=config.veto_on_failure == "hold",
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
    if stopped and not reason:
        reason = "理由が書かれていない"
    return Veto(consulted=True, stopped=stopped, reason=reason)


def apply(decision: Decision, veto: Veto) -> Decision:
    """止められた回を HOLD に変える。取消も発注も残さない。"""
    if not veto.stopped:
        return decision
    if veto.error is not None:
        reason = f"LLM に諮れなかったため見送る: {veto.error}"
    else:
        reason = f"LLM が見送ると判断した: {veto.reason}"
    return Decision(action=HOLD, state=decision.state, reason=reason)
