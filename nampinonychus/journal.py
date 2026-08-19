"""判断ログの追記。

action が HOLD でも必ず1行残す。何もしなかったことも判断である
（memory-policy.md「短期：判断ログ」）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

from . import timeutil
from .cli import Source
from .config import Config, REPO_ROOT


def path_for(config: Config, now: datetime, root: Path | None = None) -> Path:
    base = root if root is not None else REPO_ROOT
    return base / config.decisions_path.format(date=timeutil.date_key(now))


def append(
    config: Config,
    now: datetime,
    record: dict,
    root: Path | None = None,
) -> Path:
    target = path_for(config, now, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
        handle.write("\n")
    return target


def recorded_dates(config: Config, root: Path | None = None) -> list[str]:
    """判断ログが残っている日付を古い順に返す。"""
    base = root if root is not None else REPO_ROOT
    directory = (base / config.decisions_path.format(date="x")).parent
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.jsonl"))


def read_day(config: Config, date: str, root: Path | None = None) -> list[dict]:
    """その日の判断ログを読む。壊れている行は飛ばす。"""
    base = root if root is not None else REPO_ROOT
    path = base / config.decisions_path.format(date=date)
    if not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def build_record(
    *,
    run_id: str,
    state_label: str,
    pair: str,
    market: object | None,
    position_amount: float | None,
    avg_cost: float | None,
    action: str,
    reason: str,
    orders: Sequence[dict],
    sources: Sequence[Source],
    warnings: Sequence[str] = (),
    error: str | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "state": state_label,
        "pair": pair,
        "price": float(market.last) if market is not None else None,
        "anchor": float(market.anchor) if market is not None else None,
        "drop_from_anchor_pct": round(float(market.drop_from_anchor_pct), 2)
        if market is not None
        else None,
        "position": {"amount": position_amount, "avg_cost": avg_cost},
        "action": action,
        "reason": reason,
        "orders": list(orders),
        "sources": [s.as_dict() for s in sources],
        "warnings": list(warnings),
        "error": error,
    }
