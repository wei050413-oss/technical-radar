import os
import requests
import yfinance as yf
import pandas as pd

SHEET_ID = "1DM7x4sQP2Mt7Tiohf2wGLt_l1dizbhAk-sZEIMleqlc"
SHEET_NAME = "Watchlist"

def get_tickers_from_sheets():
    url = f"https://docs.google.com/spreadsheets/d/1DM7x4sQP2Mt7Tiohf2wGLt_l1dizbhAk-sZEIMleqlc/gviz/tq?tqx=out:csv&sheet=Watchlist"
    df = pd.read_csv(url)

    active_df = df[df["active"] == True]
    tickers = active_df["ticker"].dropna().astype(str).str.strip().tolist()

    return tickers


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def analyze_ticker(ticker):
    df = yf.download(ticker, period="6mo", auto_adjust=True, progress=False)

    if df.empty:
        return f"\n📌 {ticker}\n抓不到資料\n"

    if isinstance(df.columns, pd.MultiIndex):
        close = df["Close"][ticker]
        volume = df["Volume"][ticker]
    else:
        close = df["Close"]
        volume = df["Volume"]

    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    rsi = calculate_rsi(close)

    latest_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    change_pct = (latest_close - prev_close) / prev_close * 100

    latest_ma20 = float(ma20.iloc[-1])
    prev_ma20 = float(ma20.iloc[-2])
    latest_ma50 = float(ma50.iloc[-1])
    latest_rsi = float(rsi.iloc[-1])
    prev_rsi = float(rsi.iloc[-2])

    latest_volume = float(volume.iloc[-1])
    avg_volume_20 = float(volume.rolling(20).mean().iloc[-1])

    # ===== 結構：20日區間位置 =====
    recent_20_high = float(close.tail(20).max())
    recent_20_low = float(close.tail(20).min())

    if recent_20_high == recent_20_low:
        position_20 = 0.5
    else:
        position_20 = (latest_close - recent_20_low) / (recent_20_high - recent_20_low)

    # ===== 結構：突破 / 跌破 =====
    prev_20_high = float(close.iloc[-21:-1].max())
    prev_20_low = float(close.iloc[-21:-1].min())

    # ===== 結構：趨勢方向 =====
    ma20_slope = float(ma20.iloc[-1] - ma20.iloc[-5])

    message = f"\n📌 {ticker}\n"
    message += f"收盤價：{latest_close:.2f} ({change_pct:+.2f}%)\n"

    if abs(change_pct) >= 3:
        message += "⚠️ 當日波動較大\n"

    # ===== 區間結構 =====
    message += "\n📊 結構\n"

    if position_20 >= 0.8:
        message += f"👉 接近 20日高點（區間位置 {position_20:.0%}）\n"
    elif position_20 <= 0.2:
        message += f"👉 接近 20日低點（區間位置 {position_20:.0%}）\n"
    else:
        message += f"👉 位於 20日區間中段（區間位置 {position_20:.0%}）\n"

    if latest_close > prev_20_high:
        message += "🚀 突破近 20日高點\n"
    elif latest_close < prev_20_low:
        message += "⚠️ 跌破近 20日低點\n"

    if latest_ma20 > latest_ma50 and ma20_slope > 0:
        message += "📈 趨勢結構偏多：20MA > 50MA，且 20MA 上彎\n"
    elif latest_ma20 < latest_ma50 and ma20_slope < 0:
        message += "📉 趨勢結構偏空：20MA < 50MA，且 20MA 下彎\n"
    else:
        message += "↔️ 趨勢結構不明顯 / 盤整可能\n"

    # ===== 指標狀態 =====
    message += "\n📊 指標\n"

    if latest_close > latest_ma20:
        message += "👉 站上 20MA\n"
    else:
        message += "👉 跌破 20MA\n"

    if prev_close < prev_ma20 and latest_close > latest_ma20:
        message += "🚀 剛站上 20MA（短線轉強）\n"

    if prev_close > prev_ma20 and latest_close < latest_ma20:
        message += "⚠️ 剛跌破 20MA（短線轉弱）\n"

    if latest_close > latest_ma50:
        message += "👉 在 50MA 之上\n"
    else:
        message += "👉 在 50MA 之下\n"

    if latest_rsi > 80:
        message += f"🔥 RSI {latest_rsi:.1f}，極度過熱\n"
    elif latest_rsi > 70:
        message += f"⚠️ RSI {latest_rsi:.1f}，偏熱\n"
    elif latest_rsi < 30:
        message += f"🧊 RSI {latest_rsi:.1f}，超賣\n"
    else:
        message += f"👉 RSI {latest_rsi:.1f}，中性\n"

    if prev_rsi <= 70 and latest_rsi > 70:
        message += "🚀 RSI 剛進入過熱區\n"

    if prev_rsi >= 30 and latest_rsi < 30:
        message += "⚠️ RSI 剛跌入超賣區\n"

    if latest_volume > avg_volume_20 * 1.5:
        message += "📢 成交量明顯放大\n"

        prev_volume = float(volume.iloc[-2])
        prev_avg_volume_20 = float(volume.rolling(20).mean().iloc[-2])

        if prev_volume <= prev_avg_volume_20 * 1.5:
            message += "🚀 成交量剛放大\n"

    return message


def build_message():
    tickers = get_tickers_from_sheets()

    final_message = "📈 今日技術面雷達\n"
    final_message += f"追蹤股票數：{len(tickers)}\n"

    for ticker in tickers:
        try:
            final_message += analyze_ticker(ticker)
        except Exception as e:
            final_message += f"\n📌 {ticker}\n發生錯誤：{e}\n"

    return final_message


def send_line_message(text):
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.getenv("LINE_USER_ID")

    if not token or not user_id:
        print("❌ LINE token 或 user id 沒有設定")
        print(text)
        return

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }

    response = requests.post(url, headers=headers, json=payload)

    print("LINE status:", response.status_code)
    print(response.text)


def main():
    message = build_message()
    print(message)
    send_line_message(message)


if __name__ == "__main__":
    main()