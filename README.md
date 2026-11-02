# werewolf-llm-player

大規模言語モデル（LLM）にテキスト人狼をプレイさせるための実験コードです。
研究ノートのシリーズ「[LLMで人狼プレイヤーを作る](https://leonmfvo.github.io/werewolf-decision-ai/research/llm-player-design.html)」（全 4 回）で使ったものを、公開できる形に整理しました。

- 第 1 回：設計と、ローカル LLM から API へ
- 第 2 回：LLM 人狼は何を読み違えるのか
- 第 3 回：足し算の設計はなぜ失敗したか
- 第 4 回：対局ログで見る LLM 人狼

開発の途中経過をそのまま残したコードです。きれいに整理されたライブラリではありません。指示や後処理を足し続けた多層の設計（`think.py` / `think_free.py`）と、それをほぼ外した「素の LLM」モード（`raw`）の両方が入っています。両者の比較は第 3 回を参照してください。

## 動かし方

Python 3.11 以降。

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # Windows では setx ANTHROPIC_API_KEY ...
python selfplay.py                  # 手元の模擬の村（4A）で AI 同士を 1 試合戦わせる
python selfplay.py raw=1            # 主役を「素の LLM」モードで動かす
python selfplay.py players=3 human=あなた   # 自分も 1 席に入って対戦する
```

- `selfplay.py` は、ブラウザもサイトも使わずに、手元で模擬の村を動かします。オプションはファイル冒頭の説明を見てください。
- ログは `selfplay_logs/` に保存されます。出力の例を [`selfplay_logs_sample/`](selfplay_logs_sample) に 1 試合分入れています（AI 同士の試合で、人間の発言は含みません）。
- API の利用料金がかかります。1 試合あたりの目安は、多層の設計で約 1 ドル前後、素の LLM で数セントでした（開発時）。

## ファイル

| ファイル | 内容 |
|---|---|
| `think.py` / `think_free.py` | 本体。プロンプトの組み立て、考える係と話す係、読む係→話す係（3 案）→検証の段の多層処理、後処理、素の LLM モード（`think_free.py` の `raw` / `simple`） |
| `selfplay.py` | 手元の模擬の村。AI 同士、または人間 1 人を交えて対戦する |
| `board.py` | ログから CO・判定結果を集計する |
| `archive.py` | 見たログの保存と検索 |
| `bench.py` / `read_test.py` | 実戦の場面での返答の比較（ベンチマーク）と、4 択の読解テスト |
| `build_style_stats.py` / `build_playbook.py` / `extract_flow.py` | 手本にしたプレイヤーの発言から、文体の統計・考え方の手引き・流れの例を作る |
| `progress_4a.txt` / `principles_4a.json` / `reasoning_guide.txt` / `glossary.txt` / `progress.txt` | 4A の考え方、原則の台帳、推理の手順、用語集、進行の知識 |
| `style_guide.txt` / `examples_*.txt` / `style_stats.json` | 人格ごとの話し方の指定と例文、文体の統計 |
| `raw_prompt.txt` | 素の LLM モードで足す短い指示 |
| `village_rules*.txt` | 村のルール（例）と、AI が守ること |
| `lessons.txt` | 試合の振り返りから採用した教訓 |
| `zinro.py` | サイトとのつなぎ口。**公開版では中身を外しています**（下記） |

## 公開版で外したもの

- **サイトの自動操作の実装**（`zinro.py` の中身と、ログの保存・画面調査のスクリプト）。特定のサイト専用のボットの作り方になるため、インターフェースだけを残しています。`think.py` をオンラインの村で動かすには、使う環境に合わせて `zinro.py` を実装する必要があります。`selfplay.py` はこれがなくても動きます。
- **実際の対局ログと、そこから作ったデータ**（流れの例、場面のベンチマーク、読解テストの問題など）。他のプレイヤーの発言が含まれるためです。`bench.py` / `read_test.py` / `extract_flow.py` などは、自分で集めたログから同じ形式のデータを作れば使えます。
- 一部の人格の例文。他のプレイヤーの話し方をもとにしたもので、本人の了解を得ていないため外しました（`persona=rewrite` は例文なしで動きます）。

`examples_suguretenai.txt` / `examples_seishun.txt` などは、プレイヤーの すぐれてない さんの話し方を手本にしています。本人の了解を得て公開しています。

## 注意

- オンラインの村で動かすときは、サイトの利用規約を守り、運営者と同じ村の参加者の了解を得てください。開発時は、運営者に確認したうえで、参加者に AI が参加していることと、発言が外部の API に送られることを伝えて対局しました。
- 他のプレイヤーのログを集めて使う場合は、その人の了解を得てください。

## ライセンス

MIT License（[LICENSE](LICENSE)）
