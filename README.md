# 📊 102_market_phase — 市場フェーズ判定

日経・TOPIX・VIX・先物・ドル円などを総合スコアリングし、
現在の市場フェーズを判定して Discord に通知、`market_phase.json` をリポジトリに保存します。
他の戦略リポジトリ（201〜204）はこのファイルを参照してエントリー可否を判断します。

## フェーズ一覧

| フェーズ | 意味 |
|---|---|
| `BULL` | 積極運用相場 |
| `NEUTRAL` | 方向感なし・選別投資 |
| `REBOUND` | 底打ち反転の兆し |
| `RISK_OFF` | 地合い悪化・守り優先 |
| `WARN` | CRASH 予備軍（スコア 2/6） |
| `CRASH` | パニック相場（スコア 3/6 以上） |

## スケジュール

| 時刻 (JST) | 目的 |
|---|---|
| 08:11（平日） | 寄り付き前の最終確認 |
| 22:39（平日） | 米国市場開始後のボラ確認 |

## Secrets

| 名前 | 内容 |
|---|---|
| `DISCORD_WEBHOOK` | Discord の Webhook URL |

## 出力ファイル

`market_phase.json` — 各戦略ボットが PAT_TOKEN 経由で参照

```json
{
  "phase": "BULL",
  "description": "🟢【良好】積極運用相場",
  "stats": { "vix": 18.2, "crash_score": "0/6" }
}
```

## ファイル構成

```
102_market_phase/
├── emergency_sentinel.py
├── utils.py
└── .github/workflows/
    └── monitor.yml
```
