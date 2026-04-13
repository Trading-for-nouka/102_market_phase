import yfinance as yf
import pandas as pd
import os
import requests
import json
from datetime import datetime

# Discord設定
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

def notify_discord(msg: str):
    """Discordへの通知（エラー時も呼び出し可能）"""
    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
        except Exception as e:
            print(f"Discord通知失敗: {e}")

def evaluate_market_phase():
    try:
        # --- 0. データ取得 ---
        # Close価格（既存5銘柄 + ドル円）
        tickers_close = ["^N225", "1306.T", "^GSPC", "^VIX", "NIY=F", "JPY=X"]
        data = yf.download(tickers_close, period="2y", progress=False, threads=False)

        if isinstance(data.columns, pd.MultiIndex):
            close = data["Close"]
        else:
            close = data

        if close.empty:
            raise ValueError("Closeデータが空です")

        # Volume（1306.T の出来高。先に取得）
        volume_raw = yf.download("1306.T", period="2y", progress=False)
        volume_series = volume_raw["Volume"].squeeze().ffill()

        # 先物・ドル円は欠損が多いため個別ffill、他銘柄と分けてdropna
        # 先物・ドル円のみ ffill で補完、残りは行単位でdropna
        core_tickers  = ["^N225", "1306.T", "^GSPC", "^VIX"]
        extra_tickers = ["NIY=F", "JPY=X"]

        if isinstance(data.columns, pd.MultiIndex):
            data = data["Close"].copy()

        data.loc[:, extra_tickers] = data[extra_tickers].ffill()
        data.loc[:, core_tickers]  = data[core_tickers].ffill()
        data = data.dropna(subset=core_tickers)

        if len(data) < 200:
            raise ValueError(f"有効データが少なすぎます（{len(data)}行）")


        # --- 1. 各指標の計算 ---

        # 日経225
        # 日経225（closeをdataに統一）
        n225      = data["^N225"]
        n225_now  = n225.iloc[-1]
        n225_ma5  = n225.rolling(5).mean().iloc[-1]
        n225_ma25 = n225.rolling(25).mean().iloc[-1]
        n225_diff = n225.pct_change(fill_method=None)

        # 25日乖離率（RISK_OFF / REBOUND補助）
        # 修正後（nanチェックを追加）
        n225_dev25 = (n225_now / n225_ma25) - 1


        # TOPIX ETF
        topix_now    = data["1306.T"].iloc[-1]
        topix_200ma  = data["1306.T"].rolling(200).mean().iloc[-1]

        # 先物：乖離率ではなく前日比変化率で判定（修正済み）
        futures_pct = data["NIY=F"].pct_change(fill_method=None).iloc[-1]

        # S&P500
        sp500_change = data["^GSPC"].pct_change(fill_method=None).iloc[-1]

        # VIX（現値 / SMA3 / 週次急騰率）
        vix_series    = data["^VIX"]
        vix_now       = vix_series.iloc[-1]
        vix_sma3      = vix_series.rolling(3).mean()
        vix_sma3_now  = vix_sma3.iloc[-1]
        vix_sma3_prev = vix_sma3.iloc[-2]
        vix_is_falling = vix_sma3_now < vix_sma3_prev

        # VIX週次急騰（5営業日前との比較）
        vix_week_ago  = vix_series.iloc[-6] if len(vix_series) >= 6 else vix_series.iloc[0]
        vix_surge     = (vix_now / vix_week_ago) - 1  # 40%超で+1点

        # ドル円変化率（円高急進: -1.5%/日 以下）
        usdjpy_change = data["JPY=X"].pct_change(fill_method=None).iloc[-1]

        # 出来高（1306.T）：20日平均の2倍超
        vol_now   = volume_series.reindex(close.index).iloc[-1]
        vol_ma20  = volume_series.reindex(close.index).rolling(20).mean().iloc[-1]
        vol_surge = (vol_now > vol_ma20 * 2.0) if (pd.notna(vol_now) and pd.notna(vol_ma20) and vol_ma20 > 0) else False

        # 騰落レシオ（25日・N225単銘柄近似）
        def get_adr(diff_series):
            up   = (diff_series > 0).rolling(25).sum().iloc[-1]
            down = (diff_series <= 0).rolling(25).sum().iloc[-1]
            return (up / down) * 100 if down > 0 else 100

        adr_now  = get_adr(n225_diff)
        adr_prev = get_adr(n225_diff.shift(1))

        # ボラティリティ（5日std vs 60日std）
        nikkei_vol = n225_diff.rolling(5).std().iloc[-1]
        vol60      = n225_diff.rolling(60).std().iloc[-1]

        # --- 2. CRASHスコア（最大6点、閾値3以上）---
        crash_score = 0
        crash_reasons = []

        if sp500_change < -0.03:
            crash_score += 1
            crash_reasons.append(f"SP500:{sp500_change:.1%}")

        if vix_now > 30:
            crash_score += 1
            crash_reasons.append(f"VIX:{vix_now:.1f}")

        if vix_surge > 0.40:
            crash_score += 1
            crash_reasons.append(f"VIX週次急騰:{vix_surge:.0%}")

        if futures_pct < -0.03:
            crash_score += 1
            crash_reasons.append(f"先物:{futures_pct:.1%}")

        if usdjpy_change < -0.015:
            crash_score += 1
            crash_reasons.append(f"円高急進:{usdjpy_change:.1%}")

        if pd.notna(nikkei_vol) and pd.notna(vol60) and vol60 > 0 and nikkei_vol > vol60 * 2:
            crash_score += 1
            crash_reasons.append("ボラ急増")

        # 出来高急増はスコア加算ではなく注記として使用（誤報防止）
        vol_note = "📦出来高急増あり" if vol_surge else ""

        # --- 3. 最終フェーズ判定 ---

        # ① CRASH（スコア3以上 / 最大6点）
        if crash_score >= 3:
            phase = "CRASH"
            desc  = "🛑【退避】パニック相場（スコア制検知）"
            note  = f"異常値: {crash_score}/6点 ({' / '.join(crash_reasons)})"

        # ② WARN（スコア2点 / CRASH予備軍）← 追加
        elif crash_score == 2:
            phase = "WARN"
            desc  = "🟠【要注意】CRASH予備軍シグナル"
            note  = f"警戒: {crash_score}/6点 ({' / '.join(crash_reasons)}) — 翌日下落確率64%"

        # ③ REBOUND（底打ち条件 + VIX低下 + 25日線の乖離が深すぎない）
        elif (topix_now < topix_200ma or adr_now < 70) and \
             (n225_now > n225_ma5 and adr_now > adr_prev and vix_is_falling) and \
             (n225_dev25 > -0.15):
            phase = "REBOUND"
            desc  = "🔄【リバウンド】底打ち反転の兆し"
            note  = f"反転期待: 5MA回復 + ADR上昇 + VIX低下 (25日乖離:{n225_dev25:.1%})"

        # ④ RISK_OFF（200MA割れ または ADR低迷）
        elif topix_now < topix_200ma or (adr_now < 70 and vix_now > 20):  # ← ②の修正も反映済み
            phase = "RISK_OFF"
            desc  = "⚠️【警戒】地合い悪化"
            note  = f"守り優先: 200MA {'割れ' if topix_now < topix_200ma else 'OK'} / ADR:{adr_now:.0f} / 25日乖離:{n225_dev25:.1%}"

        # ⑤ BULL（ADR安定 かつ VIX低水準）
        elif 80 <= adr_now <= 120 and vix_now < 25:
            phase = "BULL"
            desc  = "🟢【良好】積極運用相場"
            note  = "安定: 指標正常範囲内"

        # ⑥ NEUTRAL
        else:
            phase = "NEUTRAL"
            desc  = "🧐【均衡】方向感なし"
            note  = "選別投資: 様子見"
        return {
            "phase":       phase,
            "description": desc,
            "level_note":  note,
            "vol_note":    vol_note,
            "stats": {
                "adr":          round(adr_now, 1),
                "vix":          round(vix_now, 1),
                "vix_sma3":     round(vix_sma3_now, 2),
                "vix_surge":    f"{vix_surge:.1%}",
                "futures_pct":  f"{futures_pct:.2%}",
                "usdjpy_change": f"{usdjpy_change:.2%}",
                "n225_dev25": f"{n225_dev25:.2%}" if pd.notna(n225_dev25) else "計算不可",
                "crash_score":  f"{crash_score}/6",
                "vol_surge":    vol_surge,
            },
            "updated": datetime.now(tz=__import__('zoneinfo').ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S JST")
        }

    except Exception as e:
        error_msg = f"⚠️ **Emergency Sentinel エラー**\n詳細: {e}"
        print(error_msg)
        notify_discord(error_msg)   # エラーもDiscordへ通知
        return None


def main():
    res = evaluate_market_phase()
    if not res:
        return

    # フェーズ別の先頭アイコン
    phase_icon = {
        "CRASH":    "📉",
        "WARN":     "🟠",   # ← 追加
        "RISK_OFF": "⚠️",
        "REBOUND":  "🔄",
        "BULL":     "📈",
        "NEUTRAL":  "📊",
    }.get(res["phase"], "📊")

    vol_line = f"\n┗ {res['vol_note']}" if res["vol_note"] else ""

    msg = (
        f"{phase_icon} **市場判定: {res['phase']}**\n"
        f"**{res['description']}**\n"
        f"┗ {res['level_note']}{vol_line}\n"
        f"```\n"
        f"CRASHスコア : {res['stats']['crash_score']}\n"
        f"ADR(25日)   : {res['stats']['adr']}\n"
        f"VIX         : {res['stats']['vix']}  (SMA3: {res['stats']['vix_sma3']} / 週次: {res['stats']['vix_surge']})\n"
        f"先物前日比  : {res['stats']['futures_pct']}\n"
        f"ドル円変化  : {res['stats']['usdjpy_change']}\n"
        f"25日線乖離  : {res['stats']['n225_dev25']}\n"
        f"出来高急増  : {'あり' if res['stats']['vol_surge'] else 'なし'}\n"
        f"```\n"
        f"🕒 {res['updated']}"
    )

    notify_discord(msg)

    with open("market_phase.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(msg)


if __name__ == "__main__":
    main()
