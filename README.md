# AI Agent Farm — Nampinonychus

ナンピノニクスは、価格下落時の買いを得意とする投資AIエージェントです。

AIエージェントが `bitbank-lab-cli` を利用して市場データを取得し、ペーパートレードによるフォワード運用を行う実験プロジェクトです。

## 目的

このリポジトリでは、以下を検証します。

1. AIエージェントがCLIを利用して自律的に市場を分析できるか
2. 性格・戦略・記憶方式が売買判断にどう影響するか
3. ペーパートレードの実績を継続的に記録・可視化できるか
4. AIエージェントの改善を `bitbank-lab-cli` の改善につなげられるか

## ナンピノニクス

ナンピノニクスは、下落局面を買い場として捉える逆張り・ナンピン型のエージェントです。

性格、戦略、リスク制約、記憶方式をそれぞれ独立したファイルで管理します。

## ファイル構成

```text
.
├── CLAUDE.md
├── README.md
├── agent.yaml
├── status.yaml
├── visual-profile.yaml
├── mood-rules.yaml
├── personality.md
├── strategy.md
├── risk-policy.md
├── memory-policy.md
├── requirements.txt
├── nampinonychus/          # エージェント本体（Python 3）
├── tests/
├── records/
│   └── performance.sample.yaml
├── scripts/
│   ├── export_agent_package.py
│   └── launchd/            # 定期実行の雛形（macOS）
├── examples/
│   └── nampinonychus.sample.agent.json
└── docs/
    ├── PROJECT_ROADMAP.md
    ├── REPOSITORY_PLAN.md
    ├── IMPLEMENTATION_PLAN.md
    └── DEVELOPMENT_PLAN.md
```

| ファイル          | 役割                                                             |
| ----------------- | ---------------------------------------------------------------- |
| `CLAUDE.md`       | 毎回必ず守る不変のルール                                         |
| `agent.yaml`      | エージェント定義。**数値パラメータとバージョンの唯一の正**       |
| `status.yaml`     | 状態のスキーマの見本。実行では書き換えない（実行時は `var/status.yaml`） |
| `visual-profile.yaml` | 表示用プロフィール。性格・傾向・特性・技の静的データ         |
| `mood-rules.yaml` | 成績連動エモートの判定ルール。実績から `normal` / `down` / `up` を決める |
| `records/performance.sample.yaml` | ペーパートレード実績のサンプル。**実際の運用結果ではない** |
| `requirements.txt` | Python の依存。PyYAML のみ                                      |
| `nampinonychus/`  | エージェント本体。観測・判断・発注・記録（Phase 6）               |
| `tests/`          | 判断ロジックとガードのテスト                                     |
| `scripts/export_agent_package.py` | 表示用パッケージ（`*.agent.json`）のエクスポート処理 |
| `examples/nampinonychus.sample.agent.json` | サンプル実績で生成した表示用パッケージ。**生成物であり手で編集しない** |
| `personality.md`  | 性格・行動原則・話し方                                           |
| `strategy.md`     | 判断ロジック。買い下がりの階段と決済条件                         |
| `risk-policy.md`  | リスク制約。性格と矛盾した場合はこちらが優先                     |
| `memory-policy.md`| 記憶の構造と書き込みルール                                       |

各 `.md` は「なぜその設計なのか」を説明する文書です。値が `agent.yaml` と食い違う場合は `agent.yaml` を優先します。

`docs/` には、企画上の位置づけと計画をまとめています。

| 文書                          | 役割                                             |
| ----------------------------- | ------------------------------------------------ |
| `docs/PROJECT_ROADMAP.md`     | AIエージェントファーム企画全体の地図             |
| `docs/REPOSITORY_PLAN.md`     | 本リポジトリの担当範囲。**実装範囲の唯一の正**   |
| `docs/IMPLEMENTATION_PLAN.md` | 今後の全体設計と実装順序。**実装順序の正**       |
| `docs/DEVELOPMENT_PLAN.md`    | 構想段階の旧計画（記録として保存）               |

本リポジトリが担当するのは、企画のうち**ナンピノニクス1個体ぶん**です。

## 設計の要点

- **アンカー（直近2時間の高値）より上では買わない。** 上昇を追いかけません。
- **5段の階段で買い下がる。** 合計は初期資金の 60% まで。6段目はありません。
- **最悪ケースを設計で確定させる。** 総資産 -15% で新規買いを停止、-25% で全建玉を手仕舞いして停止します。
- **判断に迷ったら HOLD。** 例外・データ欠損・取引所異常時は発注しません。

## バージョンと進化

エージェントは `major.minor` の2桁でバージョン管理します（例：`v1.0` / `v1.4` / `v2.0`）。

現在のナンピノニクスは **v1.0** です。

**バージョンの唯一の正は `agent.yaml` の `version` です。**

`version` と `schema_version` は別のものです。名前で意味が確定するよう、次の構成に統一しています。

| 項目             | 意味                     | 持つファイル                                              |
| ---------------- | ------------------------ | --------------------------------------------------------- |
| `version`        | エージェント本体のバージョン | `agent.yaml` **のみ**                                     |
| `schema_version` | ファイル形式の版         | すべての YAML（`agent.yaml` / `visual-profile.yaml` / `status.yaml` / `mood-rules.yaml` / `records/performance.sample.yaml`） |

| 更新    | 変更の内容                                                                                                                   |
| ------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `major` | 戦略の基本構造を変更した／記憶方式を大きく変更した／判断フロー・利用ツールを刷新した／主要な能力や技を追加した／キャラクターを次の形態へ進化させる |
| `minor` | プロンプトを調整した／売買条件やリスク値を調整した／記憶件数などを調整した／既存機能を改善した／不具合を修正した               |

README やコメントの修正など、判断へ影響しない変更ではバージョンを更新しません。

### キャラクター進化

**`major` の値がそのままキャラクターの進化段階です。** 段階の番号を別に持たせません。`visual-profile.yaml` の `evolution` は、現在の形態の説明（`stage_name` / `description` / `appearance`）だけを持ちます。

**`v1.x` は必ず初期形態です。** 初期形態は、小さく、未成熟で弱そうで、少し不格好な個体として描きます。完成された強者には見えず、今後の進化余地を感じられることを条件とします。

**バージョンは強さや運用成績を表しません。**

キャラクター画像は形態ごとに分けて置きます。パスは `visual-profile.yaml` の `character` に `character/v{major}/normal.webp` の形で定義し、`{major}` は `agent.yaml` の `version` から取ります。

次の2つは分離して扱います。

- **キャラクター進化** — アップデートやメンテナンスによる外見の変化。`major` の更新で進む。
- **成績連動エモート** — 同じバージョン内での `normal` / `down` / `up` の表情変化。

### 表示側の扱い

HTML ステータス画面は、`agent.yaml` の `version` から `major` を読み取り、進化形態とキャラクター画像のパスを決めます。表示のために進化段階の番号を別途持たせません。

## 成績連動エモート

同じ形態のなかでの表情変化（`normal` / `down` / `up`）は、ペーパートレードの**実績**から決めます。

`status.yaml` の稼働状態（`IDLE` / `LADDERING` / `HIBERNATING` など）は判定に使いません。稼働状態と成績は別のものとして扱います。

### データフロー

```text
ペーパートレード実績
  → performance データ（records/performance.sample.yaml）
  → mood-rules.yaml で判定
  → normal / down / up
  → visual-profile.yaml の character 画像を選択
```

判定結果はどのファイルにも保存しません。表示側が毎回算出します。

| ファイル                          | 役割                                                   |
| --------------------------------- | ------------------------------------------------------ |
| `records/performance.sample.yaml` | 実績データ（損益率・ドローダウン・連勝連敗など）を持つ |
| `mood-rules.yaml`                 | 実績から `normal` / `down` / `up` を判定する基準を持つ |
| `visual-profile.yaml`             | 判定結果に対応するキャラクター画像のパスを持つ         |

### 判定ルール

**判定基準の唯一の正は `mood-rules.yaml` です。** `down` → `up` → `normal` の順に評価し、最初に該当したものを結果とします。

| 結果     | 条件（いずれか1つでも満たせば該当）                                    |
| -------- | ---------------------------------------------------------------------- |
| `down`   | 直近24時間の損益率が -2% 以下／3連敗以上／現在のドローダウンが 5% 以上 |
| `up`     | `down` に該当せず、直近24時間の損益率が +2% 以上／3連勝以上            |
| `normal` | 上のいずれにも該当しない                                               |

判定に使うドローダウンは `current_drawdown_from_peak_pct` です。**この値は、過去最高資産から現在までの下落率を正の数で表します**（`4.8` なら 4.8% の下落）。

`status.yaml` の `account.drawdown_pct` は初期資金比をマイナスで表す別の値です。意味・符号・基準点のいずれも異なるため、同じ名前を使いません。

| 項目                             | 持つファイル       | 基準点       | 符号                     |
| -------------------------------- | ------------------ | ------------ | ------------------------ |
| `current_drawdown_from_peak_pct` | performance データ | 過去最高資産 | 下落を正の数で表す       |
| `account.drawdown_pct`           | `status.yaml`      | 初期資金     | 下落をマイナスで表す     |

### サンプル実績

`records/performance.sample.yaml` は、表示側との接続を確認するための固定値です。

**実際の運用結果ではありません。** `source: sample` がその印です。現在の値は `mood-rules.yaml` では `down` と判定されます。

### 実運用時の扱い

**実績データはローカルで更新します。15分ごとに GitHub へコミットしません。**

リポジトリが持つのは判定ルールとサンプルであり、稼働中の実績そのものはリポジトリの更新頻度と切り離します。

## 定期実行（macOS / launchd）

15分ごとに自動で回します。雛形は `scripts/launchd/local.nampinonychus.plist` です。

まず `bitbank` と `node` の**両方**が入っているディレクトリを求めます。
`bitbank` は `#!/usr/bin/env node` で起動するため、`node` も PATH に必要です。

```bash
cd ~/ai-agent-farm-nampinonychus
mkdir -p var

NODE_BIN="$(python3 -c "import os,shutil;print(os.path.dirname(os.path.realpath(shutil.which('node'))))")"
ls "$NODE_BIN/bitbank" "$NODE_BIN/node"
```

**`which bitbank` の結果をそのまま使ってはいけません。** fnm / nvm / volta などの
バージョン管理ツールは、シェルごとに使い捨てのディレクトリ
（例：`.../fnm_multishells/8655_1787064548028/bin`）を PATH に挿します。
そのシェルを閉じると消えるため、launchd から `bitbank` が見つからなくなり、
毎回 HOLD するだけの状態になります。`realpath` で実体まで解決してください。

launchd と同じ環境（環境変数なし）で動くかを、先に確かめられます。

```bash
env -i PATH="$NODE_BIN:/usr/bin:/bin" bitbank status --format=json --machine | head -c 60
```

`{"success":true` が出れば大丈夫です。登録します。

```bash
sed -e "s|__REPO__|$PWD|g" \
    -e "s|__PATH__|$NODE_BIN:/usr/bin:/bin:/usr/sbin:/sbin|g" \
    scripts/launchd/local.nampinonychus.plist \
    > ~/Library/LaunchAgents/local.nampinonychus.plist

launchctl load ~/Library/LaunchAgents/local.nampinonychus.plist
```

確認と停止:

```bash
launchctl list | grep nampinonychus     # 動いているか
tail -f var/run.log                     # 判断を1行ずつ眺める
launchctl unload ~/Library/LaunchAgents/local.nampinonychus.plist   # 止める
```

- **`PATH` を明示するのは必須です。** launchd の既定の `PATH` には npm の
  グローバル配置先が含まれず、`bitbank` が見つかりません。
- **Node のバージョンを上げたら、登録し直してください。** `PATH` に
  バージョン番号が含まれるためです。
- **スリープ中は動きません。** ノートの蓋を閉じれば止まり、復帰後に一度だけ実行されます。
  停止していた間の判断は補完しません。
- **24時間を超えて止まった場合は、自動で復帰処理が走ります。**
  未約定の指値をすべて取り消し、その回は何もしません
  （`risk-policy.md`「運用を中断したあとの復帰」）。
- `var/run.log` は1回あたり1行（約1KB）です。放置すると増え続けるので、
  ときどき消してください。判断の記録は `var/memory/decisions/` に残ります。

## 表示用パッケージ

Claude Desktop の HTML Artifact へは、正本の YAML とキャラクター画像を1つの JSON（`*.agent.json`）にまとめて渡します。生成は `scripts/export_agent_package.py` が行います（「セットアップ」の仮想環境を使います）。

```bash
# サンプル（records/performance.sample.yaml を使用）→ examples/ へ
.venv/bin/python scripts/export_agent_package.py --sample

# 実運用（records/performance.yaml を使用）→ dist/ へ（Git 管理外）
.venv/bin/python scripts/export_agent_package.py
```

- JSON は正本から生成される派生物です。手で直すのは常に正本側とし、JSON は再生成します。
- キャラクター画像が存在しない間は、`assets` の各値は `null` になります（エラーにしません）。
- エモート判定に `status` は使いません。判定は `performance` と `mood_rules` だけで行います。
- 構造の詳細は [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) の「6. Artifact への入力を1ファイルにまとめる」を参照してください。

## 利用するツール

市場データの取得とペーパートレードには、以下のCLIを利用します。

https://github.com/bitbankinc/bitbank-lab-cli

```bash
npm i -g bitbank-lab-cli
bitbank paper init --jpy=1000000
```

ペーパー口座の状態ファイルは `var/paper-state.json`（Git 管理外）に置きます。
場所の正は `agent.yaml` の `cli.state_path` で、環境変数
`BITBANK_PAPER_STATE_PATH` として CLI へ渡します。

`var/` には運用の産物（ペーパー口座・スナップショット・判断ログ）がまとまります。
バックアップは別途行ってください。

## セットアップ

必要なのは Python 3.9 以上と PyYAML だけです。判断ロジックとテストは
標準ライブラリだけで動きます。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

以降のコマンドは `.venv/bin/python` で実行します（`source .venv/bin/activate`
してから `python3` でも同じです）。

Homebrew や OS 付属の Python では、`pip install` が
`externally-managed-environment` で拒否されます（PEP 668）。仮想環境を作るのが
確実です。

## エージェントの実行

観測 → 判断 → 発注 → 記録を1周させます。**エントリポイントはこの1コマンドだけ**です。

```bash
.venv/bin/python -m nampinonychus.run            # agent.yaml の runtime.dry_run に従う
.venv/bin/python -m nampinonychus.run --dry-run  # 発注せず、組み立てた注文だけを出力する
```

- 結果は判断1件ぶんの JSON として標準出力へ出ます。
- 判断ログは `var/memory/decisions/{date}.jsonl` へ追記されます。HOLD でも必ず残します。
- スナップショットは `var/status.yaml` へ、成功した回だけ書き出します。
  途中で失敗した回は更新しません。
- **運用の産物はすべて `var/` 配下（Git 管理外）です。** 実行しても作業ツリーは汚れません。
  リポジトリ直下の `status.yaml` はスキーマの見本として固定です。
- `--dry-run` は **agent.yaml より安全側にのみ**倒せます。実際に発注させるときは
  `agent.yaml` の `runtime.dry_run` を人間が `false` にします。
- 判断は決定的なコードで行い、LLM は関与しません。呼び出し側が `bitbank`
  コマンドを組み立てることもしません。

テストの実行:

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

## 現在の開発段階

エージェント本体（Phase 6）を実装し、`runtime.dry_run: true` のまま
1周できる状態です。実際にペーパー注文を出すのは、人間が `dry_run` を
`false` にし、`agent.phase` を `paper` へ更新してからです。
（現在の段階は `agent.yaml` の `agent.phase` が示します）

今後の実装順序は [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) を参照してください。

## 注意事項

このプロジェクトは実験目的です。

実際の資金による取引は行わず、ペーパートレードのみを対象とします。

本リポジトリの内容は、投資助言や利益を保証するものではありません。
