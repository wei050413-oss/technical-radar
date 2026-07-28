import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import requests
from urllib3.exceptions import InsecureRequestWarning


HTTP_TIMEOUT = 15
HTTP_RETRIES = 2
TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_BFI82U_URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
TWSE_BASIC_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TWSE_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
TPEX_DAILY_TRADE_URL = (
    "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php"
)
TPEX_SUMMARY_URL = (
    "https://www.tpex.org.tw/web/stock/3insti/3insti_summary/"
    "3itrdsum_result.php"
)
TPEX_BASIC_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TPEX_LATEST_DAILY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
TPEX_LATEST_SUMMARY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_summary"
YUAN_PER_YI = Decimal("100000000")
SHARES_PER_LOT = 1000
UNAVAILABLE_MESSAGE = "資料尚未公布或暫時無法取得。"
INDUSTRY_ALIAS_PATH = Path(__file__).with_name("tw_industry_aliases.json")

INDUSTRY_CODE_LABELS = {
    "01": "水泥",
    "02": "食品",
    "03": "塑膠",
    "04": "紡織",
    "05": "電機機械",
    "06": "電器電纜",
    "07": "化學生技醫療",
    "08": "玻璃陶瓷",
    "09": "造紙",
    "10": "鋼鐵",
    "11": "橡膠",
    "12": "汽車",
    "14": "建材營造",
    "15": "航運",
    "16": "觀光餐旅",
    "17": "金融",
    "18": "貿易百貨",
    "20": "其他",
    "21": "化學",
    "22": "生技醫療",
    "23": "油電燃氣",
    "24": "半導體",
    "25": "電腦週邊",
    "26": "光電",
    "27": "通訊網路",
    "28": "電子零組件",
    "29": "電子通路",
    "30": "資訊服務",
    "31": "其他電子",
    "32": "文化創意",
    "33": "農業科技",
    "34": "電子商務",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}


class InstitutionalDataUnavailable(Exception):
    pass


class InstitutionalDataPending(Exception):
    pass


@dataclass
class MarketInstitutionalData:
    market: str
    as_of_date: date
    amounts: dict
    rows: list


def parse_int(value):
    text = str(value or "").strip().replace(",", "")
    if text in {"", "-", "--"}:
        return 0
    return int(text)


def parse_roc_date(value):
    text = str(value).strip()
    if re.fullmatch(r"\d{7}", text):
        return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7]))
    if re.fullmatch(r"\d{8}", text):
        year = int(text[:4])
        if year < 1911:
            year += 1911
        return date(year, int(text[4:6]), int(text[6:8]))
    match = re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return date(year + 1911, month, day)
    raise ValueError(f"無效民國日期：{value}")


def format_twse_date(value):
    return value.strftime("%Y%m%d")


def format_tpex_roc_date(value):
    return f"{value.year - 1911}/{value.month:02d}/{value.day:02d}"


def request_json(url, params=None, timeout=HTTP_TIMEOUT, retries=HTTP_RETRIES):
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.SSLError as exc:
            last_error = exc
            print(f"Warning: {url} SSL 驗證失敗，改用未驗證連線重試：{exc}")
            try:
                requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
                response = requests.get(
                    url,
                    params=params,
                    timeout=timeout,
                    headers={"User-Agent": "Mozilla/5.0"},
                    verify=False,
                )
                response.raise_for_status()
                return response.json()
            except (ValueError, requests.RequestException) as retry_exc:
                last_error = retry_exc
        except (ValueError, requests.RequestException) as exc:
            last_error = exc
        if attempt < retries:
            continue
    raise InstitutionalDataUnavailable(str(last_error))


def _row_dict(fields, row):
    return {
        str(field).strip(): value
        for field, value in zip(fields, row)
    }


def _get_field(row, *names):
    normalized = {key.replace(" ", ""): value for key, value in row.items()}
    for name in names:
        key = name.replace(" ", "")
        if key in normalized:
            return normalized[key]
    return 0


def normalize_twse_amounts(payload):
    if payload.get("stat") != "OK" or not payload.get("data"):
        raise InstitutionalDataPending(payload.get("stat", "no data"))

    rows = [_row_dict(payload["fields"], row) for row in payload["data"]]
    amounts = {
        "foreign": 0,
        "trust": 0,
        "dealer_self": 0,
        "dealer_hedge": 0,
    }
    for row in rows:
        name = str(row.get("單位名稱", "")).strip()
        net = parse_int(row.get("買賣差額"))
        if name.startswith("外資及陸資"):
            amounts["foreign"] = net
        elif name == "投信":
            amounts["trust"] = net
        elif name == "自營商(自行買賣)":
            amounts["dealer_self"] = net
        elif name == "自營商(避險)":
            amounts["dealer_hedge"] = net
    return amounts


def normalize_twse_trades(payload):
    if payload.get("stat") != "OK" or not payload.get("data"):
        raise InstitutionalDataPending(payload.get("stat", "no data"))

    as_of_date = parse_roc_date(payload["date"])
    rows = []
    for raw in payload["data"]:
        row = _row_dict(payload["fields"], raw)
        foreign = parse_int(
            row.get("外陸資買賣超股數(不含外資自營商)")
        )
        trust = parse_int(row.get("投信買賣超股數"))
        dealer_self = parse_int(row.get("自營商買賣超股數(自行買賣)"))
        dealer_hedge = parse_int(row.get("自營商買賣超股數(避險)"))
        rows.append(
            {
                "market": "TWSE",
                "symbol": str(row.get("證券代號", "")).strip(),
                "name": str(row.get("證券名稱", "")).strip(),
                "foreign_net_shares": foreign,
                "trust_net_shares": trust,
                "dealer_self_net_shares": dealer_self,
                "dealer_hedge_net_shares": dealer_hedge,
                "total_net_shares": foreign + trust + dealer_self + dealer_hedge,
            }
        )
    return as_of_date, rows


def get_twse_institutional_data(target_date):
    day = format_twse_date(target_date)
    amounts_payload = request_json(
        TWSE_BFI82U_URL,
        {"dayDate": day, "response": "json"},
    )
    trades_payload = request_json(
        TWSE_T86_URL,
        {"date": day, "selectType": "ALLBUT0999", "response": "json"},
    )
    as_of_date, rows = normalize_twse_trades(trades_payload)
    return MarketInstitutionalData(
        "TWSE",
        as_of_date,
        normalize_twse_amounts(amounts_payload),
        rows,
    )


def _normalize_tpex_summary_rows(rows):
    amounts = {
        "foreign": 0,
        "trust": 0,
        "dealer_self": 0,
        "dealer_hedge": 0,
    }
    for row in rows:
        name = str(row.get("Investor", row.get("單位名稱", ""))).strip()
        net = parse_int(row.get("Net", row.get("買賣超", row.get("買賣超(元)"))))
        if name.startswith("外資及陸資合計") or name.startswith("外資及陸資("):
            amounts["foreign"] = net
        elif name == "投信":
            amounts["trust"] = net
        elif name == "自營商(自行買賣)":
            amounts["dealer_self"] = net
        elif name == "自營商(避險)":
            amounts["dealer_hedge"] = net
    return amounts


def normalize_tpex_amounts(payload):
    if isinstance(payload, list):
        if not payload:
            raise InstitutionalDataPending("no data")
        return parse_roc_date(payload[0]["Date"]), _normalize_tpex_summary_rows(payload)

    tables = payload.get("tables") or []
    table = tables[0] if tables else payload
    rows = table.get("data") or payload.get("aaData") or payload.get("data") or []
    if not rows:
        raise InstitutionalDataPending(payload.get("reportTitle", "no data"))
    fields = table.get("fields") or payload.get("fields") or []
    dict_rows = [_row_dict(fields, row) for row in rows]
    report_date = table.get("date") or payload.get("reportDate") or payload.get("date")
    return parse_roc_date(report_date), _normalize_tpex_summary_rows(dict_rows)


def normalize_tpex_trades(payload):
    if isinstance(payload, list):
        if not payload:
            raise InstitutionalDataPending("no data")
        as_of_date = parse_roc_date(payload[0]["Date"])
        rows = []
        for row in payload:
            foreign = parse_int(
                _get_field(
                    row,
                    "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference",
                    "ForeignInvestorsInclude MainlandAreaInvestors-Difference",
                )
            )
            trust = parse_int(
                _get_field(row, "SecuritiesInvestmentTrustCompanies-Difference")
            )
            dealer_total = parse_int(_get_field(row, "Dealers-Difference"))
            rows.append(
                {
                    "market": "TPEx",
                    "symbol": str(row.get("SecuritiesCompanyCode", "")).strip(),
                    "name": str(row.get("CompanyName", "")).strip(),
                    "foreign_net_shares": foreign,
                    "trust_net_shares": trust,
                    "dealer_self_net_shares": dealer_total,
                    "dealer_hedge_net_shares": 0,
                    "total_net_shares": foreign + trust + dealer_total,
                }
            )
        return as_of_date, rows

    tables = payload.get("tables") or []
    table = tables[0] if tables else payload
    rows = table.get("data") or payload.get("aaData") or []
    if not rows:
        raise InstitutionalDataPending(payload.get("stat", "no data"))
    as_of_date = parse_roc_date(table.get("date") or payload.get("reportDate"))
    normalized_rows = []
    for raw in rows:
        foreign = parse_int(raw[4])
        trust = parse_int(raw[13])
        dealer_self = parse_int(raw[16])
        dealer_hedge = parse_int(raw[19])
        normalized_rows.append(
            {
                "market": "TPEx",
                "symbol": str(raw[0]).strip(),
                "name": str(raw[1]).strip(),
                "foreign_net_shares": foreign,
                "trust_net_shares": trust,
                "dealer_self_net_shares": dealer_self,
                "dealer_hedge_net_shares": dealer_hedge,
                "total_net_shares": foreign + trust + dealer_self + dealer_hedge,
            }
        )
    return as_of_date, normalized_rows


def get_tpex_institutional_data(target_date):
    day = format_tpex_roc_date(target_date)
    summary_payload = request_json(
        TPEX_SUMMARY_URL,
        {"l": "zh-tw", "t": "D", "p": "1", "d": day, "o": "json"},
    )
    trades_payload = request_json(
        TPEX_DAILY_TRADE_URL,
        {"l": "zh-tw", "o": "json", "se": "EW", "t": "D", "d": day, "s": "0,asc"},
    )
    summary_date, amounts = normalize_tpex_amounts(summary_payload)
    trades_date, rows = normalize_tpex_trades(trades_payload)
    if summary_date != trades_date:
        raise InstitutionalDataUnavailable(
            f"TPEx summary/trades date mismatch: {summary_date} != {trades_date}"
        )
    return MarketInstitutionalData("TPEx", trades_date, amounts, rows)


def calculate_institutional_amounts(markets):
    totals = {
        "foreign": 0,
        "trust": 0,
        "dealer_self": 0,
        "dealer_hedge": 0,
    }
    for market in markets:
        for key in totals:
            totals[key] += int(market.amounts.get(key, 0))
    totals["dealer"] = totals["dealer_self"] + totals["dealer_hedge"]
    totals["total"] = totals["foreign"] + totals["trust"] + totals["dealer"]
    return totals


def calculate_institutional_rankings(rows, common_stock_universe, limit=5):
    filtered = [
        row
        for row in rows
        if (row["market"], row["symbol"]) in common_stock_universe
        and row.get("total_net_shares") is not None
    ]
    buy = sorted(
        (row for row in filtered if row["total_net_shares"] > 0),
        key=lambda row: row["total_net_shares"],
        reverse=True,
    )[:limit]
    sell = sorted(
        (row for row in filtered if row["total_net_shares"] < 0),
        key=lambda row: row["total_net_shares"],
    )[:limit]
    return {"buy": buy, "sell": sell}


def format_signed_yi(amount_yuan):
    amount = Decimal(int(amount_yuan)) / YUAN_PER_YI
    if amount == 0:
        return "0.00 億"
    return f"{amount:+.2f} 億"


def format_lot_count(shares):
    lots = int(int(shares) / SHARES_PER_LOT)
    sign = "+" if lots > 0 else ""
    return f"{sign}{lots:,} 張"


def _load_industry_aliases(path=INDUSTRY_ALIAS_PATH):
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return {str(key): str(value) for key, value in data.items()}
    except (OSError, ValueError, TypeError):
        return {}


def _industry_label(industry_code):
    code = str(industry_code or "").strip().zfill(2)
    return INDUSTRY_CODE_LABELS.get(code, "")


def normalize_twse_basic_rows(rows):
    universe = {}
    for row in rows:
        symbol = str(row.get("公司代號", "")).strip()
        if symbol:
            universe[("TWSE", symbol)] = _industry_label(row.get("產業別"))
    return universe


def normalize_tpex_basic_rows(rows):
    universe = {}
    for row in rows:
        symbol = str(row.get("SecuritiesCompanyCode", "")).strip()
        if symbol:
            universe[("TPEx", symbol)] = _industry_label(
                row.get("SecuritiesIndustryCode")
            )
    return universe


def get_common_stock_universe():
    aliases = _load_industry_aliases()
    universe = {}
    twse_rows = request_json(TWSE_BASIC_URL)
    tpex_rows = request_json(TPEX_BASIC_URL)
    universe.update(normalize_twse_basic_rows(twse_rows))
    universe.update(normalize_tpex_basic_rows(tpex_rows))
    for key, value in list(universe.items()):
        alias = aliases.get(key[1])
        if alias:
            universe[key] = alias
    return universe


def _holiday_dates_from_payload(payload):
    holidays = set()
    for row in payload if isinstance(payload, list) else []:
        text = str(
            row.get("日期")
            or row.get("Date")
            or row.get("date")
            or ""
        )
        for match in re.finditer(r"\d{3,4}[/-]\d{1,2}[/-]\d{1,2}", text):
            try:
                holidays.add(parse_roc_date(match.group(0).replace("-", "/")))
            except ValueError:
                continue
    return holidays


def is_tw_trading_day(target_date):
    if target_date.weekday() >= 5:
        return False
    try:
        holidays = _holiday_dates_from_payload(request_json(TWSE_HOLIDAY_URL))
        return target_date not in holidays
    except InstitutionalDataUnavailable as exc:
        print(f"Warning: 台股休市日曆讀取失敗，改用週一至週五判斷：{exc}")
        return True


def get_market_data_for_date(target_date):
    twse = get_twse_institutional_data(target_date)
    tpex = get_tpex_institutional_data(target_date)
    if twse.as_of_date != tpex.as_of_date:
        raise InstitutionalDataUnavailable(
            f"TWSE/TPEx date mismatch: {twse.as_of_date} != {tpex.as_of_date}"
        )
    return [twse, tpex]


def collect_tw_institutional_report(today, max_lookback_days=10):
    if is_tw_trading_day(today):
        try:
            return get_market_data_for_date(today)
        except InstitutionalDataPending as exc:
            print(f"Warning: 台股法人資料尚未公布：{exc}")
            raise
        except InstitutionalDataUnavailable as exc:
            print(f"Warning: 台股法人資料讀取失敗：{exc}")
            raise

    for offset in range(1, max_lookback_days + 1):
        candidate = today - timedelta(days=offset)
        if not is_tw_trading_day(candidate):
            continue
        try:
            return get_market_data_for_date(candidate)
        except InstitutionalDataPending as exc:
            print(f"Warning: {candidate} 台股法人資料尚未公布，繼續往前查：{exc}")
            continue
        except InstitutionalDataUnavailable as exc:
            print(f"Warning: {candidate} 台股法人資料讀取失敗，繼續往前查：{exc}")
            continue
    raise InstitutionalDataUnavailable("lookback window exhausted")


def format_institutional_amount_section(as_of_date, amounts):
    lines = [f"📊 今日法人（{as_of_date.month}/{as_of_date.day}）", ""]
    lines.append(f"外資 {format_signed_yi(amounts['foreign'])}")
    lines.append(f"投信 {format_signed_yi(amounts['trust'])}")
    lines.append(f"自營商 {format_signed_yi(amounts['dealer'])}")
    lines.extend(["", f"→ 三大法人合計 {format_signed_yi(amounts['total'])}"])
    return "\n".join(lines)


def format_institutional_ranking_section(rankings, industry_by_key):
    lines = ["💰 法人買賣超排行", "", "買超前五名"]
    if rankings["buy"]:
        lines.extend(
            format_ranking_line(index, row, industry_by_key)
            for index, row in enumerate(rankings["buy"], start=1)
        )
    else:
        lines.append("無資料")

    lines.extend(["", "賣超前五名"])
    if rankings["sell"]:
        lines.extend(
            format_ranking_line(index, row, industry_by_key)
            for index, row in enumerate(rankings["sell"], start=1)
        )
    else:
        lines.append("無資料")

    return "\n".join(lines)


def format_ranking_line(index, row, industry_by_key):
    industry = industry_by_key.get((row["market"], row["symbol"]), "")
    suffix = f"｜{industry}" if industry else ""
    return (
        f"{index}. {row['name']} "
        f"{format_lot_count(row['total_net_shares'])}{suffix}"
    )


def build_unavailable_institutional_sections():
    return f"📊 今日法人\n\n{UNAVAILABLE_MESSAGE}"


def build_tw_institutional_sections(today):
    try:
        markets = collect_tw_institutional_report(today)
        common_stock_universe = get_common_stock_universe()
    except (InstitutionalDataPending, InstitutionalDataUnavailable) as exc:
        print(f"Warning: 台股法人區塊建立失敗：{exc}")
        return [build_unavailable_institutional_sections()]

    as_of_dates = {market.as_of_date for market in markets}
    if len(as_of_dates) != 1:
        print(f"Warning: 台股法人 TWSE/TPEx 日期不同：{sorted(as_of_dates)}")
        return [build_unavailable_institutional_sections()]

    as_of_date = next(iter(as_of_dates))
    rows = [row for market in markets for row in market.rows]
    amounts = calculate_institutional_amounts(markets)
    rankings = calculate_institutional_rankings(rows, common_stock_universe)

    return [
        format_institutional_amount_section(as_of_date, amounts),
        format_institutional_ranking_section(rankings, common_stock_universe),
    ]
