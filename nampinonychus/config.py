"""agent.yaml の読み込みと検証。

数値パラメータの入口はこのモジュールだけとする。
値の唯一の正は agent.yaml であり、既定値をここに書かない
（欠けていれば ConfigError で落とす。黙って補わない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """agent.yaml が読めない、または必要な値が欠けている。"""


def _get(raw: Any, path: str) -> Any:
    node: Any = raw
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"agent.yaml に {path} がありません")
        node = node[key]
    return node


def _num(raw: Any, path: str) -> float:
    value = _get(raw, path)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"agent.yaml の {path} が数値ではありません: {value!r}")
    return float(value)


def _int(raw: Any, path: str) -> int:
    value = _num(raw, path)
    if value != int(value):
        raise ConfigError(f"agent.yaml の {path} が整数ではありません: {value!r}")
    return int(value)


def _str(raw: Any, path: str) -> str:
    value = _get(raw, path)
    if not isinstance(value, str):
        raise ConfigError(f"agent.yaml の {path} が文字列ではありません: {value!r}")
    return value


def _bool(raw: Any, path: str) -> bool:
    value = _get(raw, path)
    if not isinstance(value, bool):
        raise ConfigError(f"agent.yaml の {path} が真偽値ではありません: {value!r}")
    return value


# 使わないモデル。料金が上位帯のため、設定に書かれていても受け付けない。
# 事故で高いモデルが選ばれることを防ぐ。
FORBIDDEN_MODEL_MARKERS = ("fable", "mythos")


def _model(raw: Any, path: str) -> str:
    value = _str(raw, path)
    lowered = value.lower()
    for marker in FORBIDDEN_MODEL_MARKERS:
        if marker in lowered:
            raise ConfigError(
                f"agent.yaml の {path} に {marker} 系のモデルは指定できません: {value}"
            )
    return value


@dataclass(frozen=True)
class LadderStep:
    """買い下がりの1段。`base` は下落率の基準点（anchor / last_fill）。"""

    step: int
    base: str
    drop_pct: float
    budget_jpy: float


@dataclass(frozen=True)
class TakeProfit:
    """利確の1段階。`sell_ratio` は建玉に対する比率。"""

    level: int
    gain_pct: float
    sell_ratio: float


@dataclass(frozen=True)
class LlmSettings:
    """LLM の呼びかた。言語化（narrate）と拒否権（veto）が同じ形で持つ。"""

    writer: str
    command: str
    bare: bool
    timeout_sec: int
    model: str
    effort: str
    max_tokens: int


def _llm_settings(raw: Any, prefix: str) -> LlmSettings:
    return LlmSettings(
        writer=_str(raw, f"{prefix}.writer"),
        command=_str(raw, f"{prefix}.command"),
        bare=_bool(raw, f"{prefix}.bare"),
        timeout_sec=_int(raw, f"{prefix}.timeout_sec"),
        model=_model(raw, f"{prefix}.model"),
        effort=_str(raw, f"{prefix}.effort"),
        max_tokens=_int(raw, f"{prefix}.max_tokens"),
    )


@dataclass(frozen=True)
class Config:
    """agent.yaml の内容。すべて読み取り専用。"""

    version: str
    agent_id: str
    phase: str
    pair: str
    dry_run: bool
    timezone: str
    max_runtime_sec: int
    stale_tick_hours: float

    cli_command: str
    global_flags: tuple[str, ...]
    state_path: str
    forbidden: tuple[str, ...]

    initial_jpy: float

    anchor_candle_type: str
    anchor_lookback_minutes: int
    anchor_metric: str

    max_pending_buy_orders: int
    cooldown_hours_after_fill: float
    max_fills_per_day: int
    reprice_threshold_pct: float

    ladder_max_steps: int
    ladder_total_budget_jpy: float
    ladder_steps: tuple[LadderStep, ...]

    take_profit: tuple[TakeProfit, ...]
    time_stop_days: float

    no_chase_enabled: bool

    max_position_ratio: float
    min_cash_reserve_ratio: float
    per_order_max_jpy: float

    halt_new_buys_pct: float
    forced_exit_pct: float

    skip_on_circuit_break: bool
    skip_on_exchange_maintenance: bool
    max_data_age_sec: int

    status_output: str
    performance_output: str
    narrate_enabled: bool
    narrate_writer: str
    narrate_command: str
    narrate_bare: bool
    narrate_timeout_sec: int
    narrate_model: str
    narrate_effort: str
    narrate_max_tokens: int
    narrate_targets: dict

    veto_enabled: bool
    veto_on_failure: str
    veto_read_last_n: int
    veto_llm: LlmSettings

    notify_enabled: bool
    notify_webhook_env: str
    notify_timeout_sec: int
    notify_on: dict
    notify_error_streak: int
    notify_report_at: tuple[str, ...]

    decisions_path: str
    decisions_read_last_n: int
    daily_path: str
    daily_write_at: str
    daily_read_last_n: int
    lessons_path: str
    lessons_max_entries: int

    raw: dict


# LLM を呼べなかったときにどうするか。
# proceed: 決定的コードの判断をそのまま通す（最悪ケースは LLM 無しのときと同じ）
# hold   : この回は何もしない（CLAUDE.md「判断できないときは HOLD」に寄せる）
ON_FAILURE_CHOICES = ("proceed", "hold")


def _on_failure(raw: Any) -> str:
    value = _str(raw, "veto.on_failure")
    if value not in ON_FAILURE_CHOICES:
        raise ConfigError(
            f"agent.yaml の veto.on_failure は "
            f"{' か '.join(ON_FAILURE_CHOICES)} のどちらかです: {value}"
        )
    return value


def load(path: Path | str | None = None) -> Config:
    """agent.yaml を読み込む。欠けている値があれば ConfigError。"""
    target = Path(path) if path is not None else REPO_ROOT / "agent.yaml"
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"agent.yaml が見つかりません: {target}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"agent.yaml を解釈できません: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("agent.yaml の内容が辞書ではありません")

    pairs = _get(raw, "market.pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ConfigError("agent.yaml の market.pairs が空です")

    steps_raw = _get(raw, "strategy.ladder.steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ConfigError("agent.yaml の strategy.ladder.steps が空です")
    steps = tuple(
        LadderStep(
            step=_int(item, "step"),
            base=_str(item, "from"),
            drop_pct=_num(item, "drop_pct"),
            budget_jpy=_num(item, "budget_jpy"),
        )
        for item in steps_raw
    )
    for index, step in enumerate(steps, start=1):
        if step.step != index:
            raise ConfigError(f"strategy.ladder.steps の step が連番ではありません: {step.step}")
        if step.base not in ("anchor", "last_fill"):
            raise ConfigError(f"strategy.ladder.steps の from が不正です: {step.base}")

    tp_raw = _get(raw, "strategy.exit.take_profit")
    if not isinstance(tp_raw, list) or not tp_raw:
        raise ConfigError("agent.yaml の strategy.exit.take_profit が空です")
    take_profit = tuple(
        TakeProfit(
            level=_int(item, "level"),
            gain_pct=_num(item, "gain_pct"),
            sell_ratio=_num(item, "sell_ratio"),
        )
        for item in tp_raw
    )

    forbidden = _get(raw, "cli.forbidden")
    if not isinstance(forbidden, list) or not forbidden:
        raise ConfigError("agent.yaml の cli.forbidden が空です")

    flags = _get(raw, "cli.global_flags")
    if not isinstance(flags, list):
        raise ConfigError("agent.yaml の cli.global_flags が配列ではありません")

    return Config(
        version=_str(raw, "version"),
        agent_id=_str(raw, "agent.id"),
        phase=_str(raw, "agent.phase"),
        pair=str(pairs[0]),
        dry_run=_bool(raw, "runtime.dry_run"),
        timezone=_str(raw, "runtime.timezone"),
        max_runtime_sec=_int(raw, "runtime.max_runtime_sec"),
        stale_tick_hours=_num(raw, "runtime.stale_tick_hours"),
        cli_command=_str(raw, "cli.command"),
        global_flags=tuple(str(f) for f in flags),
        state_path=_str(raw, "cli.state_path"),
        forbidden=tuple(str(f) for f in forbidden),
        initial_jpy=_num(raw, "capital.initial_jpy"),
        anchor_candle_type=_str(raw, "strategy.anchor.candle_type"),
        anchor_lookback_minutes=_int(raw, "strategy.anchor.lookback_minutes"),
        anchor_metric=_str(raw, "strategy.anchor.metric"),
        max_pending_buy_orders=_int(raw, "strategy.entry.max_pending_buy_orders"),
        cooldown_hours_after_fill=_num(raw, "strategy.entry.cooldown_hours_after_fill"),
        max_fills_per_day=_int(raw, "strategy.entry.max_fills_per_day"),
        reprice_threshold_pct=_num(raw, "strategy.entry.reprice_threshold_pct"),
        ladder_max_steps=_int(raw, "strategy.ladder.max_steps"),
        ladder_total_budget_jpy=_num(raw, "strategy.ladder.total_budget_jpy"),
        ladder_steps=steps,
        take_profit=take_profit,
        time_stop_days=_num(raw, "strategy.exit.time_stop_days"),
        no_chase_enabled=_bool(raw, "strategy.no_chase.enabled"),
        max_position_ratio=_num(raw, "risk.max_position_ratio"),
        min_cash_reserve_ratio=_num(raw, "risk.min_cash_reserve_ratio"),
        per_order_max_jpy=_num(raw, "risk.per_order_max_jpy"),
        halt_new_buys_pct=_num(raw, "risk.drawdown.halt_new_buys_pct"),
        forced_exit_pct=_num(raw, "risk.drawdown.forced_exit_pct"),
        skip_on_circuit_break=_bool(raw, "risk.guards.skip_on_circuit_break"),
        skip_on_exchange_maintenance=_bool(raw, "risk.guards.skip_on_exchange_maintenance"),
        max_data_age_sec=_int(raw, "risk.guards.max_data_age_sec"),
        status_output=_str(raw, "agent.status_output"),
        performance_output=_str(raw, "agent.performance_output"),
        narrate_enabled=_bool(raw, "narrate.enabled"),
        narrate_writer=_str(raw, "narrate.writer"),
        narrate_command=_str(raw, "narrate.command"),
        narrate_bare=_bool(raw, "narrate.bare"),
        narrate_timeout_sec=_int(raw, "narrate.timeout_sec"),
        narrate_model=_model(raw, "narrate.model"),
        narrate_effort=_str(raw, "narrate.effort"),
        narrate_max_tokens=_int(raw, "narrate.max_tokens"),
        narrate_targets={str(k): bool(v) for k, v in _get(raw, "narrate.targets").items()},
        veto_enabled=_bool(raw, "veto.enabled"),
        veto_on_failure=_on_failure(raw),
        veto_read_last_n=_int(raw, "veto.read_last_n"),
        veto_llm=_llm_settings(raw, "veto"),
        notify_enabled=_bool(raw, "notify.enabled"),
        notify_webhook_env=_str(raw, "notify.webhook_env"),
        notify_timeout_sec=_int(raw, "notify.timeout_sec"),
        notify_on={str(k): bool(v) for k, v in _get(raw, "notify.events").items()},
        notify_error_streak=_int(raw, "notify.error_streak"),
        notify_report_at=tuple(str(t) for t in _get(raw, "notify.report_at")),
        decisions_path=_str(raw, "memory.decisions.path"),
        decisions_read_last_n=_int(raw, "memory.decisions.read_last_n"),
        daily_path=_str(raw, "memory.daily.path"),
        daily_write_at=_str(raw, "memory.daily.write_at"),
        daily_read_last_n=_int(raw, "memory.daily.read_last_n"),
        lessons_path=_str(raw, "memory.lessons.path"),
        lessons_max_entries=_int(raw, "memory.lessons.max_entries"),
        raw=raw,
    )
