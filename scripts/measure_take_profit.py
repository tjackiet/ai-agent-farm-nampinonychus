#!/usr/bin/env python3
"""利確幅を決めるための測定。**読み取りだけを行い、発注はしない。**

`strategy.md` の利確幅（+0.3% / +0.6%）は、運用前のバックテスト
（2026-07-19〜08-18 の15分足）で決めた値である。本スクリプトは同じ問いを
**運用後の実データ**でやり直す。

見るもの:

1. 閉じたラウンドの実績（実現損益・保有時間・段数）
2. 到達率 — 平均取得単価からの各上昇幅に、何時間以内に何割が届いたか
3. 反実仮想 — 2段目の利確幅を変えていたら、いくつが利確で閉じ、
   いくつが保有上限の成行手仕舞いになっていたか

数値の出どころ:

- 価格 … 判断ログ（`var/memory/decisions/*.jsonl`）に残る各回の観測価格
- 約定 … `bitbank paper trade-history`（読み取りのみ）

**この結果は戦略の値を決めない。** 値を決めるのは人間であり、本スクリプトは
その材料を出すだけである（CLAUDE.md「戦略・リスク制約の値をエージェント自身が
書き換えない」）。

読むときの前提:

- 価格は15分ごとの**観測値**であって高値ではない。到達率は**下限**になる。
- 平均取得単価はラウンド確定後の値を使う。段が増える途中では実際はもっと高い。
- 資金が拘束されることで次のエントリーが変わる影響は**織り込んでいない**。
  利確幅を広げれば建玉は長く残り、拾えたはずの下げを逃す。
- 指値が板で必ず約定するとは限らない。到達＝約定ではない。

使い方:
    python3 scripts/measure_take_profit.py
    python3 scripts/measure_take_profit.py --levels 0.3,0.5,0.8,1.0,1.5
    python3 scripts/measure_take_profit.py --trades var/trade-history.json

依存: Python 3.9 以降 / PyYAML
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nampinonychus import cli as cli_module  # noqa: E402
from nampinonychus import config as config_module  # noqa: E402
from nampinonychus import observe as observe_module  # noqa: E402
from nampinonychus import performance as performance_module  # noqa: E402
from nampinonychus import state as state_module  # noqa: E402
from nampinonychus import timeutil  # noqa: E402
from nampinonychus.state import Round, Trade  # noqa: E402

Point = "tuple[datetime, Decimal]"


def price_points(records: Sequence[dict], tz_name: str) -> list:
    """判断ログから (時刻, 観測価格) を古い順に取り出す。

    価格が無い回（観測に失敗した回）は落とす。書かれていない値は作らない。
    """
    points = []
    for record in records:
        price = record.get("price")
        run_id = record.get("run_id")
        if not isinstance(price, (int, float)) or not isinstance(run_id, str):
            continue
        try:
            at = timeutil.from_iso(run_id, tz_name)
        except ValueError:
            continue
        points.append((at, Decimal(str(price))))
    points.sort(key=lambda p: p[0])
    return points


def between(points: Sequence, start: datetime, end: datetime) -> list:
    """start より後、end 以下の点。"""
    return [p for p in points if start < p[0] <= end]


def first_at_or_above(points: Sequence, target: Decimal):
    """target 以上になった最初の点。届かなければ None。"""
    for at, price in points:
        if price >= target:
            return (at, price)
    return None


def last_at_or_before(points: Sequence, end: datetime):
    """end 以下で最後に観測した点。無ければ None。"""
    seen = None
    for at, price in points:
        if at <= end:
            seen = (at, price)
    return seen


def config_timeline(records: Sequence[dict], tz_name: str) -> list:
    """判断ログから (時刻, version, 指紋) を古い順に取り出す。

    設定を変えた前後のラウンドを混ぜないために使う。指紋の無い古い記録は None。
    """
    timeline = []
    for record in records:
        run_id = record.get("run_id")
        if not isinstance(run_id, str):
            continue
        try:
            at = timeutil.from_iso(run_id, tz_name)
        except ValueError:
            continue
        conf = record.get("config")
        conf = conf if isinstance(conf, dict) else {}
        timeline.append((at, conf.get("version"), conf.get("strategy")))
    timeline.sort(key=lambda row: row[0])
    return timeline


def config_at(timeline: Sequence, when: datetime):
    """その時刻に動いていた設定。分からなければ (None, None)。"""
    seen = (None, None)
    for at, version, strategy in timeline:
        if at > when:
            break
        seen = (version, strategy)
    return seen


def round_trades(round_: Round, trades: Sequence[Trade]) -> list:
    """そのラウンドに属する約定。ラウンドは時間で重ならないので範囲で切れる。"""
    end = round_.closed_at
    return [
        t
        for t in trades
        if t.filled_at >= round_.opened_at and (end is None or t.filled_at <= end)
    ]


def last_buy_at(round_: Round, trades: Sequence[Trade]):
    """そのラウンドで最後に買った時刻。平均取得単価が確定した時点。"""
    buys = [t.filled_at for t in round_trades(round_, trades) if t.side == "buy"]
    return max(buys) if buys else None


def gain_pct(price: Decimal, avg_cost: Decimal) -> Decimal:
    if avg_cost <= 0:
        return Decimal(0)
    return (price - avg_cost) / avg_cost * Decimal(100)


def reach_table(
    rounds_: Sequence[Round],
    trades: Sequence[Trade],
    points: Sequence,
    levels: Sequence[Decimal],
    windows_h: Sequence[int],
) -> list:
    """各ラウンドが、最後の買いから何時間以内にどこまで戻したか。"""
    table = []
    for window_h in windows_h:
        row = {"window_h": window_h, "total": 0, "hit": {level: 0 for level in levels}}
        for round_ in rounds_:
            start = last_buy_at(round_, trades)
            if start is None:
                continue
            seen = between(points, start, start + timedelta(hours=window_h))
            if not seen:
                continue
            row["total"] += 1
            best = max(price for _, price in seen)
            for level in levels:
                if gain_pct(best, round_.avg_cost_jpy) >= level:
                    row["hit"][level] += 1
        table.append(row)
    return table


def simulate(
    round_: Round,
    trades: Sequence[Trade],
    points: Sequence,
    tp1_pct: Decimal,
    tp1_ratio: Decimal,
    tp2_pct: Decimal,
    time_stop_days: float,
    taker_fee_rate: Decimal,
) -> dict:
    """2段目の利確幅を変えた場合の、そのラウンドの出口を見積もる。

    1段目で `tp1_ratio` を売り、残りは 2段目か保有上限のどちらか早い方で出る。
    指値は maker 0%、保有上限の手仕舞いだけ成行（taker）とする。

    **利確は指値の価格ちょうどで約定したものとして数える。** 板に置いた指値は
    その価格で約定するのであって、そのとき市場がどこまで飛んでいたかは手取りに
    関係しない。観測価格で数えると、15分の間に大きく動いた回だけ不当に儲かった
    ことになる（実績は毎回きっかり +0.45% で、指値価格での約定を示している）。
    成行になる保有上限のときだけ、そのときの観測価格を使う。
    """
    avg = round_.avg_cost_jpy
    start = last_buy_at(round_, trades)
    stop_at = round_.opened_at + timedelta(days=time_stop_days)
    if start is None or avg <= 0 or stop_at <= start:
        return {"decided": False}

    seen = between(points, start, stop_at)
    if not seen:
        return {"decided": False}

    amount = round_.amount
    proceeds = Decimal(0)
    remaining = amount
    exit_kind = "time_stop"

    tp1_price = avg * (Decimal(1) + tp1_pct / Decimal(100))
    first = first_at_or_above(seen, tp1_price)
    if first is not None:
        sold = amount * tp1_ratio
        proceeds += sold * tp1_price  # 指値の価格で約定する
        remaining -= sold
        seen = [p for p in seen if p[0] >= first[0]]

    tp2_price = avg * (Decimal(1) + tp2_pct / Decimal(100))
    second = first_at_or_above(seen, tp2_price)
    if second is not None:
        proceeds += remaining * tp2_price  # 同上
        remaining = Decimal(0)
        exit_kind = "take_profit"
    else:
        last = last_at_or_before(seen, stop_at)
        if last is None:
            return {"decided": False}
        # 保有上限は成行。ここだけ taker がかかる。
        proceeds += remaining * last[1] * (Decimal(1) - taker_fee_rate)
        remaining = Decimal(0)

    return {
        "decided": True,
        "exit_kind": exit_kind,
        "tp1_hit": first is not None,
        "pnl_jpy": proceeds - avg * amount,
        "cost_jpy": avg * amount,
    }


def _pad(text: str, width: int, align: str = ">") -> str:
    """表の桁を揃える。全角は2桁として数える。"""
    shown = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    space = " " * max(0, width - shown)
    return space + text if align == ">" else text + space


def _fmt_pct(value: Decimal, digits: int = 2) -> str:
    return f"{float(value):+.{digits}f}%"


def _fmt_jpy(value: Decimal) -> str:
    return f"{round(float(value)):,}"


def read_trades(args, cfg) -> tuple:
    """約定履歴を読む。--trades があればそのファイル、無ければ CLI。"""
    if args.trades:
        rows = json.loads(Path(args.trades).read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("data", rows.get("trades", []))
        return state_module.parse_trades(rows, cfg.pair, cfg.timezone), None

    client = cli_module.Client(config=cfg)
    rows = client.paper_trade_history().data
    if not isinstance(rows, list):
        raise SystemExit("ペーパー口座の約定履歴が配列ではありません")
    try:
        spec = observe_module.observe_pair_spec(client, cfg)
    except Exception:  # noqa: BLE001 - 手数料が取れなくても既定値で続ける
        spec = None
    return state_module.parse_trades(rows, cfg.pair, cfg.timezone), spec


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 scripts/measure_take_profit.py",
        description="利確幅を決めるための測定（読み取りのみ。発注しない）",
    )
    parser.add_argument("--agent-config", default=None, help="agent.yaml のパス")
    parser.add_argument("--root", default=None, help="判断ログを読む基準ディレクトリ")
    parser.add_argument("--trades", default=None, help="約定履歴の JSON（省略時は CLI）")
    parser.add_argument(
        "--levels",
        default="0.3,0.6,0.8,1.0,1.5,2.0",
        help="測る上昇幅（%%）。カンマ区切り",
    )
    parser.add_argument("--windows", default="24,72", help="測る時間（h）。カンマ区切り")
    parser.add_argument("--config", dest="config_fp", default=None,
                        help="この戦略の指紋のラウンドだけを見る（設定ごとに分けて測る）")
    parser.add_argument("--since", default=None, help="この時刻より後に始まったラウンドだけ")
    parser.add_argument("--until", default=None, help="この時刻より前に始まったラウンドだけ")
    parser.add_argument("--tp1", default=None, help="1段目の利確幅（%%）。既定は agent.yaml")
    parser.add_argument(
        "--taker-fee", default=None, help="成行の手数料率（例 0.0012）。既定は CLI の pairs"
    )
    args = parser.parse_args(argv)

    cfg = config_module.load(args.agent_config)
    root = Path(args.root) if args.root else None
    records = performance_module.all_records(cfg, root)
    points = price_points(records, cfg.timezone)
    trades, spec = read_trades(args, cfg)

    levels = [Decimal(v.strip()) for v in args.levels.split(",") if v.strip()]
    windows = [int(v.strip()) for v in args.windows.split(",") if v.strip()]

    tp_levels = sorted(cfg.take_profit, key=lambda t: t.level)
    tp1_pct = Decimal(str(args.tp1)) if args.tp1 else Decimal(str(tp_levels[0].gain_pct))
    tp1_ratio = Decimal(str(tp_levels[0].sell_ratio))
    if args.taker_fee is not None:
        taker = Decimal(str(args.taker_fee))
    elif spec is not None:
        taker = spec.taker_fee_rate_quote
    else:
        taker = Decimal("0.0012")

    all_rounds = state_module.rounds(trades)
    closed = [r for r in all_rounds if r.is_closed]

    print(f"判断ログ {len(records)} 件（価格のある回 {len(points)} 件）")
    if points:
        print(f"期間 {timeutil.to_iso(points[0][0])} 〜 {timeutil.to_iso(points[-1][0])}")
    print(f"約定 {len(trades)} 件 / ラウンド {len(all_rounds)} 件（閉じた {len(closed)} 件）")
    print(f"いまの利確幅 {tp_levels[0].gain_pct}%（{tp_levels[0].sell_ratio:.0%}）"
          f" → {tp_levels[-1].gain_pct}%（残り全量）"
          f" / 保有上限 {cfg.time_stop_days} 日 / 成行 {float(taker) * 100:.2f}%")

    if not closed:
        print("\n閉じたラウンドがありません。測定できるのは決済まで終わったぶんだけです。")
        return 0

    timeline = config_timeline(records, cfg.timezone)
    owner = {id(r): config_at(timeline, r.opened_at) for r in closed}

    if args.since:
        since = timeutil.from_iso(args.since, cfg.timezone)
        closed = [r for r in closed if r.opened_at >= since]
    if args.until:
        until = timeutil.from_iso(args.until, cfg.timezone)
        closed = [r for r in closed if r.opened_at <= until]
    if args.config_fp:
        closed = [r for r in closed if owner[id(r)][1] == args.config_fp]
    if not closed:
        print("\n絞り込みの結果、閉じたラウンドが残りませんでした。")
        return 0

    print("\n=== 0. どの設定のラウンドか ===")
    seen: dict = {}
    for round_ in closed:
        seen.setdefault(owner[id(round_)], []).append(round_)
    for (version, strategy), group in sorted(seen.items(), key=lambda kv: str(kv[0])):
        name = f"{strategy}（v{version}）" if strategy else "指紋なし（記録を足す前）"
        pnl = sum((r.realized_pnl_jpy for r in group), Decimal(0))
        print(
            f"  {name}: {len(group)} ラウンド / "
            f"{timeutil.to_iso(min(r.opened_at for r in group))} 〜 "
            f"{timeutil.to_iso(max(r.closed_at or r.opened_at for r in group))} / "
            f"{_fmt_jpy(pnl)} JPY"
        )
    if len(seen) > 1:
        print("  ※ 設定が違うラウンドが混ざっています。下の数字は別の戦略を平均したものです。")
        print("     --config <指紋> か --since で切り分けてください。")

    print("\n=== 1. 閉じたラウンドの実績 ===")
    returns = [r.return_pct for r in closed]
    hours = [
        (r.closed_at - r.opened_at).total_seconds() / 3600 for r in closed if r.closed_at
    ]
    total_pnl = sum((r.realized_pnl_jpy for r in closed), Decimal(0))
    print(f"実現損益の合計 {_fmt_jpy(total_pnl)} JPY")
    print(
        f"1ラウンドの損益率 中央値 {_fmt_pct(statistics.median(returns))} / "
        f"最小 {_fmt_pct(min(returns))} / 最大 {_fmt_pct(max(returns))}"
    )
    print(f"勝ち {sum(1 for r in returns if r > 0)} / 負け {sum(1 for r in returns if r < 0)}")
    if hours:
        print(
            f"保有時間 中央値 {statistics.median(hours):.1f} 時間 / "
            f"最大 {max(hours):.1f} 時間"
        )
    print(f"段数 中央値 {statistics.median([r.steps for r in closed]):.0f} 段")

    print("\n=== 2. 到達率（最後の買いから何時間以内に、平均取得単価から何%戻したか）===")
    table = reach_table(closed, trades, points, levels, windows)
    print("  " + _pad("幅", 6, "<") + "".join(_pad(f"{w}時間以内", 16) for w in windows))
    for level in levels:
        cells = ""
        for row in table:
            total, hit = row["total"], row["hit"][level]
            cells += _pad(f"{hit / total:.0%} ({hit}/{total})" if total else "—", 16)
        print("  " + _pad(f"+{float(level):.1f}%", 6, "<") + cells)
    print("  ※ 15分ごとの観測値なので、足の高値で測るより低めに出ます（下限）")

    print("\n=== 3. 2段目の利確幅を変えていたら ===")
    print(f"  1段目は {float(tp1_pct):.1f}%（{float(tp1_ratio):.0%}）のまま、2段目だけ動かした場合")

    # 検算。いまの設定で回して実績と合わなければ、模型が実際の約定を再現できて
    # いない。合わない数字で戦略を決めないため、先に出して警告する。
    tp2_now = Decimal(str(tp_levels[-1].gain_pct))
    usable = [
        (r, x)
        for r, x in (
            (
                r,
                simulate(
                    r, trades, points, tp1_pct, tp1_ratio, tp2_now, cfg.time_stop_days, taker
                ),
            )
            for r in closed
        )
        if x["decided"]
    ]
    if usable:
        est = sum((x["pnl_jpy"] for _, x in usable), Decimal(0))
        real = sum((r.realized_pnl_jpy for r, _ in usable), Decimal(0))
        print(
            f"  検算 いまの +{float(tp2_now):.1f}% で回すと {_fmt_jpy(est)} JPY / "
            f"実績 {_fmt_jpy(real)} JPY（同じ {len(usable)} ラウンド）"
        )
        gap = abs(est - real)
        if gap > max(abs(real) * Decimal("0.05"), Decimal(1)):
            print("  ※ 実績と合いません。模型が実際の約定を再現できていないので、")
            print("     下の表は判断に使わないでください。")
    print(
        "  "
        + _pad("2段目", 7, "<")
        + _pad("利確で決済", 12)
        + _pad("保有上限で成行", 16)
        + _pad("概算の実現損益", 18)
        + _pad("コスト比", 10)
    )
    judged = 0
    for level in levels:
        decided = [
            x
            for x in (
                simulate(
                    r, trades, points, tp1_pct, tp1_ratio, level, cfg.time_stop_days, taker
                )
                for r in closed
            )
            if x["decided"]
        ]
        if not decided:
            print("  " + _pad(f"+{float(level):.1f}%", 7, "<") + "判定できる回がありません")
            continue
        judged = len(decided)
        by_tp = sum(1 for x in decided if x["exit_kind"] == "take_profit")
        pnl = sum((x["pnl_jpy"] for x in decided), Decimal(0))
        cost = sum((x["cost_jpy"] for x in decided), Decimal(0))
        ratio = pnl / cost * Decimal(100) if cost > 0 else Decimal(0)
        print(
            "  "
            + _pad(f"+{float(level):.1f}%", 7, "<")
            + _pad(str(by_tp), 12)
            + _pad(str(len(decided) - by_tp), 16)
            + _pad(f"{_fmt_jpy(pnl)} JPY", 18)
            + _pad(_fmt_pct(ratio), 10)
        )
    print(f"  ※ 判定できたのは {judged} / {len(closed)} ラウンド（価格の記録が無い区間は除外）")
    print("  ※ 資金が拘束されて次の下げを拾えなくなる影響は入っていません")
    print("  ※ 到達＝約定ではありません。板に指値が残っていた前提の概算です")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
