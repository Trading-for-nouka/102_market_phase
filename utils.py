import pandas as pd
import pandas_ta as ta


def is_excluded(df):
    """除外条件：流動性・急落・上がりすぎをフィルタ"""

    # 1. 流動性（5日平均売買代金が5億円未満は除外）
    avg_turnover = (df['Close'].squeeze() * df['Volume'].squeeze()).rolling(5).mean().iloc[-1]
    if avg_turnover < 500_000_000:
        return True

    # 2. 直近ストップ安相当の急落は除外（翌日も続落リスク）
    if (df['Close'].squeeze().iloc[-1] / df['Close'].squeeze().iloc[-2] - 1) < -0.10:
        return True

    # 3. 25日MAから+10%以上乖離していたら除外（上がりすぎ、REBOUNDの対象外）
    sma25 = df['Close'].squeeze().rolling(25).mean().iloc[-1]
    if df['Close'].squeeze().iloc[-1] > sma25 * 1.10:
        return True

    return False


def calculate_score(df, macro_phase, return_breakdown=False):
    """
    リバウンド候補スコアリング（最大100点）
    60点以上を候補として抽出

    Args:
        df              (DataFrame): 株価データ
        macro_phase     (str):       フェーズ名
        return_breakdown (bool):     Trueのとき内訳dictも返す

    Returns:
        int or (int, dict): スコア、またはスコアと内訳の tuple
    """
    score = 0
    breakdown = {}
    close  = df['Close'].squeeze()
    volume = df['Volume'].squeeze()

    # --- RSI（売られ過ぎ度）最大30点 ---
    rsi = ta.rsi(close, length=14).iloc[-1]
    if rsi <= 30:
        s = 30
    elif rsi <= 40:
        s = 15
    elif rsi <= 50:
        s = 5
    else:
        s = 0
    score += s
    breakdown["RSI売られ過ぎ"] = s

    # --- 20日安値圏（底値付近）最大20点 ---
    low20 = df['Low'].squeeze().rolling(20).min().iloc[-1]
    s = 20 if close.iloc[-1] <= low20 * 1.03 else 0
    score += s
    breakdown["20日安値圏"] = s

    # --- 直近1日の反発（陽線）10点 ---
    s = 10 if close.iloc[-1] > close.iloc[-2] else 0
    score += s
    breakdown["直近陽線"] = s

    # --- 出来高急増（反発の信頼性）最大15点 ---
    vol_ma5 = volume.rolling(5).mean().iloc[-1]
    s = 15 if volume.iloc[-1] > vol_ma5 * 1.5 else 0
    score += s
    breakdown["出来高急増"] = s

    # --- 25日MAへの回復途中（乖離 -5〜0%）15点 ---
    sma25 = close.rolling(25).mean().iloc[-1]
    dev = (close.iloc[-1] / sma25) - 1
    s = 15 if -0.05 <= dev <= 0.0 else 0
    score += s
    breakdown["MA25回復途中"] = s

    # --- マクロフェーズボーナス 最大20点 ---
    if macro_phase == "REBOUND":
        s = 20
    elif macro_phase == "WARN":
        s = 10
    elif macro_phase in ["NEUTRAL", "BULL"]:
        s = 5
    else:
        s = 0
    score += s
    breakdown[f"フェーズ({macro_phase})"] = s

    if return_breakdown:
        return score, breakdown
    return score
