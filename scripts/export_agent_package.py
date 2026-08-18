#!/usr/bin/env python3
"""表示用エージェントパッケージ（*.agent.json）を生成する。

正本（agent.yaml / visual-profile.yaml / status.yaml / mood-rules.yaml /
records/performance.*.yaml とキャラクター画像）を、Claude Desktop の
HTML Artifact が読み込む1つの JSON へまとめる。

- 正本の内容は変換・再解釈せず、対応するセクションへそのまま保持する。
- 生成される JSON は派生物であり、手で編集しない。直すのは常に正本側。
- キャラクター画像が存在しない間は assets を null で出力する（エラーにしない）。
- エモート判定は行わない。判定は表示側が performance と mood_rules で行う
  （status は判定に使わない。docs/IMPLEMENTATION_PLAN.md「6.」）。

使い方:
    python3 scripts/export_agent_package.py --sample
        records/performance.sample.yaml から examples/<agent_id>.sample.agent.json を生成する
    python3 scripts/export_agent_package.py
        records/performance.yaml から dist/<agent_id>.agent.json を生成する（Git 管理外）
    python3 scripts/export_agent_package.py --performance <path> --out <path>
        入力の performance と出力先を個別に指定する

依存: Python 3.9 以降 / PyYAML（python3 -m pip install pyyaml）
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "PyYAML が見つかりません。`python3 -m pip install pyyaml` を実行してください。",
        file=sys.stderr,
    )
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]

# パッケージ形式の版。正本 YAML の schema_version やエージェントの version とは別。
PACKAGE_SCHEMA_VERSION = 1

# 正本ファイル。パッケージのセクション名 → リポジトリ内のパス。
# performance だけは実行時に選ぶ（サンプル or 実運用）。
SOURCE_FILES = {
    "agent": "agent.yaml",
    "visual_profile": "visual-profile.yaml",
    "status": "status.yaml",
    "mood_rules": "mood-rules.yaml",
}

# 実運用のスナップショット（Git 管理外）。存在すればリポジトリの status.yaml より優先する。
# リポジトリの status.yaml はスキーマの見本であり、運用中の値は入っていない。
RUNTIME_STATUS = "var/status.yaml"

MIME_TYPES = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_yaml(path: Path):
    if not path.is_file():
        sys.exit(f"エラー: 正本ファイルがありません: {rel(path)}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_character_paths(agent, visual_profile) -> dict:
    """visual-profile.yaml の character パスの {major} を agent.yaml の version で解決する。"""
    version = (agent or {}).get("version")
    if not isinstance(version, str) or "." not in version:
        sys.exit('エラー: agent.yaml の version（例: "1.0"）を読み取れません。')
    major = version.split(".")[0]

    character = (visual_profile or {}).get("character")
    if not isinstance(character, dict):
        sys.exit("エラー: visual-profile.yaml に character セクションがありません。")

    paths = {}
    for key in ("normal", "down", "up"):
        template = character.get(key)
        if not isinstance(template, str):
            sys.exit(f"エラー: visual-profile.yaml の character.{key} がありません。")
        paths[key] = template.replace("{major}", major)
    return paths


def to_data_url(path: Path):
    """画像ファイルを Data URL にする。画像が存在しなければ None（エラーにしない）。"""
    if not path.is_file():
        return None
    mime = MIME_TYPES.get(path.suffix.lower())
    if mime is None:
        sys.exit(f"エラー: 対応していない画像形式です: {rel(path)}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def now_jst_iso() -> str:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Tokyo")
    except Exception:
        tz = timezone(timedelta(hours=9))  # tzdata が無い環境向けの固定オフセット
    return datetime.now(tz).isoformat(timespec="seconds")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="表示用エージェントパッケージ（*.agent.json）を生成する"
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="records/performance.sample.yaml から examples/ 配下へサンプルを生成する",
    )
    parser.add_argument(
        "--performance",
        type=Path,
        default=None,
        help="performance YAML のパス（既定: records/performance.yaml、--sample 時は records/performance.sample.yaml）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="出力先（既定: dist/<agent_id>.agent.json、--sample 時は examples/<agent_id>.sample.agent.json）",
    )
    args = parser.parse_args()

    sections = {key: load_yaml(REPO_ROOT / name) for key, name in SOURCE_FILES.items()}

    # サンプル以外では、実行が書き出したスナップショットがあればそちらを使う。
    runtime_status = REPO_ROOT / RUNTIME_STATUS
    if not args.sample and runtime_status.is_file():
        sections["status"] = load_yaml(runtime_status)
        print(f"status は {rel(runtime_status)} を使います")

    if args.performance is not None:
        performance_path = (
            args.performance
            if args.performance.is_absolute()
            else REPO_ROOT / args.performance
        )
    elif args.sample:
        performance_path = REPO_ROOT / "records/performance.sample.yaml"
    else:
        performance_path = REPO_ROOT / "records/performance.yaml"
        if not performance_path.is_file():
            sys.exit(
                "エラー: records/performance.yaml がありません"
                "（実運用の実績はまだ生成されていません）。\n"
                "サンプルを生成する場合は --sample を指定してください。"
            )
    performance = load_yaml(performance_path)

    agent_id = (sections["agent"].get("agent") or {}).get("id")
    if not agent_id:
        sys.exit("エラー: agent.yaml の agent.id を読み取れません。")

    if args.out is not None:
        out_path = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    elif args.sample:
        out_path = REPO_ROOT / "examples" / f"{agent_id}.sample.agent.json"
    else:
        out_path = REPO_ROOT / "dist" / f"{agent_id}.agent.json"

    character_paths = resolve_character_paths(
        sections["agent"], sections["visual_profile"]
    )
    assets = {key: to_data_url(REPO_ROOT / p) for key, p in character_paths.items()}

    # キーの並びは docs/IMPLEMENTATION_PLAN.md「パッケージの構造」に合わせる。
    package = {
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "generated_at": now_jst_iso(),
        "agent": sections["agent"],
        "visual_profile": sections["visual_profile"],
        "status": sections["status"],
        "mood_rules": sections["mood_rules"],
        "performance": performance,
        "assets": assets,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(package, f, ensure_ascii=False, indent=2)
        f.write("\n")

    missing = [key for key, value in assets.items() if value is None]
    note = f"（画像なし: {', '.join(missing)}）" if missing else ""
    print(f"生成しました: {rel(out_path)} {note}".rstrip())


if __name__ == "__main__":
    main()
