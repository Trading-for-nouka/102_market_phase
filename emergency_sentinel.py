import yfinance as yf
import pandas as pd
import os
import requests
import json
from datetime import datetime

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")


def notify_discord(msg: str):
    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
        except Exception as e:
            print(f"Discord通知失敗: {e}")


def calc_adr_from_universe(csv_path: str = "universe496.csv") -> tuple:
    """
    universe496.csvの全銘柄で正しい25日騰落レシオを計算する。
    Returns: (adr_now, adr_prev) — 失敗時は (None, None)
    """
    try:
        try:
            univ = pd.read_csv(csv_path, encoding="utf-8")
        except UnicodeDecodeError:
            univ = pd.read_csv(csv_path, encoding="shift_jis")

        tickers = univ["ticker"].dropna().tolist()

        # 25日ローリングに必要な最小限のデータのみ取得
        raw = yf.download(tickers, period="60d", progress=False, threads=False)
        prices = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw

        if prices.empty or len(prices) < 27:
            raise ValueError(f"ADR用データ不足（{len(prices)}行）")

        changes = prices.pct_change(fill_method=None)
        up_sum   = (changes > 0).rolling(25).sum().sum(axis=1)
        down_sum = (changes <= 0).rolling(25).sum().sum(axis=1)

        adr_series = up_sum / down_sum.replace(0, float("nan")) * 100
        return float(adr_series.iloc[-1]), float(adr_series.iloc[-2])

    except Exception as e:
        print(f"ADR計算失敗（N225フォールバック使用）: {e}")
        return None, None


def calc_adr_fallback(n225_diff: pd.Series) -> tuple:
    """N225単体フォールバック（universe ADR計算失敗時のみ使用）"""
    up   = (n225_diff > 0).rolling(25).sum()
    down = (n225_diff <= 0).rolling(25).sum().replace(0, float("nan"))
    adr  = up / down * 100
    return float(adr.iloc[-1]), float(adr.iloc[-2])


def load_last_crash_date() -> str | None:
    """market_phase.jsonから直前のCRASH日付を読み込む"""
    if os.path.exists("market_phase.json"):
        try:
            with open("market_phase.json", "r", encoding="utf-8") as f:
                return json.load(f).get("last_crash_date")
        except Exception:
            pass
    return None


def evaluate_market_phase():
    try:
        # --- 0. データ取得 ---
        tickers_close = ["^N225", "1306.T", "^GSPC", "^VIX", "NIY=F", "JPY=X"]
        data = yf.download(tickers_close, period="2y", progress=False, threads=False)

        if isinstance(data.columns, pd.MultiIndex):
            close = data["Close"]
        else:
            close = data

        if close.empty:
            raise ValueError("Closeデータが空です")

        volume_raw    = yf.download("1306.T", period="2y", progress=False)
        volume_series = volume_raw["Volume"].squeeze().ffill()

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
        n225      = data["^N225"]
        n225_now  = n225.iloc[-1]
        n225_ma5  = n225.rolling(5).mean().iloc[-1]
        n225_ma25 = n225.rolling(25).mean().iloc[-1]
        n225_diff = n225.pct_change(fill_method=None)
        n225_dev25 = (n225_now / n225_ma25) - 1

        # 中期累積下落：10営業日で-8%超（じわじわ下落の捕捉）
        n225_10d_change = (n225_now / n225.iloc[-11]) - 1 if len(n225) >= 11 else 0.0

        topix_now   = data["1306.T"].iloc[-1]
        topix_200ma = data["1306.T"].rolling(200).mean().iloc[-1]

        futures_pct  = data["NIY=F"].pct_change(fill_method=None).iloc[-1]
        sp500_change = data["^GSPC"].pct_change(fill_method=None).iloc[-1]

        vix_series    = data["^VIX"]
        vix_now       = vix_series.iloc[-1]
        vix_sma3      = vix_series.rolling(3).mean()
        vix_sma3_now  = vix_sma3.iloc[-1]
        vix_sma3_prev = vix_sma3.iloc[-2]
        vix_is_falling = vix_sma3_now < vix_sma3_prev

        vix_week_ago = vix_series.iloc[-6] if len(vix_series) >= 6 else vix_series.iloc[0]
        vix_surge    = (vix_now / vix_week_ago) - 1

        usdjpy_change = data["JPY=X"].pct_change(fill_method=None).iloc[-1]

        vol_now   = volume_series.reindex(close.index).iloc[-1]
        vol_ma20  = volume_series.reindex(close.index).rolling(20).mean().iloc[-1]
        vol_surge = (vol_now > vol_ma20 * 2.0) if (pd.notna(vol_now) and pd.notna(vol_ma20) and vol_ma20 > 0) else False

        # ADR：universe496.csv全銘柄で計算（失敗時はN225フォールバック）
        adr_now, adr_prev = calc_adr_from_universe()
        adr_source = "universe496"
        if adr_now is None:
            adr_now, adr_prev = calc_adr_fallback(n225_diff)
            adr_source = "N225(fallback)"

        nikkei_vol = n225_diff.rolling(5).std().iloc[-1]
        vol60      = n225_diff.rolling(60).std().iloc[-1]

        # --- 2. CRASHスコア（最大7点、閾値3以上）---
        crash_score   = 0
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

        # ★追加：中期累積下落（10営業日で-8%超 = じわじわ下落パターン）
        if n225_10d_change < -0.08:
            crash_score += 1
            crash_reasons.append(f"10日累積:{n225_10d_change:.1%}")

        vol_note = "📦出来高急増あり" if vol_surge else ""

        # --- 2b. CRASHメモリ（直近7日以内のCRASH状態をWARN以上で保持）---
        today_str    = datetime.now().strftime("%Y-%m-%d")
        last_crash_date   = load_last_crash_date()
        crash_memory_active = False

        if crash_score >= 3:
            last_crash_date = today_str
        elif last_crash_date:
            prev_dt  = datetime.strptime(last_crash_date, "%Y-%m-%d")
            days_ago = (datetime.now() - prev_dt).days
            if days_ago <= 7:
                crash_memory_active = True

        # --- 3. 最終フェーズ判定 ---

        # ① CRASH（スコア3以上）
        if crash_score >= 3:
            phase = "CRASH"
            desc  = "🛑【退避】パニック相場（スコア制検知）"
            note  = f"異常値: {crash_score}/7点 ({' / '.join(crash_reasons)})"

        # ② WARN（スコア2点 or 直近7日以内にCRASHを観測）
        elif crash_score == 2 or crash_memory_active:
            phase = "WARN"
            if crash_memory_active and crash_score < 2:
                desc = "🟠【要注意】直近CRASH余韻（7日以内）"
                note = f"警戒継続: 直近CRASH={last_crash_date} — 急変動は収まったが余韻に注意"
            else:
                desc = "🟠【要注意】CRASH予備軍シグナル"
                note = f"警戒: {crash_score}/7点 ({' / '.join(crash_reasons)})"

        # ③ REBOUND（底打ち条件 + VIX低下 + 25日乖離が深すぎない）
        elif (topix_now < topix_200ma or adr_now < 70) and \
             (n225_now > n225_ma5 and adr_now > adr_prev and vix_is_falling) and \
             (n225_dev25 > -0.15):
            phase = "REBOUND"
            desc  = "🔄【リバウンド】底打ち反転の兆し"
            note  = f"反転期待: 5MA回復 + ADR上昇 + VIX低下 (25日乖離:{n225_dev25:.1%})"

        # ④ RISK_OFF（200MA割れ または ADR低迷）
        elif topix_now < topix_200ma or (adr_now < 70 and vix_now > 20):
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
            "phase":           phase,
            "description":     desc,
            "level_note":      note,
            "vol_note":        vol_note,
            "last_crash_date": last_crash_date,
            "stats": {
                "adr":             round(adr_now, 1),
                "adr_source":      adr_source,
                "vix":             round(vix_now, 1),
                "vix_sma3":        round(vix_sma3_now, 2),
                "vix_surge":       f"{vix_surge:.1%}",
                "futures_pct":     f"{futures_pct:.2%}",
                "usdjpy_change":   f"{usdjpy_change:.2%}",
                "n225_dev25":      f"{n225_dev25:.2%}" if pd.notna(n225_dev25) else "計算不可",
                "n225_10d_change": f"{n225_10d_change:.2%}",
                "crash_score":     f"{crash_score}/7",
                "vol_surge":       bool(vol_surge),
            },
            "updated": datetime.now(tz=__import__('zoneinfo').ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S JST")
        }

    except Exception as e:
        error_msg = f"⚠️ **Emergency Sentinel エラー**\n詳細: {e}"
        print(error_msg)
        notify_discord(error_msg)
        return None


def main():
    res = evaluate_market_phase()
    if not res:
        return

    phase_icon = {
        "CRASH":    "📉",
        "WARN":     "🟠",
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
        f"ADR(25日)   : {res['stats']['adr']}  [{res['stats']['adr_source']}]\n"
        f"VIX         : {res['stats']['vix']}  (SMA3: {res['stats']['vix_sma3']} / 週次: {res['stats']['vix_surge']})\n"
        f"先物前日比  : {res['stats']['futures_pct']}\n"
        f"ドル円変化  : {res['stats']['usdjpy_change']}\n"
        f"25日線乖離  : {res['stats']['n225_dev25']}\n"
        f"10日累積変化: {res['stats']['n225_10d_change']}\n"
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
