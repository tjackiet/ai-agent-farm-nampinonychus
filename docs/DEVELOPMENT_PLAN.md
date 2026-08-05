# Nampinonychus Development Plan

> **この文書の役割**：構想段階でまとめた**旧開発計画**。記録として残す。
> **現在の実装順序の正は [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) である。**
> 本書の段階・順序・将来構想を、現時点の作業対象と解釈しないこと。
> 実装してよい範囲は [`REPOSITORY_PLAN.md`](REPOSITORY_PLAN.md)、
> 必ず守るルールは [`../CLAUDE.md`](../CLAUDE.md) を参照する。

## 現在地

現在の段階は `agent.yaml` の `agent.phase` が示す（`design` | `paper` | `halted`）。

現在は **`design`**。性格・戦略・リスク制約・記憶方式の設計ファイルのみが存在し、
実行コードはまだない。

## 開発順序

前の段階が動いていない状態で、次の段階に進まない。

| 段階 | やること                             | 完了の目安                                             |
| ---- | ------------------------------------ | ------------------------------------------------------ |
| 1    | エージェント設定ファイルを整備する   | `agent.yaml` と4つの `.md` が矛盾なく揃っている        |
| 2    | CLI を使った1回の判断を実行する      | 手動実行で BUY / SELL / HOLD が1回決まる               |
| 3    | ペーパートレード注文まで接続する     | `bitbank paper create-order` まで到達する              |
| 4    | 判断と結果を保存する                 | `memory/decisions/{date}.jsonl` に1行追記される        |
| 5    | 定期実行に対応する                   | 15分ごとに無人で1サイクル回る                          |
| 6    | 運用実績を保存する                   | 日次サマリと `memory/lessons.md` が蓄積される          |
| 7    | ステータス JSON を生成する           | `status.yaml` から表示用データが出力される             |
| 8    | HTML ステータス画面を作る            | 1画面で現在の状態が読める                              |
| 9    | 成績連動エモートを実装する           | 状態と成績に応じて表情が変わる                         |

## 段階ごとの補足

### 段階 1 — 設定ファイルの整備

数値は `agent.yaml` に集約し、`.md` は理由の説明に徹する。
両者が食い違ったまま次の段階に進まない。

### 段階 2 — 1回の判断

`strategy.md`「15分ごとの処理」の 1〜5 までを手動で通す。
この段階では発注しない。判断結果を出力するところまで。

`strategy.md`「実装前に確認が必要な点」を、実際の CLI で確認するのもここ。

### 段階 3 — ペーパートレード注文

`agent.yaml` の `runtime.dry_run` を `false` にするのはこの段階。

**`dry_run: false` は「ペーパートレードで実際に発注する」という意味であり、
実資金の取引を許可するものではない。** 実取引の禁止は段階に関係なく不変
（[`../CLAUDE.md`](../CLAUDE.md)）。

あわせて `agent.yaml` の `agent.phase` を `design` から `paper` に更新する。

### 段階 4 — 判断と結果の保存

`memory-policy.md` の書き込みルールに従う。
出典のない数値は書かない。HOLD も必ず記録する。

### 段階 5 — 定期実行

`runtime.schedule`（15分ごと）で無人稼働させる。
1回の実行が `runtime.max_runtime_sec` を超えないことを確認する。
失敗時は `status.yaml` を更新しない（古い状態のほうが、嘘の状態より安全）。

### 段階 6 — 運用実績の保存

日次サマリ（23:50）と `memory/lessons.md`（建玉完結時）を稼働させる。
`memory/decisions/*.jsonl` の90日での削除もここで組み込む。

### 段階 7〜9 — 可視化

`status.yaml` を単一の入力として表示用データを作る。
表示のために新しい状態を持たない。エモートは `personality.md` の
状態別セリフと成績に対応させる。

## 将来構想（未着手・確定していない）

以下は**まだ着手しない**。[`REPOSITORY_PLAN.md`](REPOSITORY_PLAN.md) の
「扱うもの」に追加されて初めて作業対象になる。

- `btc_jpy` 以外の通貨ペアへの拡張
- 運用実績（`memory/lessons.md`）の蓄積を踏まえた戦略パラメータの見直し
- アンカー基準の再設計（段の基準を `last_fill` から `anchor` 累積下落率へ変更する案）
- `1hour` 足を使った短期判定の追加
- 他エージェントとの成績比較（比較の受け口は企画側の別リポジトリ）
- `bitbank-lab-cli` へ還元する改善点の整理と提案

戦略そのものを変えたくなった場合、エージェントは自分で `agent.yaml` を
書き換えない。`memory/lessons.md` に記録し、人間の判断を待つ。

## 関連文書

- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — 現在の実装順序（正）
- [`REPOSITORY_PLAN.md`](REPOSITORY_PLAN.md) — 実装してよい範囲（唯一の正）
- [`PROJECT_ROADMAP.md`](PROJECT_ROADMAP.md) — 企画全体の地図
- [`../CLAUDE.md`](../CLAUDE.md) — 毎回必ず守る不変のルール
