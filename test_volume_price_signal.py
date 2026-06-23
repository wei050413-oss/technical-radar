import unittest
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

import main
from main import (
    TAIPEI_TIMEZONE,
    analyze_ticker,
    build_event_radar,
    build_technical_alerts,
    calculate_volume_ratio,
    get_upcoming_events,
    get_volume_price_signal,
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

    def test_volume_shrink_up(self):
        self.assertSignal(
            3.0,
            0.7,
            "⚠️ 量縮上漲（0.7x Avg Volume）",
            "medium",
        )

    def test_strong_volume_shrink_down(self):
        self.assertSignal(
            -5.0,
            0.7,
            "📉 強勢量縮下跌（0.7x Avg Volume）",
            "medium",
        )

    def test_volume_shrink_down(self):
        self.assertSignal(
            -3.0,
            0.7,
            "📉 量縮下跌（0.7x Avg Volume）",
            "medium",
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

    def test_volume_expansion_up_is_high_priority(self):
        message = self.build_alerts(
            high_alerts=[
                "突破近20日高點",
                "創52週新高",
                "剛站上50MA",
                "RSI breakout",
                "📈 放量上漲（2.1x Avg Volume）",
            ]
        )

        self.assertIn("🔥 High Priority", message)
        self.assertIn("- 📈 放量上漲（2.1x Avg Volume）", message)

    def test_volume_expansion_down_is_high_priority(self):
        message = self.build_alerts(
            high_alerts=["⚠️ 放量下跌（2.1x Avg Volume）"]
        )

        self.assertIn("🔥 High Priority", message)
        self.assertIn("- ⚠️ 放量下跌（2.1x Avg Volume）", message)

    def test_volume_shrink_up_is_medium_priority(self):
        message = self.build_alerts(
            medium_alerts=["⚠️ 量縮上漲（0.7x Avg Volume）"]
        )

        self.assertIn("⚠️ Medium Priority", message)
        self.assertIn("- ⚠️ 量縮上漲（0.7x Avg Volume）", message)

    def test_volume_shrink_down_is_medium_priority(self):
        message = self.build_alerts(
            medium_alerts=["📉 量縮下跌（0.7x Avg Volume）"]
        )

        self.assertIn("⚠️ Medium Priority", message)
        self.assertIn("- 📉 量縮下跌（0.7x Avg Volume）", message)

    def test_normal_volume_is_not_displayed(self):
        message = self.build_alerts()

        self.assertNotIn("正常量能", message)
        self.assertEqual(message, "🚨 Technical Alerts\n今日無重大技術訊號。")

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

    def test_analyze_appends_volume_shrink_up_to_medium_alerts(self):
        result = self.analyze_with_price_data(103.0, 10.0)

        self.assertIn("⚠️ 量縮上漲（0.1x Avg Volume）", result["medium_alerts"])

    def test_analyze_appends_volume_shrink_down_to_medium_alerts(self):
        result = self.analyze_with_price_data(97.0, 10.0)

        self.assertIn("📉 量縮下跌（0.1x Avg Volume）", result["medium_alerts"])

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
            medium_alerts=["⚠️ 量縮上漲（0.7x Avg Volume）"],
        )

        self.assertIn("- ⚠️ 量縮上漲（0.7x Avg Volume）", message)


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


if __name__ == "__main__":
    unittest.main()
