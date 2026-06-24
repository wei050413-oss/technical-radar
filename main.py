import json
import os
from datetime import date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import yfinance as yf


SHEET_ID = "1DM7x4sQP2Mt7Tiohf2wGLt_l1dizbhAk-sZEIMleqlc"
SHEET_NAME = "watchlist"
MAX_MESSAGE_LENGTH = 4500
TRUNCATION_NOTICE = "訊息過長，已截斷。"
EVENT_STATE_PATH = Path(__file__).with_name("event_state.json")
VOLUME_EXPANSION_THRESHOLD = 1.5
VOLUME_SHRINK_THRESHOLD = 1.0
BIG_MOVE_THRESHOLD = 5.0
TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
MAX_ALERTS_PER_TICKER = 4
WHY_DID_IT_MOVE_THRESHOLD = 5.0
MAX_WHY_DID_IT_MOVE_TICKERS = 5
MAX_WHY_DID_IT_MOVE_NEWS = 3
NEWS_LOOKBACK_HOURS = 72
NEWS_REQUEST_TIMEOUT = 10

MACRO_EVENTS = [
    {
        "id": "CPI_2026-06-12",
        "type": "macro",
        "name": "CPI",
        "date": "2026-06-12",
        "session": "before_market",
    },
    {
        "id": "PPI_2026-06-15",
        "type": "macro",
        "name": "PPI",
        "date": "2026-06-15",
        "session": "before_market",
    },
    {
        "id": "FOMC_2026-06-18",
        "type": "macro",
        "name": "FOMC",
        "date": "2026-06-18",
        "session": "after_market",
    },
    {
        "id": "NFP_2026-07-05",
        "type": "macro",
        "name": "非農就業",
        "date": "2026-07-05",
        "session": "before_market",
    },
    {
        "id": "PCE_2026-07-26",
        "type": "macro",
        "name": "PCE",
        "date": "2026-07-26",
        "session": "before_market",
    },
]


def get_tickers_from_sheets():
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
        f"?tqx=out:csv&sheet={SHEET_NAME}"
    )

    try:
        df = pd.read_csv(url)
        required_columns = {"ticker", "active"}
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"缺少必要欄位：{missing}")

        active = df["active"].astype(str).str.strip().str.lower()
        active_df = df[active.isin({"true", "1", "yes", "y"})]
        return (
            active_df["ticker"]
            .dropna()
            .astype(str)
            .str.strip()
            .loc[lambda values: values.ne("")]
            .tolist()
        )
    except Exception as exc:
        print(f"Google Sheets 讀取失敗：{exc}")
        raise


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_volume_ratio(volume, avg_volume_20d):
    if (
        pd.isna(volume)
        or pd.isna(avg_volume_20d)
        or avg_volume_20d <= 0
    ):
        return None

    return volume / avg_volume_20d


def get_volume_price_signal(change_pct, volume_ratio):
    if change_pct is None or volume_ratio is None:
        return {"label": None, "priority": "none"}
    if pd.isna(change_pct) or pd.isna(volume_ratio):
        return {"label": None, "priority": "none"}

    ratio_label = f"{volume_ratio:.1f}x Avg Volume"

    if change_pct == 0:
        return {
            "label": f"➖ 價格持平（{ratio_label}）",
            "priority": "none",
        }

    if volume_ratio >= VOLUME_EXPANSION_THRESHOLD:
        if change_pct >= BIG_MOVE_THRESHOLD:
            return {
                "label": f"🔥 強勢放量上漲（{ratio_label}）",
                "priority": "high",
            }
        if change_pct > 0:
            return {
                "label": f"📈 放量上漲（{ratio_label}）",
                "priority": "high",
            }
        if change_pct <= -BIG_MOVE_THRESHOLD:
            return {
                "label": f"🚨 強勢放量下跌（{ratio_label}）",
                "priority": "high",
            }
        return {
            "label": f"⚠️ 放量下跌（{ratio_label}）",
            "priority": "high",
        }

    if volume_ratio < VOLUME_SHRINK_THRESHOLD:
        if change_pct >= BIG_MOVE_THRESHOLD:
            return {
                "label": f"⚠️ 強勢量縮上漲（{ratio_label}）",
                "priority": "medium",
            }
        if change_pct <= -BIG_MOVE_THRESHOLD:
            return {
                "label": f"📉 強勢量縮下跌（{ratio_label}）",
                "priority": "medium",
            }
        return {"label": None, "priority": "none"}

    if change_pct > 0:
        return {
            "label": f"➖ 正常量能上漲（{ratio_label}）",
            "priority": "none",
        }

    return {
        "label": f"➖ 正常量能下跌（{ratio_label}）",
        "priority": "none",
    }


def is_volume_price_signal(alert):
    return isinstance(alert, str) and "Avg Volume" in alert


def limit_alerts_prioritizing_volume_signal(alerts, limit=MAX_ALERTS_PER_TICKER):
    if limit <= 0:
        return [alert for alert in alerts if is_volume_price_signal(alert)][:1]

    if len(alerts) <= limit:
        return alerts

    visible_alerts = alerts[:limit]
    if any(is_volume_price_signal(alert) for alert in visible_alerts):
        return visible_alerts

    for alert in alerts[limit:]:
        if is_volume_price_signal(alert):
            return visible_alerts[: limit - 1] + [alert]

    return visible_alerts


def get_today_taipei():
    return datetime.now(TAIPEI_TIMEZONE).date()


def parse_event_date(event):
    current_year = get_today_taipei().year
    value = event.get("date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        value = value.strip()
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            pass

        for separator in ("/", "-"):
            if separator not in value:
                continue
            try:
                month, day = (int(part) for part in value.split(separator, 1))
                return date(current_year, month, day)
            except ValueError:
                continue
    raise ValueError(f"無效事件日期：{value}")


def format_event_date(event_date):
    if isinstance(event_date, datetime):
        event_date = event_date.date()
    return f"{event_date.month}/{event_date.day}"


def format_event_session(session):
    session_labels = {
        "before_market": "盤前公布",
        "after_market": "盤後公布",
        "during_market": "盤中公布",
        "unknown": "時間待確認",
    }
    return session_labels.get(session, "時間待確認")


def format_event_line(event):
    event_date = parse_event_date(event)
    session = format_event_session(event.get("session", "unknown"))
    return f"{event['name']}：{format_event_date(event_date)} {session}"


def _flatten_calendar_values(value):
    if isinstance(value, pd.DataFrame):
        return value.to_numpy().ravel().tolist()
    if isinstance(value, (pd.Series, pd.Index)):
        return value.tolist()
    if isinstance(value, (list, tuple, set)):
        flattened = []
        for item in value:
            flattened.extend(_flatten_calendar_values(item))
        return flattened
    return [value]


def _extract_earnings_values(calendar):
    if calendar is None:
        return []

    if isinstance(calendar, dict):
        for key, value in calendar.items():
            if "earnings date" in str(key).strip().lower():
                return _flatten_calendar_values(value)
        return []

    if isinstance(calendar, pd.Series):
        for key, value in calendar.items():
            if "earnings date" in str(key).strip().lower():
                return _flatten_calendar_values(value)
        return []

    if isinstance(calendar, pd.DataFrame):
        for label in calendar.index:
            if "earnings date" in str(label).strip().lower():
                return _flatten_calendar_values(calendar.loc[label])
        for label in calendar.columns:
            if "earnings date" in str(label).strip().lower():
                return _flatten_calendar_values(calendar[label])

    return []


def _parse_earnings_datetime(value):
    if value is None or pd.isna(value):
        return None

    try:
        if isinstance(value, (int, float)):
            timestamp = pd.to_datetime(value, unit="s")
        else:
            timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if pd.isna(timestamp):
        return None
    return timestamp.to_pydatetime()


def _get_earnings_session(earnings_datetime):
    if not isinstance(earnings_datetime, datetime):
        return "unknown"

    event_time = earnings_datetime.time()
    if event_time == time.min:
        return "unknown"

    eastern = ZoneInfo("America/New_York")
    if earnings_datetime.tzinfo is None:
        eastern_datetime = earnings_datetime.replace(tzinfo=eastern)
    else:
        eastern_datetime = earnings_datetime.astimezone(eastern)

    event_time = eastern_datetime.time().replace(tzinfo=None)
    if event_time < time(9, 30):
        return "before_market"
    if event_time >= time(16, 0):
        return "after_market"
    if time(9, 30) <= event_time < time(16, 0):
        return "during_market"
    return "unknown"


def get_earnings_events(tickers):
    events = []
    today = get_today_taipei()

    for ticker in tickers:
        try:
            calendar = yf.Ticker(ticker).calendar
            values = _extract_earnings_values(calendar)
            earnings_datetimes = [
                parsed
                for value in values
                if (parsed := _parse_earnings_datetime(value)) is not None
            ]
            upcoming = [
                value for value in earnings_datetimes if value.date() >= today
            ]
            if not upcoming:
                continue

            earnings_datetime = min(upcoming, key=lambda value: value.date())
            earnings_date = earnings_datetime.date().isoformat()
            events.append(
                {
                    "id": f"{ticker}_earnings_{earnings_date}",
                    "type": "earnings",
                    "name": f"{ticker} 財報",
                    "ticker": ticker,
                    "date": earnings_date,
                    "session": _get_earnings_session(earnings_datetime),
                }
            )
        except Exception as exc:
            print(f"Warning: {ticker} 財報日期讀取失敗，已跳過：{exc}")

    return events


def get_macro_events():
    events = []
    for event in MACRO_EVENTS:
        try:
            parse_event_date(event)
            events.append(event.copy())
        except (TypeError, ValueError) as exc:
            print(
                f"Warning: macro event {event.get('id', 'unknown')} "
                f"日期格式錯誤，已跳過：{exc}"
            )
    return events


def get_upcoming_events(events, today):
    upcoming_events = []
    for event in events:
        try:
            event_date = parse_event_date(event)
        except (TypeError, ValueError) as exc:
            print(
                f"Warning: event {event.get('id', 'unknown')} "
                f"日期格式錯誤，已跳過：{exc}"
            )
            continue

        if event_date >= today:
            upcoming_events.append(event)

    return upcoming_events


def load_event_state():
    empty_state = {"notified_event_ids": []}
    if not EVENT_STATE_PATH.exists():
        try:
            save_event_state(empty_state)
        except OSError as exc:
            print(f"Warning: event_state.json 建立失敗：{exc}")
        return empty_state

    try:
        with EVENT_STATE_PATH.open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        notified_ids = state.get("notified_event_ids")
        if not isinstance(notified_ids, list):
            raise ValueError("notified_event_ids 必須是 list")
        return {"notified_event_ids": list(dict.fromkeys(notified_ids))}
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"Warning: event_state.json 讀取失敗，使用空狀態：{exc}")
        return empty_state


def save_event_state(state):
    normalized_state = {
        "notified_event_ids": list(
            dict.fromkeys(state.get("notified_event_ids", []))
        )
    }
    with EVENT_STATE_PATH.open("w", encoding="utf-8") as state_file:
        json.dump(normalized_state, state_file, ensure_ascii=False, indent=2)
        state_file.write("\n")

    # GitHub Actions runner is ephemeral. Persisting this state there later
    # requires committing it back to the repo or using external storage such
    # as Google Sheets, GitHub Gist, Firebase, or Supabase.


def get_new_events(events, state):
    notified_ids = set(state.get("notified_event_ids", []))
    return [event for event in events if event["id"] not in notified_ids]


def update_event_state(state, new_events):
    notified_ids = list(state.get("notified_event_ids", []))
    notified_ids.extend(event["id"] for event in new_events)
    state["notified_event_ids"] = list(dict.fromkeys(notified_ids))
    return state


def get_this_week_events(events, today):
    end_date = today + timedelta(days=7)
    this_week = []

    for event in events:
        try:
            event_date = parse_event_date(event)
        except (TypeError, ValueError) as exc:
            print(
                f"Warning: event {event.get('id', 'unknown')} "
                f"日期格式錯誤，已跳過：{exc}"
            )
            continue

        if today <= event_date <= end_date:
            this_week.append(event)

    return this_week


def _sort_events(events):
    return sorted(events, key=lambda event: (parse_event_date(event), event["name"]))


def build_event_radar(tickers):
    today = get_today_taipei()
    earnings_events = get_earnings_events(tickers)
    macro_events = get_macro_events()
    events_by_id = {
        event["id"]: event for event in earnings_events + macro_events
    }
    events = _sort_events(get_upcoming_events(events_by_id.values(), today))

    state = load_event_state()
    new_events = get_new_events(events, state)[:10]
    new_event_ids = {event["id"] for event in new_events}
    this_week_events = [
        event
        for event in get_this_week_events(events, today)
        if event["id"] not in new_event_ids
    ][:10]

    if new_events:
        update_event_state(state, new_events)
        try:
            save_event_state(state)
        except OSError as exc:
            print(f"Warning: event_state.json 更新失敗：{exc}")

    if not new_events and not this_week_events:
        return "📅 Event Radar\n今日無新的或當週重要事件。"

    lines = ["📅 Event Radar"]
    if new_events:
        lines.extend(["", "🆕 New Events"])
        lines.extend(format_event_line(event) for event in new_events)

    if this_week_events:
        lines.extend(["", "📌 This Week"])
        lines.extend(format_event_line(event) for event in this_week_events)

    return "\n".join(lines)


def get_price_data(ticker):
    df = yf.download(
        ticker,
        period="1y",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if df.empty:
        raise ValueError("抓不到價格資料")

    if isinstance(df.columns, pd.MultiIndex):
        try:
            close = df["Close"][ticker]
            volume = df["Volume"][ticker]
        except KeyError:
            close = df["Close"].iloc[:, 0]
            volume = df["Volume"].iloc[:, 0]
    else:
        close = df["Close"]
        volume = df["Volume"]

    price_data = pd.DataFrame({"close": close, "volume": volume}).dropna(
        subset=["close"]
    )
    if len(price_data) < 2:
        raise ValueError("價格資料不足，至少需要兩個交易日")

    return price_data


def analyze_ticker(ticker):
    result = {
        "ticker": ticker,
        "close": None,
        "change_pct": None,
        "volume_ratio": None,
        "high_alerts": [],
        "medium_alerts": [],
        "error": None,
    }

    try:
        df = get_price_data(ticker)
        close = df["close"]
        volume = df["volume"]

        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        rsi = calculate_rsi(close)
        avg_volume_20 = volume.rolling(20).mean()

        latest_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        change_pct = (latest_close - prev_close) / prev_close * 100

        result["close"] = latest_close
        result["change_pct"] = change_pct

        previous_close = close.iloc[:-1]
        previous_20 = previous_close.tail(20)
        previous_52_week = previous_close.tail(252)

        if len(previous_20) >= 20:
            if latest_close > float(previous_20.max()):
                result["high_alerts"].append("突破近20日高點")
            if latest_close < float(previous_20.min()):
                result["high_alerts"].append("跌破近20日低點")

        if not previous_52_week.empty:
            if latest_close > float(previous_52_week.max()):
                result["high_alerts"].append("創52週新高")
            if latest_close < float(previous_52_week.min()):
                result["high_alerts"].append("創52週新低")

        latest_ma50 = ma50.iloc[-1]
        prev_ma50 = ma50.iloc[-2]
        if pd.notna(latest_ma50) and pd.notna(prev_ma50):
            if prev_close > prev_ma50 and latest_close < latest_ma50:
                result["high_alerts"].append("剛跌破50MA")
            if prev_close < prev_ma50 and latest_close > latest_ma50:
                result["high_alerts"].append("剛站上50MA")

        latest_avg_volume = avg_volume_20.iloc[-1]
        latest_volume = volume.iloc[-1]
        volume_ratio = calculate_volume_ratio(latest_volume, latest_avg_volume)
        result["volume_ratio"] = volume_ratio
        volume_signal = get_volume_price_signal(change_pct, volume_ratio)
        if volume_signal["priority"] == "high":
            result["high_alerts"].append(volume_signal["label"])
        if volume_signal["priority"] == "medium":
            result["medium_alerts"].append(volume_signal["label"])

        latest_ma20 = ma20.iloc[-1]
        prev_ma20 = ma20.iloc[-2]
        if pd.notna(latest_ma20) and pd.notna(prev_ma20):
            if prev_close > prev_ma20 and latest_close < latest_ma20:
                result["medium_alerts"].append("剛跌破20MA")
            if prev_close < prev_ma20 and latest_close > latest_ma20:
                result["medium_alerts"].append("剛站上20MA")

        latest_rsi = rsi.iloc[-1]
        if pd.notna(latest_rsi):
            if latest_rsi > 80:
                result["medium_alerts"].append(
                    f"RSI {latest_rsi:.1f}，極度過熱"
                )
            if latest_rsi < 30:
                result["medium_alerts"].append(f"RSI {latest_rsi:.1f}，超賣")

    except Exception as exc:
        result["error"] = str(exc)
        print(f"{ticker} 資料處理失敗：{exc}")

    return result


def build_daily_watchlist(results):
    lines = ["📈 Daily Watchlist"]
    for result in results:
        ticker = result["ticker"]
        if result.get("error"):
            lines.append(f"{ticker}: 資料錯誤")
            continue

        lines.append(
            f"{ticker}: {result['close']:.2f} "
            f"({result['change_pct']:+.2f}%)"
        )

    return "\n".join(lines)


def build_technical_alerts(results):
    ticker_alerts = []

    for result in results:
        if result.get("error"):
            continue

        high_alerts = limit_alerts_prioritizing_volume_signal(
            result["high_alerts"]
        )
        remaining_slots = MAX_ALERTS_PER_TICKER - len(high_alerts)
        medium_alerts = limit_alerts_prioritizing_volume_signal(
            result["medium_alerts"],
            remaining_slots,
        )
        alerts = high_alerts + medium_alerts

        if alerts:
            ticker_alerts.append((result["ticker"], alerts))

    if not ticker_alerts:
        return "🚨 Technical Alerts\n\nNo alerts today."

    lines = ["🚨 Technical Alerts"]
    for ticker, alerts in ticker_alerts:
        lines.extend(["", ticker])
        lines.extend(f"- {alert}" for alert in alerts)

    return "\n".join(lines)


def get_why_did_it_move_candidates(results):
    candidates = [
        result
        for result in results
        if not result.get("error")
        and result.get("change_pct") is not None
        and abs(result["change_pct"]) >= WHY_DID_IT_MOVE_THRESHOLD
    ]
    return sorted(
        candidates,
        key=lambda result: abs(result["change_pct"]),
        reverse=True,
    )[:MAX_WHY_DID_IT_MOVE_TICKERS]


def _parse_news_publish_time(news_item):
    published_at = news_item.get("published_at")
    if not published_at:
        return None

    try:
        return parsedate_to_datetime(published_at)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _rss_child_text(item, local_name):
    for child in item:
        if child.tag.split("}")[-1] == local_name:
            text = child.text
            if text:
                return text.strip()
    return None


def _normalize_news_title(title):
    return " ".join((title or "").split()).lower()


def _news_source_from_title(title):
    if " - " not in title:
        return None
    return title.rsplit(" - ", 1)[-1].strip()


def _clean_news_title(title, source):
    if not title:
        return None
    cleaned = " ".join(title.split())
    if source and cleaned.endswith(f" - {source}"):
        cleaned = cleaned[: -len(f" - {source}")].strip()
    return cleaned


def _parse_rss_news_item(item):
    raw_title = _rss_child_text(item, "title")
    link = _rss_child_text(item, "link")
    published_at = _rss_child_text(item, "pubDate")
    source = _rss_child_text(item, "source") or _news_source_from_title(raw_title)
    title = _clean_news_title(raw_title, source)

    if not title or not link or not published_at:
        return None

    return {
        "title": title,
        "source": source or "Unknown",
        "published_at": published_at,
        "url": link,
    }


def get_news_feed_urls(ticker):
    query = quote_plus(f"{ticker} stock")
    return [
        (
            "https://news.google.com/rss/search"
            f"?q={query}+when:3d&hl=en-US&gl=US&ceid=US:en"
        ),
        (
            "https://feeds.finance.yahoo.com/rss/2.0/headline"
            f"?s={quote_plus(ticker)}&region=US&lang=en-US"
        ),
    ]


def fetch_rss_news(url):
    response = requests.get(
        url,
        headers={"User-Agent": "InvestJarTechnicalRadar/1.0"},
        timeout=NEWS_REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    news = []
    for item in root.findall(".//item"):
        parsed = _parse_rss_news_item(item)
        if parsed is not None:
            news.append(parsed)

    return news


def get_recent_news(ticker):
    earliest = datetime.now().astimezone() - timedelta(hours=NEWS_LOOKBACK_HOURS)
    recent_news = []
    seen_titles = set()

    for url in get_news_feed_urls(ticker):
        try:
            feed_items = fetch_rss_news(url)
        except Exception as exc:
            print(f"Warning: {ticker} RSS 新聞讀取失敗，略過來源：{exc}")
            continue

        for item in feed_items:
            published_at = _parse_news_publish_time(item)
            if published_at is None:
                continue
            if published_at.tzinfo is None:
                published_at = published_at.astimezone()
            if published_at < earliest:
                continue

            normalized_title = _normalize_news_title(item["title"])
            if not normalized_title or normalized_title in seen_titles:
                continue

            seen_titles.add(normalized_title)
            item["published_at"] = published_at.date().isoformat()
            recent_news.append(item)

            if len(recent_news) >= MAX_WHY_DID_IT_MOVE_NEWS:
                return recent_news

    return recent_news


def format_why_did_it_move_news(news_items):
    if not news_items:
        return ["近期未找到明確新聞。"]

    lines = ["近期新聞："]
    for index, news in enumerate(news_items, 1):
        lines.extend(
            [
                f"{index}. {news['title']}",
                f"   Source: {news['source']} / {news['published_at']}",
                f"   URL: {news['url']}",
            ]
        )
        if index < len(news_items):
            lines.append("")

    return lines


def build_why_did_it_move(results):
    candidates = get_why_did_it_move_candidates(results)
    if not candidates:
        return ""

    lines = ["🚨 Why Did It Move"]
    for index, result in enumerate(candidates):
        ticker = result["ticker"]
        if index > 0:
            lines.extend(["", "---"])

        lines.extend(["", f"{ticker} {result['change_pct']:+.1f}%", ""])
        try:
            news_items = get_recent_news(ticker)
        except Exception as exc:
            print(f"Warning: {ticker} Why Did It Move 新聞讀取失敗：{exc}")
            news_items = []

        lines.extend(format_why_did_it_move_news(news_items))

    return "\n".join(lines)


def build_message():
    try:
        tickers = get_tickers_from_sheets()
    except Exception:
        return None

    if not tickers:
        print("Google Sheets 中沒有 active=True 的 ticker，不送出 LINE 訊息。")
        return None

    results = [analyze_ticker(ticker) for ticker in tickers]
    try:
        event_radar = build_event_radar(tickers)
    except Exception as exc:
        print(f"Warning: Event Radar 建立失敗：{exc}")
        event_radar = "📅 Event Radar\n今日無新的或當週重要事件。"

    why_did_it_move = build_why_did_it_move(results)
    sections = [
        build_daily_watchlist(results),
        build_technical_alerts(results),
    ]
    if why_did_it_move:
        sections.append(why_did_it_move)
    sections.append(event_radar)

    message = "\n\n".join(sections)

    if len(message) > MAX_MESSAGE_LENGTH:
        keep_length = MAX_MESSAGE_LENGTH - len(TRUNCATION_NOTICE) - 1
        message = f"{message[:keep_length].rstrip()}\n{TRUNCATION_NOTICE}"

    return message


def send_line_message(text):
    if not text or not text.strip():
        print("LINE 訊息為空，不送出。")
        return

    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.getenv("LINE_USER_ID")

    if not token or not user_id:
        print("LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID 沒有設定，不送出訊息。")
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

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        print(f"LINE 訊息發送成功，status={response.status_code}")
    except requests.RequestException as exc:
        print(f"LINE 訊息發送失敗：{exc}")
        if getattr(exc, "response", None) is not None:
            print(exc.response.text)


def main():
    message = build_message()
    if not message:
        return

    print(message)
    send_line_message(message)


if __name__ == "__main__":
    main()
