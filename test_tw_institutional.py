import unittest
from datetime import date
from unittest.mock import Mock, patch

import main
import tw_institutional as twi


class TwInstitutionalCalculationTest(unittest.TestCase):
    def market(self, market, amount_offset=0):
        return twi.MarketInstitutionalData(
            market=market,
            as_of_date=date(2026, 7, 24),
            amounts={
                "foreign": 10_000_000_000 + amount_offset,
                "trust": 2_000_000_000,
                "dealer_self": -300_000_000,
                "dealer_hedge": -700_000_000,
            },
            rows=[],
        )

    def test_amount_calculation_combines_categories_and_markets(self):
        amounts = twi.calculate_institutional_amounts(
            [self.market("TWSE"), self.market("TPEx", 1_000_000_000)]
        )

        self.assertEqual(amounts["foreign"], 21_000_000_000)
        self.assertEqual(amounts["trust"], 4_000_000_000)
        self.assertEqual(amounts["dealer_self"], -600_000_000)
        self.assertEqual(amounts["dealer_hedge"], -1_400_000_000)
        self.assertEqual(amounts["dealer"], -2_000_000_000)
        self.assertEqual(amounts["total"], 23_000_000_000)

    def test_yuan_to_yi_and_sign_format(self):
        self.assertEqual(twi.format_signed_yi(18_300_000_000), "+183.00 億")
        self.assertEqual(twi.format_signed_yi(-1_800_000_000), "-18.00 億")
        self.assertEqual(twi.format_signed_yi(0), "0.00 億")

    def test_amount_section_matches_line_format(self):
        message = twi.format_institutional_amount_section(
            date(2026, 7, 28),
            {
                "foreign": 18_300_000_000,
                "trust": 3_200_000_000,
                "dealer": -1_800_000_000,
                "total": 19_700_000_000,
            },
        )

        self.assertEqual(
            message,
            "📊 今日法人（7/28）\n\n"
            "外資 +183.00 億\n"
            "投信 +32.00 億\n"
            "自營商 -18.00 億\n\n"
            "→ 三大法人合計 +197.00 億",
        )


class TwInstitutionalNormalizeTest(unittest.TestCase):
    def test_twse_and_tpex_rows_are_normalized_to_same_shape(self):
        twse_date, twse_rows = twi.normalize_twse_trades(
            {
                "stat": "OK",
                "date": "20260724",
                "fields": [
                    "證券代號",
                    "證券名稱",
                    "外陸資買賣超股數(不含外資自營商)",
                    "投信買賣超股數",
                    "自營商買賣超股數(自行買賣)",
                    "自營商買賣超股數(避險)",
                ],
                "data": [["2330", "台積電", "1,000", "2,000", "-300", "400"]],
            }
        )
        tpex_date, tpex_rows = twi.normalize_tpex_trades(
            {
                "tables": [
                    {
                        "date": "115/07/24",
                        "data": [
                            [
                                "6488",
                                "環球晶",
                                "0",
                                "0",
                                "3,000",
                                "0",
                                "0",
                                "0",
                                "0",
                                "0",
                                "0",
                                "0",
                                "0",
                                "4,000",
                                "0",
                                "0",
                                "-500",
                                "0",
                                "0",
                                "600",
                                "0",
                                "0",
                                "100",
                                "7,100",
                            ]
                        ],
                    }
                ]
            }
        )

        self.assertEqual(twse_date, date(2026, 7, 24))
        self.assertEqual(tpex_date, date(2026, 7, 24))
        self.assertEqual(twse_rows[0]["total_net_shares"], 3_100)
        self.assertEqual(tpex_rows[0]["total_net_shares"], 7_100)

    def test_tpex_openapi_trades_are_supported_when_used(self):
        as_of_date, rows = twi.normalize_tpex_trades(
            [
                {
                    "Date": "1150724",
                    "SecuritiesCompanyCode": "6488",
                    "CompanyName": "環球晶",
                    "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "3000",
                    "SecuritiesInvestmentTrustCompanies-Difference": "4000",
                    "Dealers-Difference": "100",
                }
            ]
        )

        self.assertEqual(as_of_date, date(2026, 7, 24))
        self.assertEqual(rows[0]["total_net_shares"], 7_100)

    def test_different_market_dates_cannot_be_merged(self):
        with (
            patch(
                "tw_institutional.get_twse_institutional_data",
                return_value=twi.MarketInstitutionalData(
                    "TWSE", date(2026, 7, 24), {}, []
                ),
            ),
            patch(
                "tw_institutional.get_tpex_institutional_data",
                return_value=twi.MarketInstitutionalData(
                    "TPEx", date(2026, 7, 23), {}, []
                ),
            ),
        ):
            with self.assertRaises(twi.InstitutionalDataUnavailable):
                twi.get_market_data_for_date(date(2026, 7, 24))


class TwInstitutionalRankingTest(unittest.TestCase):
    def rows(self):
        return [
            {"market": "TWSE", "symbol": "2330", "name": "台積電", "total_net_shares": 12_345_999},
            {"market": "TWSE", "symbol": "2317", "name": "鴻海", "total_net_shares": 8_210_000},
            {"market": "TWSE", "symbol": "2308", "name": "台達電", "total_net_shares": 4_530_000},
            {"market": "TPEx", "symbol": "6488", "name": "環球晶", "total_net_shares": 4_000_000},
            {"market": "TWSE", "symbol": "2454", "name": "聯發科", "total_net_shares": 3_280_000},
            {"market": "TWSE", "symbol": "2382", "name": "廣達", "total_net_shares": 2_960_000},
            {"market": "TWSE", "symbol": "00632R", "name": "元大台灣50反1", "total_net_shares": 50_000_000},
            {"market": "TWSE", "symbol": "3481", "name": "群創", "total_net_shares": -18_430_000},
            {"market": "TWSE", "symbol": "2603", "name": "長榮", "total_net_shares": -9_250_000},
            {"market": "TWSE", "symbol": "2002", "name": "中鋼", "total_net_shares": -7_810_000},
            {"market": "TWSE", "symbol": "2882", "name": "國泰金", "total_net_shares": -6_920_000},
            {"market": "TWSE", "symbol": "2303", "name": "聯電", "total_net_shares": -5_630_000},
            {"market": "TWSE", "symbol": "03001", "name": "台積電購", "total_net_shares": -99_000_000},
        ]

    def test_buy_and_sell_top_five_sorting_and_exclusion(self):
        universe = {
            ("TWSE", "2330"): "半導體",
            ("TWSE", "2317"): "電子代工",
            ("TWSE", "2308"): "電源／能源管理",
            ("TPEx", "6488"): "半導體",
            ("TWSE", "2454"): "IC 設計",
            ("TWSE", "2382"): "AI Server",
            ("TWSE", "3481"): "面板",
            ("TWSE", "2603"): "航運",
            ("TWSE", "2002"): "鋼鐵",
            ("TWSE", "2882"): "金融",
            ("TWSE", "2303"): "晶圓代工",
        }

        rankings = twi.calculate_institutional_rankings(self.rows(), universe)

        self.assertEqual(
            [row["symbol"] for row in rankings["buy"]],
            ["2330", "2317", "2308", "6488", "2454"],
        )
        self.assertEqual(
            [row["symbol"] for row in rankings["sell"]],
            ["3481", "2603", "2002", "2882", "2303"],
        )

    def test_share_to_lot_format_uses_integer_lots(self):
        self.assertEqual(twi.format_lot_count(12_345_999), "+12,345 張")
        self.assertEqual(twi.format_lot_count(-18_430_999), "-18,430 張")

    def test_ranking_section_handles_less_than_five_and_missing_industry(self):
        rankings = twi.calculate_institutional_rankings(
            [
                {"market": "TWSE", "symbol": "2330", "name": "台積電", "total_net_shares": 1_500},
                {"market": "TWSE", "symbol": "2303", "name": "聯電", "total_net_shares": -2_500},
            ],
            {("TWSE", "2330"): "半導體", ("TWSE", "2303"): ""},
        )

        message = twi.format_institutional_ranking_section(
            rankings,
            {("TWSE", "2330"): "半導體", ("TWSE", "2303"): ""},
        )

        self.assertIn("1. 台積電 +1 張｜半導體", message)
        self.assertIn("1. 聯電 -2 張", message)
        self.assertNotIn("聯電 -2 張｜", message)


class TwInstitutionalReportFlowTest(unittest.TestCase):
    def test_holiday_uses_recent_complete_trading_day(self):
        target = date(2026, 7, 26)
        friday_data = [
            twi.MarketInstitutionalData("TWSE", date(2026, 7, 24), {}, []),
            twi.MarketInstitutionalData("TPEx", date(2026, 7, 24), {}, []),
        ]

        with (
            patch("tw_institutional.is_tw_trading_day", side_effect=lambda day: day == date(2026, 7, 24)),
            patch("tw_institutional.get_market_data_for_date", return_value=friday_data) as mocked_fetch,
        ):
            result = twi.collect_tw_institutional_report(target)

        self.assertEqual(result[0].as_of_date, date(2026, 7, 24))
        mocked_fetch.assert_called_once_with(date(2026, 7, 24))

    def test_trading_day_pending_does_not_use_previous_day(self):
        with (
            patch("tw_institutional.is_tw_trading_day", return_value=True),
            patch(
                "tw_institutional.get_market_data_for_date",
                side_effect=twi.InstitutionalDataPending("no data"),
            ) as mocked_fetch,
        ):
            with self.assertRaises(twi.InstitutionalDataPending):
                twi.collect_tw_institutional_report(date(2026, 7, 24))

        mocked_fetch.assert_called_once_with(date(2026, 7, 24))

    def test_api_failure_returns_unavailable_section(self):
        with patch(
            "tw_institutional.collect_tw_institutional_report",
            side_effect=twi.InstitutionalDataUnavailable("timeout"),
        ):
            sections = twi.build_tw_institutional_sections(date(2026, 7, 24))

        self.assertEqual(
            sections,
            ["📊 今日法人\n\n資料尚未公布或暫時無法取得。"],
        )

    def test_line_message_order_for_tw_report(self):
        watchlist = [
            {"ticker": "2330.TW", "name": "台積電", "display_name": "台積電"}
        ]
        result = {
            "ticker": "2330.TW",
            "display_name": "台積電",
            "close": 1000.0,
            "change_pct": 1.23,
            "high_alerts": [],
            "medium_alerts": [],
            "error": None,
        }

        with (
            patch("main.get_watchlist_from_sheets", return_value=watchlist),
            patch("main.analyze_ticker", return_value=result),
            patch(
                "main.build_tw_institutional_sections",
                return_value=[
                    "📊 今日法人（7/24）\n\n外資 +1.00 億",
                    "💰 法人買賣超排行\n\n買超前五名\n1. 台積電 +1,000 張｜半導體",
                ],
            ),
            patch("main.build_event_radar", return_value="📅 Event Radar\n今日無新的或當週重要事件。"),
        ):
            message = main.build_message("tw")

        self.assertLess(message.index("📈 台股 Watchlist"), message.index("📊 今日法人"))
        self.assertLess(message.index("📊 今日法人"), message.index("💰 法人買賣超排行"))
        self.assertLess(message.index("💰 法人買賣超排行"), message.index("🚨 Technical Alerts"))
        self.assertLess(message.index("🚨 Technical Alerts"), message.index("📅 Event Radar"))

    def test_us_report_does_not_include_tw_institutional_sections(self):
        with (
            patch(
                "main.get_watchlist_from_sheets",
                return_value=[{"ticker": "AAPL", "name": "Apple", "display_name": "AAPL"}],
            ),
            patch(
                "main.analyze_ticker",
                return_value={
                    "ticker": "AAPL",
                    "display_name": "AAPL",
                    "close": 200.0,
                    "change_pct": 1.0,
                    "high_alerts": [],
                    "medium_alerts": [],
                    "error": None,
                },
            ),
            patch("main.build_tw_institutional_sections") as mocked_institutional,
            patch("main.build_event_radar", return_value="📅 Event Radar\n今日無新的或當週重要事件。"),
        ):
            message = main.build_message("us")

        self.assertNotIn("今日法人", message)
        mocked_institutional.assert_not_called()


if __name__ == "__main__":
    unittest.main()
