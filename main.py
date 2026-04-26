import os
import requests
import yfinance as yf
import pandas as pd

TICKERS = ["AMD", "NVDA", "BE"]


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def analyze_ticker(ticker):
    df = yf.download(ticker, period="3mo", auto_adjust=True, progress=False)

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
    latest_ma20 = float(ma20.iloc[-1])
    prev_ma20 = float(ma20.iloc[-2])
    latest_ma50 = float(ma50.iloc[-1])
    latest_rsi = float(rsi.iloc[-1])

    latest_volume = float(volume.iloc[-1])
    avg_volume_20 = float(volume.rolling(20).mean().iloc[-1])

    message = f"\n📌 {ticker}\n"
    message += f"收盤價：{latest_close:.2f}\n"

    if prev_close < prev_ma20 and latest_close > latest_ma20:
        message += "🚀 剛站上 20MA（轉強）\n"
    elif prev_close > prev_ma20 and latest_close < latest_ma20:
        message += "⚠️ 剛跌破 20MA（轉弱）\n"
    elif latest_close > latest_ma20:
        message += "👉 站上 20MA\n"
    else:
        message += "👉 跌破 20MA\n"

    if latest_close > latest_ma50:
        message += "👉 在 50MA 之上\n"
    else:
        message += "👉 在 50MA 之下\n"

    if latest_rsi > 80:
        message += "🔥 RSI > 80（極度過熱）\n"
    elif latest_rsi > 70:
        message += "⚠️ RSI 過熱\n"
    elif latest_rsi < 30:
        message += "🧊 RSI 超賣\n"
    else:
        message += "👉 RSI 中性\n"

    if latest_volume > avg_volume_20 * 1.5:
        message += "📢 成交量放大\n"

    return message


def build_message():
    final_message = "📈 今日技術面雷達\n"

    for ticker in TICKERS:
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
        "messages": [
            {
                "type": "text",
                "text": text,
            }
        ],
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