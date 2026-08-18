# Nampinonychus Implementation Plan

> **この文書の役割**：本リポジトリの**今後の全体設計と実装順序**（Phase 1〜9）。
> **本書が実装順序の正である**（[`../CLAUDE.md`](../CLAUDE.md) の文書役割表に対応）。
> 予定は進行に応じて変わるが、変更は本書を更新して行う。
> 実装してよい範囲は [`REPOSITORY_PLAN.md`](REPOSITORY_PLAN.md)、
> 必ず守るルールは [`../CLAUDE.md`](../CLAUDE.md) を参照する。
> [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md) は構想段階の旧計画として残る
> （同書の冒頭にも明記）。

## 1. このリポジトリの目的

このリポジトリでは、AIエージェントファームの最初の試験エージェント
「ナンピノニクス」を開発する。

ナンピノニクスは、将来的に `bitbank-lab-cli` を利用して市場データを取得し、
ペーパートレードによるフォワード運用を行う。**実取引は行わない。**

このリポジトリでは、以下を一体として管理する。

1. エージェントの性格
2. 売買戦略
3. リスク制約
4. 記憶方式
5. 静的なステータス
6. キャラクターデザイン
7. キャラクター画像
8. ペーパートレード実績
9. 成績連動エモート
10. Artifact へ渡す表示用パッケージ

## 2. 現在できているもの

| #   | 対象                              | 状態                                                       |
| --- | --------------------------------- | ---------------------------------------------------------- |
| 1   | `agent.yaml`                      | エージェント本体の設定とバージョンの唯一の正               |
| 2   | `personality.md`                  | 性格、判断姿勢、話し方                                     |
| 3   | `strategy.md`                     | BUY・SELL・HOLD の戦略                                     |
| 4   | `risk-policy.md`                  | 戦略より優先される安全制約                                 |
| 5   | `memory-policy.md`                | 記憶の保存・参照ルール                                     |
| 6   | `visual-profile.yaml`             | HTML ステータス画面で使う静的な表示情報                    |
| 7   | `status.yaml`                     | 口座、建玉、稼働状態などの動的状態（未稼働のため初期値）   |
| 8   | `mood-rules.yaml`                 | 実績から normal / down / up を判定する唯一の正             |
| 9   | `records/performance.sample.yaml` | エモート判定を確認するためのサンプル実績                   |
| 10  | Claude Desktop の HTML Artifact   | YAML を読み込み、ステータス表示とエモート判定ができる      |
| 11  | `scripts/export_agent_package.py` | 表示用パッケージのエクスポート処理（Phase 1）              |
| 12  | `examples/nampinonychus.sample.agent.json` | サンプル実績で生成した表示用パッケージ            |
| 13  | `nampinonychus/`                  | エージェント本体。観測 → 判断 → 発注 → 記録が1周する（Phase 6） |
| 14  | `tests/`                          | 判断ロジックとガードのテスト（`python3 -m unittest discover -s tests -t .`） |

Artifact はリポジトリ外（Claude Desktop 側）にある。

キャラクター画像はまだ存在しない。`visual-profile.yaml` の `character` に
パス（`character/v{major}/normal.webp` など）だけが定義されている。

## 3. 責務の分離

以下の責務を混同しない。

### エージェント設計

対象：`agent.yaml` / `personality.md` / `strategy.md` / `risk-policy.md` / `memory-policy.md`

AIエージェントがどのように判断するかを定義する。

### ステータス表示

対象：`visual-profile.yaml`

名前、性格タグ、戦略タグ、傾向、特性、技、信条、現在の進化形態を定義する。
キャラクター画像のパス（`character` セクション）も現行どおり本ファイルが持つ。

現在は人間または AI が確認して整備する静的データである（自動生成は Phase 9 で検討）。

### キャラクターデザイン

対象：`character-design.yaml`（今後追加。Phase 3）

キャラクターの外見を生成するための設計情報を持つ。

1. 生物モチーフ
2. 基本シルエット
3. 体格
4. 色
5. 模様
6. 固定すべき識別要素
7. 進化時に維持する要素
8. 進化時に変更できる要素
9. normal / down / up の表情・ポーズ方針

### キャラクター画像

保存場所：

```text
character/
  v1/
    normal.webp
    down.webp
    up.webp
```

画像は**バージョン資産として固定**する。HTML を表示するたびに再生成しない。
過去の形態の画像は消さない（`visual-profile.yaml` の方針どおり）。

### 実績とエモート

対象：`records/performance.*.yaml` / `mood-rules.yaml`

実績から normal / down / up を判定する。判定基準の唯一の正は `mood-rules.yaml`。

- 稼働状態（`status.yaml` の `state`：IDLE / LADDERING / HIBERNATING など）は判定に使わない。
- バージョン進化（`agent.yaml` `version` の major）と成績連動エモートは別概念である。
- 判定結果はどのファイルにも保存しない。表示側が毎回算出する。

### HTML Artifact

Artifact は以下だけを担当する。

1. 表示用データを読み込む
2. 実績からエモートを判定する
3. 対応するキャラクター画像を表示する

Artifact 自身はキャラクター画像を生成しない。

## 4. バージョンと進化

規則の詳細は [`../README.md`](../README.md)「バージョンと進化」を正とする。要点のみ再掲する。

- エージェントは `major.minor` 形式のバージョンを持つ。唯一の正は `agent.yaml` の `version`。
- **major 更新**：戦略、記憶、判断構造、主要機能が大きく変わったとき。
  major はそのままキャラクターの進化形態に対応する（例：v1.x → v2.0）。
- **minor 更新**：同じ形態のまま行う調整や改善
  （プロンプト調整／売買条件の調整／リスク値の調整／不具合修正）。
- **v1.x は必ず初期形態**。以下の特徴を持たせる。
  1. 小さい
  2. 未成熟で弱そう
  3. 少し不格好
  4. 不器用または不細工
  5. 完成された強者には見えない
  6. 今後の進化余地を感じられる
- バージョンは運用成績や強さを表さない。

## 5. キャラクター画像生成の方針

初期段階では**半手動**で行う。画像生成自体は AI を利用するが、
採用判断とリポジトリへの保存は当面人間が行う。

### v1 の初回生成

入力：

1. `agent.yaml`
2. `visual-profile.yaml`
3. `character-design.yaml`

手順：

1. まず normal 画像の候補を生成する
2. 人間が基準となる normal 画像を採用する
3. 採用済み normal 画像を参照して down と up を生成する
4. 人間が同一個体に見えることを確認する
5. 採用画像を `character/v1/` へ保存する

### v2 以降の進化生成

入力：

1. 最新のエージェント設定
2. 新しい `character-design.yaml`
3. 進化前の normal 画像
4. 必要に応じて進化前の down・up 画像

前形態と同じ個体だと分かる特徴（`character-design.yaml` の
「進化時に維持する要素」）を維持しながら進化させる。

## 6. Artifact への入力を1ファイルにまとめる

現在の Artifact は複数の YAML を個別に入力する必要があり、操作が煩雑である。
そこで、正本ファイルをまとめた**表示用パッケージ**（`*.agent.json`）を生成する。

### パッケージの構造

| キー                     | 内容                                                         |
| ------------------------ | ------------------------------------------------------------ |
| `package_schema_version` | パッケージ形式の版（正本 YAML の `schema_version` とは別）   |
| `generated_at`           | 生成時刻（ISO8601 / Asia/Tokyo）                             |
| `agent`                  | `agent.yaml` の内容                                          |
| `visual_profile`         | `visual-profile.yaml` の内容                                 |
| `status`                 | `status.yaml` の内容。口座・建玉・稼働状態の**表示に使う**   |
| `mood_rules`             | `mood-rules.yaml` の内容                                     |
| `performance`            | `records/performance.*.yaml` の内容                          |
| `assets`                 | `normal` / `down` / `up` のキャラクター画像（Data URL）。画像が無い間は `null` |

- 正本の内容は**変換・再解釈せず**、対応するセクションへそのまま保持する。
- **エモート判定に `status` は使わない。** 判定は `performance` と `mood_rules` だけで行う。
  `status` は表示専用である。
- この JSON は手で編集する正本ではなく、正本から生成される**派生物**である。
  手で直すのは常に正本側（YAML・画像）とし、JSON は再生成する。
- Artifact の利用者は、この1ファイルだけを選択する。

### 生成と保存場所

エクスポート処理は `scripts/export_agent_package.py`（Python 3 + PyYAML）。

| 用途     | コマンド                                           | 出力                                       | Git 管理                   |
| -------- | -------------------------------------------------- | ------------------------------------------ | -------------------------- |
| サンプル | `python3 scripts/export_agent_package.py --sample` | `examples/nampinonychus.sample.agent.json` | コミットする               |
| 実運用   | `python3 scripts/export_agent_package.py`          | `dist/nampinonychus.agent.json`            | **対象外**（`.gitignore`） |

- サンプルは `records/performance.sample.yaml` を使い、Claude Desktop の開発と動作確認に使う。
- 実運用の既定入力は `records/performance.yaml`（`--performance` で変更可能）。
  正式なファイル名は Phase 8 で確定する。
- 画像が存在しない場合もエラーにせず、`assets` の各値を `null` として出力する。

## 7. 開発順序

| Phase | やること                         | 完了の目安                                                     |
| ----- | -------------------------------- | -------------------------------------------------------------- |
| 1     | 表示用パッケージの生成           | YAML から `*.agent.json` が生成される（画像なしでも動作）      |
| 2     | Artifact の1ファイル入力対応     | Artifact が `*.agent.json` だけで現在と同じ表示・判定を行える  |
| 3     | キャラクターデザインの整備       | `character-design.yaml` と生成用指示文が揃う                   |
| 4     | キャラクター画像の生成           | 採用された3画像が `character/v1/` に置かれる                   |
| 5     | 画像込みパッケージの生成         | エモート判定に応じた画像が Artifact に表示される               |
| 6     | AIエージェント本体の実装         | CLI を使った判断〜ペーパー注文〜記録が1周する                  |
| 7     | 定期運用                         | 15分ごとの Agent Runner が無人で回る                           |
| 8     | 実績との自動接続                 | 実績から performance データと `*.agent.json` が再生成される    |
| 9     | ステータス鑑定の自動化（検討）   | ステータス候補・根拠・確信度が自動生成され、人間が確認できる   |

### Phase 1：表示用パッケージの生成

複数の YAML と画像から `*.agent.json` を生成するエクスポート処理を作る。
最初は画像が存在しなくても動作するようにする。
仕様・実行方法・保存場所は「6. Artifact への入力を1ファイルにまとめる」のとおり。

### Phase 2：Artifact の1ファイル入力対応

Claude Desktop 側の Artifact を、`*.agent.json` だけを読み込む UI へ変更する。
エモート判定と表示処理は現在の仕様を維持する。

### Phase 3：キャラクターデザインの整備

`character-design.yaml` を追加し、ナンピノニクス v1 の外見仕様を定義する。
画像生成用の指示文も整理する。

### Phase 4：キャラクター画像の生成

normal 画像を基準として down と up を生成する（手順は「5. キャラクター画像生成の方針」）。
採用した3画像を `character/v1/` へ保存する。

### Phase 5：画像込みパッケージの生成

3画像を Data URL として埋め込んだ `*.agent.json` を生成する。
Artifact で、実績から判定されたエモート画像が表示されることを確認する。

### Phase 6：AIエージェント本体の実装

ナンピノニクスが `bitbank-lab-cli` を使い、観測 → 判断 → 発注 → 記録を
一周できるようにする。定期実行は Phase 7 で扱う。本 Phase は
**「手で1回実行すれば1周する」**までを完了とする。

前提は `strategy.md`「15分ごとの処理」「実装で踏む CLI の仕様」と
`risk-policy.md`「発注と取消の競合」「運用を中断したあとの復帰」に従う。

#### 決めたこと

| 項目           | 決定                                                                 |
| -------------- | -------------------------------------------------------------------- |
| 実装言語       | Python 3（`scripts/export_agent_package.py` と揃える。依存は PyYAML のみ） |
| 判断の主体     | 決定的コード。**LLM は売買判断に関与しない**                         |
| 実行のさせ方   | Claude Code CLI から1コマンドを呼ぶ                                  |
| 状態の正       | `bitbank paper` の実測から毎回導出する（`memory-policy.md`）          |
| 状態ファイル   | `var/paper-state.json`（Git 管理外）                                 |

#### モジュール構成

| モジュール    | 責務                                                       |
| ------------- | ---------------------------------------------------------- |
| `config.py`   | `agent.yaml` の読み込みと検証。数値パラメータの入口はここだけ |
| `cli.py`      | `bitbank` コマンドの薄いラッパ（`--format=json --machine` 固定、出典と取得時刻を返す） |
| `observe.py`  | 市場・口座の観測をまとめた不変オブジェクトを作る            |
| `state.py`    | 現在状態の導出と `status.yaml` の書き出し                   |
| `decide.py`   | 観測と状態から BUY・SELL・HOLD を決める**純関数**           |
| `orders.py`   | 数量計算・丸め・発注・取消                                  |
| `journal.py`  | `memory/decisions/{date}.jsonl` への追記                    |
| `run.py`      | 1周のオーケストレーション（唯一のエントリポイント）         |

`decide.py` に I/O を入れない。入力は観測値と状態、出力は
「何を取り消し、何を発注し、なぜそうしたか」だけとする。
`risk-policy.md` の制約は、この関数のテストで担保する。
本番では滅多に起きない「データが5分より古い」「サーキットブレイク中」も、
テストなら確実に踏める。

#### 1周の流れ

| #   | やること                                                    | 失敗したとき                     |
| --- | ----------------------------------------------------------- | -------------------------------- |
| 1   | `status` / `circuit-break` でガード                          | HOLD を記録して終了              |
| 2   | `ticker` / `candles --type=1day` / `pairs` を観測し鮮度を検証 | HOLD                             |
| 3   | `paper tick`（`--pair` を付けない）で約定を解決               | HOLD                             |
| 4   | `assets` / `pnl` / `active-orders` / `trade-history` を観測   | HOLD                             |
| 5   | 現在状態を導出する                                           | 導出できなければ HOLD ＋ 人間へ  |
| 6   | `decide()` で BUY・SELL・HOLD を決める                       | —                                |
| 7   | 取消 → 発注の順に実行する                                    | 取消が「約定済み」ならその回は打ち切って HOLD |
| 8   | 判断ログを追記し、`status.yaml` を書き出す                   | 途中で失敗したら `status.yaml` を更新しない |

#### 状態の導出

階段の状態は `status.yaml` に覚えさせず、**毎回 CLI の実測から導出する**。
クラッシュ・二重起動・人間の手動介入があっても状態が壊れないためである。

| 状態                    | 導出方法                                                   |
| ----------------------- | ---------------------------------------------------------- |
| `ladder.step`           | 建玉がゼロだった時点以降の buy 約定件数                     |
| `last_fill_price`       | 直近 buy 約定の `fillPrice`                                 |
| `cooldown_until`        | 直近 buy 約定の `filledAt` ＋ 6時間                         |
| `fills_today`           | 当日（Asia/Tokyo）の約定件数                                |
| `opened_at` / `age_days` | 建玉がゼロから非ゼロになった約定の `filledAt`              |
| 平均取得単価・損益      | `paper pnl` の `avgCost` / `realizedPnl` / `unrealizedPnl`  |
| 未約定注文              | `paper active-orders`（`max_pending_buy_orders` の判定もこれで行う） |

実装で踏む点：

- `paper pnl` は **建玉ゼロかつ実現損益ゼロのペアを出力しない**。
  対象ペアが応答に無い場合は「建玉なし」として扱う。
- `avgCost` は**買い手数料を含む**。利確ラインはこの値を基準に計算する
  （手数料を回収したうえでの +3% / +6% になる）。
- 未約定注文の本数を `status.yaml` から数えない。数えると、発注直後に
  落ちた回の注文が次回に二重発注される。

#### Claude Code CLI からの実行

- 実行は `python3 -m nampinonychus.run` の**1コマンド**とする。
  Claude Code はこれを呼んで結果を読むだけとし、
  **Claude が `bitbank` コマンドを組み立てない。**
  組み立てを任せると、`risk-policy.md` の制約が確率的に破られる。
- 出力は判断1件ぶんの JSON（`action` / `reason` / `orders` / `sources`）。
  人間向けの言い換えは Claude Code 側で行ってよいが、
  **数値を作り直さない**（`memory-policy.md`）。
- `.claude/settings.json` の `permissions.deny` に禁止コマンドを登録し、
  `CLAUDE.md` の禁止事項をハーネス側でも二重化する。
  これはエージェント側のルールの置き換えではなく、二重化である。

#### dry_run の外しかた

1. `runtime.dry_run: true` のまま1周させ、組み立てた注文が正しいか人間が確認する
2. `runtime.dry_run` を `false`、`agent.phase` を `design` から `paper` へ人間が更新する
3. `bitbank paper init --jpy=1000000` で口座を初期化する

`version` は Phase 6 が一周するまで `1.0` に据え置く（本書「10. 未確定事項」）。

#### 実装して分かったこと

- **板に置ける買い指値は常に1本になる。** 2段目以降の指値価格は直前の約定価格から
  決まるため、1段目が約定するまで2段目の価格が確定しない。
  `max_pending_buy_orders` の 2 は上限として残る（`strategy.md`「ペース制御」）。
- **アンカーには当日の未確定足を含める。** 含めることで、7日高値の更新中は
  現在価格がアンカーと等しくなり `no_chase` が働く（`strategy.md`「アンカー価格」）。
- **経過時間は単調時計で測る。** 判断に使う時刻（`now`）と実行時間は別物であり、
  混ぜるとテストで時刻を注入できない。

#### テスト方針

CLI を叩くテストと、叩かないテストを分ける。

- **`decide()` の純関数テスト**：状態遷移（IDLE → LADDERING → HOLDING）、
  no_chase、クールダウン、当日約定上限、ドローダウン -15% / -25%、
  時間切れ手仕舞い、5段使い切り
- **数量計算**：`unit_amount` の丸め、手数料マージン、`per_order_max_jpy` 超過、
  `min_cash_reserve_ratio` 抵触
- **状態の導出**：`trade-history` のフィクスチャから段数・クールダウン・当日回数
- **ガード**：データが5分より古い、CLI がエラー終了、JSON が壊れている、
  サーキットブレイク中、`pnl` に対象ペアが出てこない
- **統合テスト**：実 CLI を使うものは1本だけ（`paper init` → `create-order` →
  `tick` → `assets`）。既定ではスキップし、明示的に有効化したときだけ走らせる

### Phase 7：定期運用

15分ごとに判断する Agent Runner を作る。

- 停止中の判断は後から補完しない。
- 実績はローカルで保存し、15分ごとに GitHub へコミットしない。
- 失敗時は `status.yaml` を更新しない（古い状態のほうが、嘘の状態より安全）。

### Phase 8：実績との自動接続

ペーパートレード実績から performance データを生成し、
最新の `*.agent.json` を再生成できるようにする。

### Phase 9：ステータス鑑定の自動化（後段で検討）

以下の設定ファイルを解析し、ステータス候補を自動生成する仕組みを検討する。

1. `personality.md`
2. `strategy.md`
3. `risk-policy.md`
4. `memory-policy.md`
5. `agent.yaml`

この処理では、各ステータスの値・根拠・確信度を出力し、
**人間が確認してから** `visual-profile.yaml` へ反映する
（エージェント自身が書き換えない、という [`../CLAUDE.md`](../CLAUDE.md) の
ルールはここでも変わらない）。

MVP の完成を妨げないよう、この自動鑑定機能は後段で実装する。

## 8. 変わらない前提

Phase の進行に関係なく、以下は常に守る（詳細は [`../CLAUDE.md`](../CLAUDE.md)）。

- 実取引は行わない。ペーパートレードのみ。
- 判断できないときは HOLD。
- 数値パラメータの正は `agent.yaml`。矛盾時は `risk-policy.md` が `personality.md` に勝つ。
- 戦略・リスク制約の値をエージェント自身が書き換えない。
- エモート判定の唯一の正は `mood-rules.yaml`。稼働状態を判定に使わない。
- 観測していない値を書かない。API キー等を残さない。

## 9. 既存文書との関係

| 文書                      | 本書との関係                                                                                             |
| ------------------------- | -------------------------------------------------------------------------------------------------------- |
| `../CLAUDE.md`            | 不変のルール。本書はこれに従う。文書役割表に本書が1行登録されており、計画本文は転記しない                |
| `REPOSITORY_PLAN.md`      | 実装範囲の唯一の正。本書の対象（キャラクターデザイン・画像・表示用パッケージ・エクスポート処理・将来のステータス鑑定）は「扱うもの」に記載済み |
| `DEVELOPMENT_PLAN.md`     | 構想段階の旧計画（記録として残す）。**実装順序の正は本書**                                               |
| `PROJECT_ROADMAP.md`      | 企画全体の地図。本書は項目5・6（可視化・エモート）の「自分のぶんのみ」の範囲内であり矛盾しない           |

### DEVELOPMENT_PLAN.md との対応

旧計画はエージェント実装（段階2〜6）→ 可視化（段階7〜9）の順だったが、
Artifact とエモート判定のデータ構造が先行して存在する現状に合わせ、本書は
可視化・パッケージ（Phase 1〜5）→ エージェント実装（Phase 6〜7）の順を正とする。
参考として、旧計画の段階との対応を示す。

| DEVELOPMENT_PLAN.md | 状態                     | 本書での対応                         |
| ------------------- | ------------------------ | ------------------------------------ |
| 段階1               | 完了                     | —（前提）                            |
| 段階2〜4            | 未着手                   | Phase 6                              |
| 段階5               | 未着手                   | Phase 7                              |
| 段階6               | 未着手                   | Phase 7〜8                           |
| 段階7〜8            | Artifact として先行実装  | Phase 1〜2（1ファイル入力へ置換）    |
| 段階9               | 判定基準は確定済み       | Phase 3〜5（画像の作成と表示接続）   |

## 10. 未確定事項

以下は本書では決めない。実装着手前に人間が判断する。

1. 実運用時の performance ファイルの正式な名前と置き場所
   （エクスポート処理の既定値は `records/performance.yaml`。Phase 8 で確定する。
   `records/performance.sample.yaml` はサンプル専用）
2. `memory/` 配下（判断ログ・日次サマリ・lessons）をどの頻度でコミットするか、ローカルのみとするか
3. 画像1枚あたりのサイズ目安（Data URL 埋め込みでパッケージが過大にならないための上限）
4. `character-design.yaml` の形式詳細（`schema_version` ほか。Phase 3 で確定）

以下は判断済みである（2026-08 時点）。

- 実装順序の正は本書（`DEVELOPMENT_PLAN.md` は旧計画として残す）
- `REPOSITORY_PLAN.md`「扱うもの」へ対象を追記済み
- `CLAUDE.md` の文書役割表に本書の行を追加済み（計画本文は転記しない）
- 表示用パッケージに `status` を**含める**（表示専用。エモート判定には使わない）
- 実運用の生成先は `dist/`（Git 管理外）、サンプルは `examples/` へコミット
- エクスポート処理は Python 3 + PyYAML（`scripts/export_agent_package.py`）
- Phase 6 の実装言語は Python 3（依存は PyYAML のみ）
- Phase 6 の売買判断は決定的コードで行い、LLM を関与させない。
  実行は Claude Code CLI から `python3 -m nampinonychus.run` の1コマンドを呼ぶ
- 階段の状態は `bitbank paper` の実測から毎回導出する。
  `status.yaml` は状態の正ではなくスナップショット（`memory-policy.md` を更新済み）
- ペーパートレードの状態ファイルは `var/paper-state.json`（Git 管理外）。
  `BITBANK_PAPER_STATE_PATH` で CLI へ渡す
- **`agent.yaml` の `version` は Phase 6 が一周するまで `1.0` に据え置く。**
  それまでの設定・文書の修正では minor を上げない。エージェントが一通り
  動くようになった時点で、あらためて版を判断する

## 関連文書

- [`REPOSITORY_PLAN.md`](REPOSITORY_PLAN.md) — 実装してよい範囲（唯一の正）
- [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md) — 従来の開発順序と将来構想
- [`PROJECT_ROADMAP.md`](PROJECT_ROADMAP.md) — 企画全体の地図
- [`../CLAUDE.md`](../CLAUDE.md) — 毎回必ず守る不変のルール
- [`../README.md`](../README.md) — リポジトリの概要、バージョンと進化、成績連動エモート
