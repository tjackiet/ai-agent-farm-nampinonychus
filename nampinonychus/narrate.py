"""記録の言語化。

日誌の「所感」と「学び」を、ナンピノニクス自身の言葉で書く。

**売買の判断には一切関与しない。** ここで生成するのは記録の文章だけで、
発注・取消・HOLD の決定は決定的なコードが行う。書けなかった場合は
「（未記入）」のまま残し、運用は何ごともなく続く。

数値を新しく作らせない。渡した本文にある値だけを使わせる
（memory-policy.md「観測しなかったことを書かない」「予測を事実として書かない」）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Sequence

from . import summary
from .config import Config, REPO_ROOT

# system, user を受け取って本文を返す。テストでは差し替える。
Writer = Callable[[str, str], str]

class NarrateError(RuntimeError):
    """言語化に失敗した。

    メッセージは記録に残してよい内容だけにする（`CLAUDE.md`「API キー・
    シークレット・プロファイル名は、ログにも記憶にも残さない」）。
    """


STYLE_RULES = """守ること:

- 渡された本文に書かれている数値だけを使う。新しい数値を作らない
- 予測を事実として書かない（「上がりそう」は根拠にならない）
- 損失を言い換えない。含み損は含み損、撤退は撤退と書く
- 助言や指示をしない。起きたことへの所感だけを書く
- 前置きをしない。1〜2文で書く
- 記号や箇条書きを使わない。地の文で書く"""


def _personality(root: Path | None = None) -> str:
    base = root if root is not None else REPO_ROOT
    path = base / "personality.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def build_prompt(kind: str, root: Path | None = None) -> str:
    """書き手としての指示。性格設定をそのまま渡す。"""
    what = {
        "daily": "運用日誌の「所感」",
        "lessons": "建玉が完結したときの「学び」",
        "report": "半日ごとの振り返りに添える一言",
    }.get(kind, "記録の一文")
    extra = ""
    if kind == "lessons":
        extra = (
            "\n- **次の判断を変えうることだけを書く。** その日の値動きへの感想は書かない"
            "\n- 戦略を変えたくなったら、そう書くだけにする。自分では変えない"
        )
    return (
        f"あなたはペーパートレードを行うエージェント「ナンピノニクス」です。\n"
        f"以下の性格設定に従って、{what}を書いてください。\n\n"
        f"{STYLE_RULES}{extra}\n\n"
        f"--- 性格設定 ---\n{_personality(root)}"
    )


def claude_code_writer(config: Config) -> Writer:
    """Claude Code CLI を非対話で呼ぶ。

    引かれ先は「headless かどうか」ではなく「何で認証されているか」で決まる。
    Claude Code のログイン（サブスクリプション）ならプランの利用枠から、
    環境に ANTHROPIC_API_KEY があればそちらが優先されて API クレジットから引かれる。

    `--bare` を付けると起動は軽く副作用もないが、keychain と OAuth を読まなくなるため
    ANTHROPIC_API_KEY が必須になる（= 必ず API 課金）。付けない場合は CLAUDE.md と
    フックを毎回読み込む。どちらを取るかは agent.yaml の narrate.bare で決める。
    """

    def write(system: str, user: str) -> str:
        argv = [
            config.narrate_command,
            "-p",
            "--output-format",
            "text",
            "--model",
            config.narrate_model,
            "--effort",
            config.narrate_effort,
            "--append-system-prompt",
            system,
        ]
        if config.narrate_bare:
            argv.insert(1, "--bare")
        try:
            proc = subprocess.run(  # noqa: S603
                argv,
                input=user,
                capture_output=True,
                text=True,
                timeout=config.narrate_timeout_sec,
                check=False,
            )
        except FileNotFoundError as exc:
            raise NarrateError(
                f"{config.narrate_command} が見つかりません。PATH を確認する"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise NarrateError(
                f"{config.narrate_command} が {config.narrate_timeout_sec} 秒で返らなかった"
            ) from exc
        if proc.returncode != 0:
            raise NarrateError(
                f"{config.narrate_command} が異常終了しました（終了コード {proc.returncode}）"
            )
        return (proc.stdout or "").strip()

    return write


def anthropic_writer(config: Config) -> Writer:
    """Anthropic API で書く。SDK か資格情報が無ければ ImportError / 例外。"""

    def write(system: str, user: str) -> str:
        import anthropic  # 判断ロジックから切り離すため、ここで読み込む

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=config.narrate_model,
            max_tokens=config.narrate_max_tokens,
            system=system,
            output_config={"effort": config.narrate_effort},
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

    return write


def writer_for(config: Config) -> Writer:
    """設定に応じた書き手を返す。"""
    if config.narrate_writer == "api":
        return anthropic_writer(config)
    if config.narrate_writer == "claude_code":
        return claude_code_writer(config)
    raise ValueError(f"narrate.writer が不正です: {config.narrate_writer}")


def _clean(text: str) -> str:
    """1行に収める。前置きや箇条書きの記号を落とす。"""
    line = " ".join(text.split())
    return re.sub(r"^[-*・\s]+", "", line)


def fill(text: str, writer: Writer, prompt: str) -> tuple[str, bool]:
    """本文中の「（未記入）」を埋める。書けなければそのまま返す。"""
    if summary.UNWRITTEN not in text:
        return text, False
    written = _clean(writer(prompt, text))
    if not written:
        return text, False
    return text.replace(summary.UNWRITTEN, written), True


def fill_unwritten(
    config: Config,
    writer: Writer,
    root: Path | None = None,
) -> list[Path]:
    """日誌と lessons の空欄を埋める。埋めたファイルを返す。"""
    if not config.narrate_enabled:
        return []
    base = root if root is not None else REPO_ROOT
    targets: list[tuple[Path, str]] = []

    if config.narrate_targets.get("daily"):
        directory = (base / config.daily_path.format(date="x")).parent
        if directory.is_dir():
            targets.extend((path, "daily") for path in sorted(directory.glob("*.md")))

    if config.narrate_targets.get("lessons"):
        lessons = base / config.lessons_path
        if lessons.is_file():
            targets.append((lessons, "lessons"))

    filled: list[Path] = []
    for path, kind in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if summary.UNWRITTEN not in text:
            continue
        updated, changed = fill(text, writer, build_prompt(kind, root))
        if changed:
            path.write_text(updated, encoding="utf-8")
            filled.append(path)
    return filled


def comment(config: Config, writer: Writer, report: str, root: Path | None = None) -> str:
    """半日レポートに添える一言。書けなければ空。"""
    if not config.narrate_enabled or not config.narrate_targets.get("report"):
        return ""
    return _clean(writer(build_prompt("report", root), report))
