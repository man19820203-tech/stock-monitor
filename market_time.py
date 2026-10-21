"""
統一的「台北時間」來源。

重要：第一波高低點區間、開盤時間等判斷，全部必須用台灣時間（Asia/Taipei），
不能用 datetime.now()，因為部署的伺服器時區可能是 UTC（尤其雲端主機常見），
若不統一，09:00~09:10 的觀察區間會整個對不上。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")


def now_tw() -> datetime:
    return datetime.now(TW_TZ)
