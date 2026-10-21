"""
全市場掃描「官方前一交易日成交額排行 → 縮小掃描範圍」測試：

    python scripts/test_shortlist.py

不需要連線，直接用證交所 / 櫃買中心 OpenAPI 格式的假資料驗證排行與篩選規則。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_source.market_scanner import build_shortlist


def twse(code, name, close, change, value):
    return {"Code": code, "Name": name, "ClosingPrice": close, "Change": change, "TradeValue": value}


def tpex(code, name, close, change, value):
    return {"SecuritiesCompanyCode": code, "CompanyName": name, "Close": close,
            "Change": change, "TransactionAmount": value}


TWSE = [
    twse("2330", "台積電", "2475.00", "-25.0000", "80000000000"),
    twse("1303", "南亞", "261.00", "14.0000", "25473000000"),
    twse("0050", "元大台灣50", "190.00", "1.0000", "90000000000"),     # ETF
    twse("00400A", "主動國泰", "15.51", "-0.1500", "431715180"),       # ETF（6碼）
    twse("2888", "新光金", "5.00", "0.0000", "30000000000"),           # 低於價格下限
    twse("9999", "停牌股", "--", "--", ""),                           # 無成交
    twse("2303", "聯電", "50.00", "0.5000", "900000000"),             # 成交額太小，排在後面
]
TPEX = [
    tpex("6488", "環球晶", "1035.00", "+90.00", "17666364000"),
    tpex("12345", "權證", "1.00", "+0.10", "99999999999"),             # 非股票代碼
]

PRICE = {"min": 10, "max": 5000}

rows = build_shortlist(TWSE, TPEX, size=10, exclude_etf=True, price_range=PRICE)
syms = [r["symbol"] for r in rows]

# 1. 排除 ETF、權證、價格區間外、沒成交的；上市上櫃一起排
assert syms == ["2330", "1303", "6488", "2303"], syms

# 2. 成交額換算成億、漲跌幅用「昨收 = 收盤 − 漲跌」回推
r6488 = next(r for r in rows if r["symbol"] == "6488")
assert r6488["turnover_billion"] == 176.66 and r6488["change_pct"] == 9.52, r6488
assert r6488["name"] == "環球晶" and r6488["price"] == 1035.0

# 3. size 截斷
assert [r["symbol"] for r in build_shortlist(TWSE, TPEX, 2, True, PRICE)] == ["2330", "1303"]

# 4. 不排除 ETF 時 0050 會進來
assert "0050" in [r["symbol"] for r in build_shortlist(TWSE, TPEX, 10, False, PRICE)]

# 5. 價格上限 2000 時，股價 2475 的台積電會被排除
assert "2330" not in [r["symbol"] for r in build_shortlist(TWSE, TPEX, 10, True, {"min": 10, "max": 2000})]

# 6. 其中一邊資料抓不到（空清單）也能照常排
assert [r["symbol"] for r in build_shortlist([], TPEX, 10, True, PRICE)] == ["6488"]

print("=== PASS: 官方排行縮小範圍全部正確 ===")
