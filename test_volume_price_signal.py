import unittest
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

import main
from main import (
    TAIPEI_TIMEZONE,
    analyze_ticker,
    build_event_radar,
    build_message,
    build_technical_alerts,
    build_why_did_it_move,
    calculate_volume_ratio,
    format_why_did_it_move_news,
    get_recent_news,
    get_upcoming_events,
    get_volume_price_signal,
    get_why_did_it_move_candidates,
)


class VolumePriceSignalTest(unittest.TestCase):
    def assertSignal(self, change_pct, volume_ratio, label, priority):
        self.assertEqual(
            get_volume_price_signal(change_pct, volume_ratio),
            {"label": label, "priority": priority},
        )

    def test_strong_volume_expansion_up(self):
        self.assertSignal(
            5.0,
            2.1,
            "🔥 強勢放量上漲（2.1x Avg Volume）",
            "high",
        )

    def test_volume_expansion_up(self):
        self.assertSignal(
            3.0,
            2.1,
            "📈 放量上漲（2.1x Avg Volume）",
            "high",
        )

    def test_strong_volume_expansion_down(self):
        self.assertSignal(
            -5.0,
            2.1,
            "🚨 強勢放量下跌（2.1x Avg Volume）",
            "high",
        )

    def test_volume_expansion_down(self):
        self.assertSignal(
            -3.0,
            2.1,
            "⚠️ 放量下跌（2.1x Avg Volume）",
            "high",
        )

    def test_strong_volume_shrink_up(self):
        self.assertSignal(
            5.0,
            0.7,
            "⚠️ 強勢量縮上漲（0.7x Avg Volume）",
            "medium",
        )

    def test_volume_shrink_up_is_not_alerted(self):
        self.assertSignal(
            3.0,
            0.7,
            None,
            "none",
        )

    def test_strong_volume_shrink_down(self):
        self.assertSignal(
            -5.0,
            0.7,
            "📉 強勢量縮下跌（0.7x Avg Volume）",
            "medium",
        )

    def test_volume_shrink_down_is_not_alerted(self):
        self.assertSignal(
            -3.0,
            0.7,
            None,
            "none",
        )

    def test_normal_volume_up_is_not_alerted(self):
        self.assertSignal(
            3.0,
            1.2,
            "➖ 正常量能上漲（1.2x Avg Volume）",
            "none",
        )

    def test_normal_volume_down_is_not_alerted(self):
        self.assertSignal(
            -3.0,
            1.2,
            "➖ 正常量能下跌（1.2x Avg Volume）",
            "none",
        )

    def test_flat_price_is_not_alerted(self):
        self.assertSignal(
            0.0,
            2.1,
            "➖ 價格持平（2.1x Avg Volume）",
            "none",
        )

    def test_missing_avg_volume_does_not_create_signal(self):
        volume_ratio = calculate_volume_ratio(100, None)
        self.assertIsNone(volume_ratio)
        self.assertSignal(None, volume_ratio, None, "none")

    def test_zero_avg_volume_does_not_create_signal(self):
        volume_ratio = calculate_volume_ratio(100, 0)
        self.assertIsNone(volume_ratio)
        self.assertSignal(3.0, volume_ratio, None, "none")


class TechnicalAlertsTest(unittest.TestCase):
    def build_alerts(self, high_alerts=None, medium_alerts=None):
        return build_technical_alerts(
            [
                {
                    "ticker": "TEST",
                    "high_alerts": high_alerts or [],
                    "medium_alerts": medium_alerts or [],
                }
            ]
        )

    def test_volume_expansion_up_is_displayed_without_priority_heading(self):
        message = self.build_alerts(
            high_alerts=[
                "突破近20日高點",
                "創52週新高",
                "剛站上50MA",
                "RSI breakout",
                "📈 放量上漲（2.1x Avg Volume）",
            ]
        )

        self.assertNotIn("🔥 High Priority", message)
        self.assertNotIn("⚠️ Medium Priority", message)
        self.assertIn("TEST", message)
        self.assertIn("- 📈 放量上漲（2.1x Avg Volume）", message)

    def test_volume_expansion_down_is_displayed(self):
        message = self.build_alerts(
            high_alerts=["⚠️ 放量下跌（2.1x Avg Volume）"]
        )

        self.assertIn("- ⚠️ 放量下跌（2.1x Avg Volume）", message)

    def test_strong_volume_shrink_up_is_displayed(self):
        message = self.build_alerts(
            medium_alerts=["⚠️ 強勢量縮上漲（0.7x Avg Volume）"]
        )

        self.assertIn("- ⚠️ 強勢量縮上漲（0.7x Avg Volume）", message)

    def test_strong_volume_shrink_down_is_displayed(self):
        message = self.build_alerts(
            medium_alerts=["📉 強勢量縮下跌（0.7x Avg Volume）"]
        )

        self.assertIn("- 📉 強勢量縮下跌（0.7x Avg Volume）", message)

    def test_high_and_medium_alerts_are_grouped_under_same_ticker_once(self):
        message = self.build_alerts(
            high_alerts=["跌破近20日低點"],
            medium_alerts=["RSI 23.1，超賣"],
        )

        self.assertEqual(message.count("TEST"), 1)
        self.assertIn("TEST\n- 跌破近20日低點\n- RSI 23.1，超賣", message)

    def test_regular_volume_shrink_up_is_not_displayed(self):
        signal = get_volume_price_signal(3.0, 0.7)
        message = self.build_alerts(
            medium_alerts=[] if signal["priority"] == "none" else [signal["label"]]
        )

        self.assertEqual(signal, {"label": None, "priority": "none"})
        self.assertNotIn("量縮上漲", message)

    def test_regular_volume_shrink_down_is_not_displayed(self):
        signal = get_volume_price_signal(-3.0, 0.7)
        message = self.build_alerts(
            medium_alerts=[] if signal["priority"] == "none" else [signal["label"]]
        )

        self.assertEqual(signal, {"label": None, "priority": "none"})
        self.assertNotIn("量縮下跌", message)

    def test_normal_volume_is_not_displayed(self):
        message = self.build_alerts()

        self.assertNotIn("正常量能", message)
        self.assertEqual(message, "🚨 Technical Alerts\n\nNo alerts today.")

    def test_missing_avg_volume_does_not_error_or_display(self):
        volume_ratio = calculate_volume_ratio(100, float("nan"))
        signal = get_volume_price_signal(3.0, volume_ratio)
        message = self.build_alerts(
            high_alerts=[] if signal["priority"] == "none" else [signal["label"]]
        )

        self.assertIsNone(volume_ratio)
        self.assertEqual(signal, {"label": None, "priority": "none"})
        self.assertNotIn("Avg Volume", message)

    def price_data(self, latest_close, latest_volume):
        close = [100.0] * 59 + [latest_close]
        volume = [100.0] * 59 + [latest_volume]
        return pd.DataFrame({"close": close, "volume": volume})

    def analyze_with_price_data(self, latest_close, latest_volume):
        with patch(
            "main.get_price_data",
            return_value=self.price_data(latest_close, latest_volume),
        ):
            return analyze_ticker("TEST")

    def test_analyze_appends_volume_expansion_up_to_high_alerts(self):
        result = self.analyze_with_price_data(103.0, 210.0)

        self.assertIn("📈 放量上漲（2.0x Avg Volume）", result["high_alerts"])

    def test_analyze_appends_volume_expansion_down_to_high_alerts(self):
        result = self.analyze_with_price_data(97.0, 210.0)

        self.assertIn("⚠️ 放量下跌（2.0x Avg Volume）", result["high_alerts"])

    def test_analyze_appends_strong_volume_shrink_up_to_medium_alerts(self):
        result = self.analyze_with_price_data(106.0, 10.0)

        self.assertIn("⚠️ 強勢量縮上漲（0.1x Avg Volume）", result["medium_alerts"])

    def test_analyze_does_not_append_regular_volume_shrink_up(self):
        result = self.analyze_with_price_data(103.0, 10.0)

        self.assertNotIn("⚠️ 量縮上漲（0.1x Avg Volume）", result["medium_alerts"])

    def test_analyze_appends_strong_volume_shrink_down_to_medium_alerts(self):
        result = self.analyze_with_price_data(94.0, 10.0)

        self.assertIn("📉 強勢量縮下跌（0.1x Avg Volume）", result["medium_alerts"])

    def test_analyze_does_not_append_regular_volume_shrink_down(self):
        result = self.analyze_with_price_data(97.0, 10.0)

        self.assertNotIn("📉 量縮下跌（0.1x Avg Volume）", result["medium_alerts"])

    def test_analyze_does_not_append_normal_volume_signal(self):
        result = self.analyze_with_price_data(103.0, 120.0)
        all_alerts = result["high_alerts"] + result["medium_alerts"]

        self.assertFalse(any("正常量能" in alert for alert in all_alerts))

    def test_analyze_ignores_missing_avg_volume(self):
        df = self.price_data(103.0, float("nan"))
        df["volume"] = float("nan")
        with patch("main.get_price_data", return_value=df):
            result = analyze_ticker("TEST")
        all_alerts = result["high_alerts"] + result["medium_alerts"]

        self.assertIsNone(result["error"])
        self.assertFalse(any("Avg Volume" in alert for alert in all_alerts))

    def test_medium_volume_signal_survives_when_high_alerts_are_full(self):
        message = self.build_alerts(
            high_alerts=[
                "突破近20日高點",
                "創52週新高",
                "剛站上50MA",
                "其他高優先訊號",
            ],
            medium_alerts=["⚠️ 強勢量縮上漲（0.7x Avg Volume）"],
        )

        self.assertIn("- ⚠️ 強勢量縮上漲（0.7x Avg Volume）", message)


class EventRadarTest(unittest.TestCase):
    def test_upcoming_events_excludes_past_events(self):
        events = [
            {"id": "past", "name": "CPI", "date": "2026-06-12"},
            {"id": "future", "name": "MU 財報", "date": "2026-06-24"},
        ]

        upcoming = get_upcoming_events(events, date(2026, 6, 23))

        self.assertEqual([event["id"] for event in upcoming], ["future"])

    def test_event_radar_keeps_today_and_future_events(self):
        events = [
            {
                "id": "today",
                "name": "今日事件",
                "date": "2026-06-23",
                "session": "unknown",
            },
            {
                "id": "future",
                "name": "未來事件",
                "date": "2026-06-24",
                "session": "unknown",
            },
            {
                "id": "past",
                "name": "過期事件",
                "date": "2026-06-22",
                "session": "unknown",
            },
        ]

        with (
            patch("main.get_today_taipei", return_value=date(2026, 6, 23)),
            patch("main.get_earnings_events", return_value=[]),
            patch("main.get_macro_events", return_value=events),
            patch("main.load_event_state", return_value={"notified_event_ids": []}),
            patch("main.save_event_state"),
        ):
            message = build_event_radar([])

        self.assertIn("今日事件：6/23", message)
        self.assertIn("未來事件：6/24", message)
        self.assertNotIn("過期事件", message)

    def test_event_radar_fills_missing_year_with_current_taipei_year(self):
        events = [
            {
                "id": "future_yearless",
                "name": "未來無年份事件",
                "date": "6/24",
                "session": "unknown",
            },
            {
                "id": "past_yearless",
                "name": "過期無年份事件",
                "date": "6/12",
                "session": "unknown",
            },
        ]

        with (
            patch("main.get_today_taipei", return_value=date(2026, 6, 23)),
            patch("main.get_earnings_events", return_value=[]),
            patch("main.get_macro_events", return_value=events),
            patch("main.load_event_state", return_value={"notified_event_ids": []}),
            patch("main.save_event_state"),
        ):
            message = build_event_radar([])

        self.assertIn("未來無年份事件：6/24", message)
        self.assertNotIn("過期無年份事件", message)

    def test_today_uses_asia_taipei_timezone(self):
        fixed_now = datetime(2026, 6, 23, 1, 0, tzinfo=TAIPEI_TIMEZONE)
        with patch("main.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_now
            today = main.get_today_taipei()

        mocked_datetime.now.assert_called_once_with(TAIPEI_TIMEZONE)
        self.assertEqual(today, date(2026, 6, 23))


class WhyDidItMoveTest(unittest.TestCase):
    def result(self, ticker, change_pct, close=100.0):
        return {
            "ticker": ticker,
            "close": close,
            "change_pct": change_pct,
            "volume_ratio": 1.8,
            "high_alerts": ["📈 放量上漲（1.8x Avg Volume）"],
            "medium_alerts": [],
            "error": None,
        }

    def test_change_over_five_percent_triggers(self):
        candidates = get_why_did_it_move_candidates(
            [self.result("BE", 5.1)]
        )

        self.assertEqual([item["ticker"] for item in candidates], ["BE"])

    def test_change_below_five_percent_does_not_trigger(self):
        candidates = get_why_did_it_move_candidates(
            [self.result("BE", 4.9)]
        )

        self.assertEqual(candidates, [])

    def test_only_top_five_by_absolute_change_are_used(self):
        results = [
            self.result("A", 5.1),
            self.result("B", -9.0),
            self.result("C", 7.0),
            self.result("D", -6.0),
            self.result("E", 12.0),
            self.result("F", 8.0),
        ]

        candidates = get_why_did_it_move_candidates(results)

        self.assertEqual(
            [item["ticker"] for item in candidates],
            ["E", "B", "F", "C", "D"],
        )

    def news(self, index):
        return {
            "title": f"News title {index}",
            "source": "Yahoo Finance",
            "published_at": "2026-06-24",
            "url": f"https://example.com/news-{index}",
        }

    def test_each_ticker_displays_at_most_three_news_items(self):
        news_items = [self.news(1), self.news(2), self.news(3), self.news(4)]
        with patch("main.get_recent_news", return_value=news_items[:3]):
            section = build_why_did_it_move([self.result("BE", 15.4)])

        self.assertIn("1. News title 1", section)
        self.assertIn("2. News title 2", section)
        self.assertIn("3. News title 3", section)
        self.assertNotIn("4. News title 4", section)

    def test_get_recent_news_returns_at_most_three_items(self):
        news_items = [
            {
                **self.news(index),
                "published_at": "Wed, 24 Jun 2026 12:00:00 GMT",
            }
            for index in range(1, 5)
        ]
        with (
            patch("main.get_news_feed_urls", return_value=["https://example.com/rss"]),
            patch("main.fetch_rss_news", return_value=news_items),
        ):
            recent_news = get_recent_news("BE")

        self.assertEqual(len(recent_news), 3)
        self.assertEqual([item["title"] for item in recent_news], [
            "News title 1",
            "News title 2",
            "News title 3",
        ])

    def test_get_recent_news_continues_when_one_feed_fails(self):
        news_item = {
            **self.news(1),
            "published_at": "Wed, 24 Jun 2026 12:00:00 GMT",
        }

        def fake_fetch(url):
            if "google" in url:
                raise RuntimeError("feed down")
            return [news_item]

        with (
            patch(
                "main.get_news_feed_urls",
                return_value=["https://google.example/rss", "https://yahoo.example/rss"],
            ),
            patch("main.fetch_rss_news", side_effect=fake_fetch),
        ):
            recent_news = get_recent_news("BE")

        self.assertEqual(len(recent_news), 1)
        self.assertEqual(recent_news[0]["title"], "News title 1")

    def test_missing_news_still_outputs_section(self):
        with patch("main.get_recent_news", return_value=[]):
            section = build_why_did_it_move([self.result("BE", 15.4)])

        self.assertIn("BE +15.4%", section)
        self.assertIn("近期未找到明確新聞。", section)

    def test_news_output_contains_title_source_date_and_url(self):
        lines = format_why_did_it_move_news([self.news(1)])
        output = "\n".join(lines)

        self.assertIn("1. News title 1", output)
        self.assertIn("Source: Yahoo Finance / 2026-06-24", output)
        self.assertIn("URL: https://example.com/news-1", output)

    def test_news_failure_does_not_block_full_message(self):
        with (
            patch("main.get_tickers_from_sheets", return_value=["BE"]),
            patch("main.analyze_ticker", return_value=self.result("BE", 15.4)),
            patch("main.build_event_radar", return_value="📅 Event Radar\nOK"),
            patch("main.get_recent_news", side_effect=RuntimeError("news down")),
        ):
            message = build_message()

        self.assertIn("📈 Daily Watchlist", message)
        self.assertIn("🚨 Technical Alerts", message)
        self.assertIn("近期未找到明確新聞。", message)
        self.assertIn("📅 Event Radar", message)


if __name__ == "__main__":
    unittest.main()
