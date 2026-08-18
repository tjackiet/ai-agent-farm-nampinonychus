"""BUY・SELL・HOLD の判断。

このモジュールは純関数だけで構成する。I/O を持たない。
リスク制約は戦略に優先する（risk-policy.md）。判断できないときは HOLD。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .config import Config
from .observe import Guards, Market
from .orders import (
    PairSpec,
    PlaceOrder,
    floor_price,
    floor_to_unit,
    buy_amount,
    sell_amount,
    to_decimal,
)
from .state import DUST, State

HOLD = "HOLD"
BUY = "BUY"
SELL = "SELL"


@dataclass(frozen=True)
class Decision:
    action: str
    state: str
    reason: str
    cancel: tuple[str, ...] = ()
    place: tuple[PlaceOrder, ...] = ()


def _hold(state_name: str, reason: str) -> Decision:
    return Decision(action=HOLD, state=state_name, reason=reason)


def state_name(config: Config, state: State | None) -> str:
    if state is None:
        return "NOT_INITIALIZED"
    drawdown = state.account.drawdown_pct
    if drawdown <= to_decimal(config.forced_exit_pct):
        return "HALTED"
    if drawdown <= to_decimal(config.halt_new_buys_pct):
        return "HIBERNATING"
    if state.position.amount > 0:
        if state.ladder.step >= config.ladder_max_steps:
            return "HOLDING"
        return "LADDERING"
    return "IDLE"


def desired_sell_orders(
    config: Config, spec: PairSpec, state: State
) -> tuple[PlaceOrder, ...]:
    """建玉に対して板に置いておくべき利確の売り指値。"""
    position = state.position.amount
    avg_cost = state.position.avg_cost_jpy
    if position <= 0 or avg_cost is None:
        return ()

    levels = sorted(config.take_profit, key=lambda tp: tp.level)

    def price_of(gain_pct: float) -> Decimal:
        gain = to_decimal(gain_pct) / Decimal(100)
        return floor_price(avg_cost * (Decimal(1) + gain), spec.price_digits)

    # 1段目が約定済みのラウンドでは、残り全量を最終段の価格で置き直す。
    if state.ladder.sold_in_round > DUST:
        amount = floor_to_unit(position, spec.unit_amount)
        if amount < spec.unit_amount:
            return ()
        last = levels[-1]
        return (
            PlaceOrder(
                side="sell",
                order_type="limit",
                amount=amount,
                price=price_of(last.gain_pct),
                label=f"tp-{last.level}",
            ),
        )

    placed: list[PlaceOrder] = []
    remaining = position
    for level in levels[:-1]:
        amount = sell_amount(position, to_decimal(level.sell_ratio), spec)
        if amount > remaining:
            amount = floor_to_unit(remaining, spec.unit_amount)
        if amount < spec.unit_amount:
            continue
        placed.append(
            PlaceOrder(
                side="sell",
                order_type="limit",
                amount=amount,
                price=price_of(level.gain_pct),
                label=f"tp-{level.level}",
            )
        )
        remaining -= amount

    last = levels[-1]
    amount = floor_to_unit(remaining, spec.unit_amount)
    if amount >= spec.unit_amount:
        placed.append(
            PlaceOrder(
                side="sell",
                order_type="limit",
                amount=amount,
                price=price_of(last.gain_pct),
                label=f"tp-{last.level}",
            )
        )
    return tuple(placed)


def _sells_match(state: State, desired: tuple[PlaceOrder, ...]) -> bool:
    current = sorted((o.price, o.amount) for o in state.pending_sell)
    wanted = sorted((o.price, o.amount) for o in desired)
    return current == wanted


def _exit_all(
    config: Config, spec: PairSpec, state: State, name: str, reason: str
) -> Decision:
    """全建玉を成行で手仕舞いする（強制手仕舞い・時間切れ）。"""
    cancel = tuple(o.id for o in (*state.pending_buy, *state.pending_sell))
    amount = floor_to_unit(state.position.amount, spec.unit_amount)
    if amount > spec.market_max_amount:
        amount = floor_to_unit(spec.market_max_amount, spec.unit_amount)
    if amount < spec.unit_amount:
        return Decision(action=HOLD, state=name, reason=f"{reason}（売却できる建玉なし）", cancel=cancel)
    return Decision(
        action=SELL,
        state=name,
        reason=reason,
        cancel=cancel,
        place=(
            PlaceOrder(
                side="sell",
                order_type="market",
                amount=amount,
                price=None,
                label="exit",
            ),
        ),
    )


def _next_buy(
    config: Config, market: Market, spec: PairSpec, state: State, name: str, now: datetime
) -> Decision:
    """次の段の買い指値を出せるか判断する。"""
    if config.no_chase_enabled and market.last >= market.anchor:
        return _hold(name, "現在価格がアンカー以上のため追わない")

    pending = len(state.pending_buy)
    if pending >= config.max_pending_buy_orders:
        return _hold(name, f"未約定の買い指値が上限（{config.max_pending_buy_orders}本）に達している")

    if state.ladder.fills_today >= config.max_fills_per_day:
        return _hold(name, f"本日すでに {state.ladder.fills_today} 回約定している")

    cooldown = state.ladder.cooldown_until
    if cooldown is not None and now < cooldown:
        return _hold(name, f"クールダウン中。次の段は {cooldown.isoformat(timespec='minutes')} 以降")

    # 板に置いてある未約定の段も「使った段」として数える。
    index = state.ladder.step + pending
    if index >= config.ladder_max_steps or index >= len(config.ladder_steps):
        return _hold(name, "階段を使い切っている")

    step = config.ladder_steps[index]
    if step.base == "anchor":
        base_price = market.anchor
    else:
        base_price = state.ladder.last_fill_price_jpy
    if base_price is None:
        return _hold(name, f"{step.step} 段目の基準となる約定がまだない")

    limit_price = floor_price(
        base_price * (Decimal(1) - to_decimal(step.drop_pct) / Decimal(100)),
        spec.price_digits,
    )
    if limit_price <= 0:
        return _hold(name, "指値価格を算出できない")

    pending_notional = sum(
        (o.price * o.amount for o in state.pending_buy), Decimal(0)
    )
    budget = min(
        to_decimal(step.budget_jpy),
        to_decimal(config.per_order_max_jpy),
        to_decimal(config.ladder_total_budget_jpy)
        - state.ladder.used_budget_jpy
        - pending_notional,
        to_decimal(config.initial_jpy) * to_decimal(config.max_position_ratio)
        - state.position.cost_basis_jpy
        - pending_notional,
        state.account.cash_available_jpy
        - to_decimal(config.initial_jpy) * to_decimal(config.min_cash_reserve_ratio),
    )
    if budget <= 0:
        return _hold(name, "リスク制約により使える予算が残っていない")

    amount = buy_amount(budget, limit_price, spec)
    if amount <= 0:
        return _hold(name, "刻みに満たないため発注しない")

    return Decision(
        action=BUY,
        state=name,
        reason=(
            f"{step.step} 段目の買い指値を置く"
            f"（基準 {step.base} = {base_price}、-{step.drop_pct}%）"
        ),
        place=(
            PlaceOrder(
                side="buy",
                order_type="limit",
                amount=amount,
                price=limit_price,
                label=f"step-{step.step}",
            ),
        ),
    )


def decide(
    config: Config,
    guards: Guards,
    market: Market | None,
    spec: PairSpec | None,
    state: State | None,
    now: datetime,
) -> Decision:
    """観測と状態から、この回の行動を決める。"""
    name = state_name(config, state)

    if state is None or market is None or spec is None:
        return _hold(name, "ペーパートレード口座または市場データを観測できていない")

    if config.skip_on_exchange_maintenance and not guards.exchange_ok:
        return _hold(name, f"取引所の状態が {guards.exchange_status} のため判断しない")
    if config.skip_on_circuit_break and not guards.circuit_ok:
        return _hold(name, f"サーキットブレイク（mode={guards.circuit_mode}）のため判断しない")
    if market.age_sec > config.max_data_age_sec:
        return _hold(name, f"データが {int(market.age_sec)} 秒前と古い")
    if not spec.is_enabled:
        return _hold(name, f"{config.pair} が取引可能な状態ではない")
    if state.position_mismatch:
        return _hold(name, "建玉と残高が一致しない。人間の確認が必要")

    if name == "HALTED":
        return _exit_all(
            config, spec, state, name,
            f"総資産が {state.account.drawdown_pct:.1f}% で強制手仕舞いの水準",
        )

    age_days = state.position.age_days
    if age_days is not None and age_days > config.time_stop_days:
        return _exit_all(
            config, spec, state, name,
            f"建玉の保有が {age_days:.1f} 日で上限を超えた",
        )

    desired = desired_sell_orders(config, spec, state)
    if desired and not _sells_match(state, desired):
        return Decision(
            action=SELL,
            state=name,
            reason="平均取得単価が動いたため利確の売り指値を置き直す",
            cancel=tuple(o.id for o in state.pending_sell),
            place=desired,
        )

    if name == "HIBERNATING":
        return _hold(
            name,
            f"総資産が {state.account.drawdown_pct:.1f}% のため新規買いを停止している",
        )

    return _next_buy(config, market, spec, state, name, now)
