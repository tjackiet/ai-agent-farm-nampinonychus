"""1周（観測 → 判断 → 発注 → 記録）のオーケストレーション。

エントリポイントはここだけとする。Claude Code から呼ぶのもこの1コマンドで、
`bitbank` コマンドを外側で組み立てない（docs/IMPLEMENTATION_PLAN.md Phase 6）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from . import (
    cli,
    config as config_module,
    decide as decide_module,
    journal,
    narrate as narrate_module,
    notify as notify_module,
    observe,
    performance as performance_module,
    state as state_module,
    summary as summary_module,
    veto as veto_module,
)
from . import timeutil
from .orders import Executor, execute


@dataclass
class Cycle:
    """1周の結果。JSON にしてそのまま標準出力へ流す。"""

    run_id: str
    action: str
    state: str
    reason: str
    dry_run: bool
    orders: list[dict[str, Any]]
    sources: list[dict[str, str]]
    warnings: list[str]
    error: str | None = None
    status_written: bool = False
    veto: dict | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "action": self.action,
            "state": self.state,
            "reason": self.reason,
            "dry_run": self.dry_run,
            "orders": self.orders,
            "sources": self.sources,
            "warnings": self.warnings,
            "error": self.error,
            "status_written": self.status_written,
            "veto": self.veto,
        }


def _observe_account(client: cli.Client, cfg: config_module.Config, now: datetime, last_price):
    """ペーパー口座を観測して状態を導出する。

    `paper tick` が返す `filled` は「前回からこの回までに約定したもの」。
    通知に使うため、最初の観測のぶんだけ拾っておく。
    """
    tick = client.paper_tick().data
    assets = client.paper_assets().data
    pnl = client.paper_pnl(cfg.pair).data
    orders = client.paper_active_orders().data
    history = client.paper_trade_history().data
    if not isinstance(assets, list) or not isinstance(orders, list) or not isinstance(history, list):
        raise ValueError("ペーパー口座の応答が配列ではありません")
    derived = state_module.derive(
        config=cfg,
        now=now,
        last_price=last_price,
        assets_rows=assets,
        pnl_report=pnl if isinstance(pnl, dict) else {},
        order_rows=orders,
        history_rows=history,
    )
    trades = state_module.parse_trades(history, cfg.pair, cfg.timezone)
    filled = tick.get("filled", []) if isinstance(tick, dict) else []
    return derived, trades, filled if isinstance(filled, list) else []


def _narrate(
    cfg: config_module.Config,
    repo_root: Path | None,
    client: cli.Client,
    narrator: narrate_module.Writer | None,
) -> None:
    """日誌の空欄を埋める。失敗しても運用は続ける（空欄のまま残るだけ）。"""
    if not cfg.narrate_enabled:
        return
    writer = narrator or narrate_module.writer_for(cfg)
    try:
        narrate_module.fill_unwritten(cfg, writer, repo_root)
    except Exception as exc:  # noqa: BLE001 - 言語化の失敗で発注を止めない
        client.warnings.append(f"所感を書けませんでした: {type(exc).__name__}")


def _review(
    cfg: config_module.Config,
    client: cli.Client,
    decision: decide_module.Decision,
    market: observe.Market | None,
    state: state_module.State | None,
    now: datetime,
    repo_root: Path | None,
    reviewer: narrate_module.Writer | None,
) -> veto_module.Veto:
    """買いを出す前に、止める権利だけを LLM に諮る。

    諮る対象でない回（HOLD・売り・取消だけ）はここで何も呼ばない。
    書き手を作れないこと自体も失敗として扱い、`veto.on_failure` に従う。
    """
    if not cfg.veto_enabled or not veto_module.is_reviewable(decision):
        return veto_module.SKIPPED
    try:
        writer = reviewer or narrate_module.build_writer(cfg.veto_llm)
    except Exception as exc:  # noqa: BLE001 - 書き手を作れない場合も失敗として扱う
        result = veto_module.Veto(
            consulted=True,
            stopped=cfg.veto_on_failure == "hold",
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
    else:
        records = journal.read_recent(cfg, now, cfg.veto_read_last_n, repo_root)
        result = veto_module.review(
            cfg, decision, market, state, now, writer, records
        )
    if result.error is not None:
        client.warnings.append(f"拒否権を諮れませんでした: {result.error}")
    return result


def run_once(
    cfg: config_module.Config,
    client: cli.Client,
    now: datetime,
    *,
    force_dry_run: bool = False,
    repo_root: Path | None = None,
    notifier: notify_module.Poster | None = None,
    narrator: narrate_module.Writer | None = None,
    reviewer: narrate_module.Writer | None = None,
) -> Cycle:
    dry_run = cfg.dry_run or force_dry_run
    run_id = timeutil.to_iso(now)
    # 経過時間は単調時計で測る。now は判断に使う時刻であり、実行時間ではない。
    started = time.monotonic()

    guards = observe.Guards(exchange_status=None, circuit_mode=None)
    market = None
    spec = None
    derived = None
    trades: tuple[state_module.Trade, ...] = ()
    fills: list[dict] = []
    error: str | None = None
    # 通知の差分をとるため、スナップショットを上書きする前に読む。判断には使わない。
    previous = notify_module.read_previous(cfg, repo_root)

    stopped_hours: float | None = None
    try:
        guards = observe.check_guards(client, cfg)
        market = observe.observe_market(client, cfg, now)
        spec = observe.observe_pair_spec(client, cfg)
        # ペーパー口座に触る前に測る。tick も lazy tick も lastTickAt を進めるため。
        stopped_hours = state_module.stopped_hours(cfg, now, repo_root)
        derived, trades, fills = _observe_account(client, cfg, now, market.last)
    except cli.CliError as exc:
        error = f"{exc}（{exc.cmd}）"
    except (ValueError, KeyError, TypeError) as exc:
        error = f"観測を解釈できません: {exc}"

    if error is not None:
        decision = decide_module.Decision(
            action=decide_module.HOLD,
            state=decide_module.state_name(cfg, derived),
            reason=f"観測に失敗したため何もしない: {error}",
        )
    else:
        elapsed = time.monotonic() - started
        if elapsed > cfg.max_runtime_sec:
            decision = decide_module.Decision(
                action=decide_module.HOLD,
                state=decide_module.state_name(cfg, derived),
                reason=f"観測に {int(elapsed)} 秒かかり、実行時間の上限を超えた",
            )
        else:
            decision = decide_module.decide(
                cfg, guards, market, spec, derived, now, stopped_hours
            )

    veto = veto_module.SKIPPED
    if error is None:
        veto = _review(
            cfg, client, decision, market, derived, now, repo_root, reviewer
        )
        decision = veto_module.apply(decision, veto)

    order_records: list[dict[str, Any]] = []
    if error is None and (decision.cancel or decision.place):
        executor = Executor(client=client, pair=cfg.pair, dry_run=dry_run)
        try:
            order_records, aborted = execute(executor, decision.cancel, decision.place)
            if aborted is not None:
                decision = decide_module.Decision(
                    action=decide_module.HOLD,
                    state=decision.state,
                    reason=aborted,
                )
        except cli.CliError as exc:
            error = f"{exc}（{exc.cmd}）"
            decision = decide_module.Decision(
                action=decide_module.HOLD,
                state=decision.state,
                reason=f"発注または取消に失敗した: {error}",
            )

    # 実際に注文を出した回は、スナップショットを取り直す。
    if error is None and not dry_run and order_records:
        try:
            derived, trades, _ = _observe_account(client, cfg, now, market.last)
        except (cli.CliError, ValueError, KeyError, TypeError) as exc:
            error = f"発注後の観測に失敗した: {exc}"

    record = journal.build_record(
        run_id=run_id,
        state_label=decision.state,
        pair=cfg.pair,
        market=market,
        position_amount=float(derived.position.amount) if derived else None,
        avg_cost=float(derived.position.avg_cost_jpy)
        if derived and derived.position.avg_cost_jpy is not None
        else None,
        action=decision.action,
        reason=decision.reason,
        orders=order_records,
        sources=client.sources,
        warnings=client.warnings,
        error=error,
        veto=veto.as_dict() if veto.consulted else None,
    )
    journal.append(cfg, now, record, root=repo_root)

    status_written = False
    if error is None:
        document = state_module.build_status(
            config=cfg,
            now=now,
            run_id=run_id,
            state_label=decision.state,
            market=market,
            state=derived,
            trades=trades,
            action=decision.action,
            reason=decision.reason,
            price_source=market.source_cmd if market is not None else None,
        )
        root = repo_root if repo_root is not None else config_module.REPO_ROOT
        # 書き出し先は Git 管理外。リポジトリの status.yaml は見本として触らない。
        state_module.write_status(document, root / cfg.status_output)
        status_written = True

    # 記録の集約。判断そのものには影響しないため、失敗しても HOLD にはしない。
    performance_doc: dict | None = None
    # 判断ログを書いたあとに読む。この回の記録も集計と通知に含めるため。
    all_records = performance_module.all_records(cfg, repo_root)
    if error is None and derived is not None:
        try:
            summary_module.ensure(cfg, now, trades, repo_root)
            _narrate(cfg, repo_root, client, narrator)
            performance_doc = performance_module.build(cfg, now, all_records, trades)
            performance_module.write(
                performance_doc,
                (repo_root if repo_root is not None else config_module.REPO_ROOT)
                / cfg.performance_output,
            )
        except OSError as exc:
            client.warnings.append(f"記録を書けませんでした: {exc}")

    # 通知。送れなくても判断には影響させない。
    messages = notify_module.build_messages(
        config=cfg,
        now=now,
        previous=previous,
        decision_state=decision.state,
        fills=fills,
        orders=order_records,
        records=all_records,
    )
    if performance_doc is not None and notify_module.crossed_report_times(
        cfg, previous.at, now
    ):
        report = notify_module.build_report(cfg, now, performance_doc, derived)
        if cfg.narrate_enabled:
            try:
                writer = narrator or narrate_module.writer_for(cfg)
                remark = narrate_module.comment(cfg, writer, report, repo_root)
                if remark:
                    report = f"{report}\n> {remark}"
            except Exception as exc:  # noqa: BLE001 - 一言が書けなくても送る
                client.warnings.append(f"一言を書けませんでした: {type(exc).__name__}")
        messages.append(report)
    failure = notify_module.send(cfg, messages, poster=notifier)
    if failure:
        client.warnings.append(failure)

    return Cycle(
        run_id=run_id,
        action=decision.action,
        state=decision.state,
        reason=decision.reason,
        dry_run=dry_run,
        orders=order_records,
        sources=[s.as_dict() for s in client.sources],
        warnings=list(client.warnings),
        error=error,
        status_written=status_written,
        veto=veto.as_dict() if veto.consulted else None,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m nampinonychus.run",
        description="ナンピノニクスの判断を1周ぶん実行する（ペーパートレードのみ）",
    )
    parser.add_argument("--config", default=None, help="agent.yaml のパス")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="発注せず、組み立てた注文だけを出力する（agent.yaml より安全側にのみ倒せる）",
    )
    args = parser.parse_args(argv)

    try:
        cfg = config_module.load(args.config)
    except config_module.ConfigError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    now = timeutil.now(cfg.timezone)
    client = cli.Client(config=cfg)
    cycle = run_once(cfg, client, now, force_dry_run=args.dry_run)
    print(json.dumps(cycle.as_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
