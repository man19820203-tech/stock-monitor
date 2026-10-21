"""
共用資料結構。所有模組（data_source / engine / scoring / scenario）都用同一份型別溝通，
避免每個模組自己定義欄位造成串接錯誤。
"""
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Optional


@dataclass
class Tick:
    """單筆報價快照（不是逐筆成交，是「目前狀態」的一個切片）"""
    symbol: str
    name: str
    ts: datetime
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int              # 累計成交量（股）
    turnover: float          # 累計成交額（元）
    bid_prices: list = field(default_factory=list)   # 五檔買價，高到低
    bid_volumes: list = field(default_factory=list)
    ask_prices: list = field(default_factory=list)    # 五檔賣價，低到高
    ask_volumes: list = field(default_factory=list)
    inner_volume: int = 0     # 內盤累計成交量（打在買價成交）
    outer_volume: int = 0     # 外盤累計成交量（打在賣價成交）
    is_disposition: bool = False       # 是否處置股
    is_attention: bool = False          # 是否注意股
    is_short_restricted: bool = False   # 是否禁空 / 平盤下不得放空


@dataclass
class Bar:
    """K棒"""
    symbol: str
    interval_min: int
    ts_open: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class FirstRangeState:
    """第一波高低點狀態（每檔股票、每天一份）"""
    symbol: str
    window_start: Optional[dtime] = None
    window_end: Optional[dtime] = None
    formed: bool = False           # 觀察區間是否已結束並形成第一高低
    first_high: Optional[float] = None
    first_low: Optional[float] = None
    first_high_volume: int = 0
    first_low_volume: int = 0
    first_high_time: Optional[datetime] = None
    first_low_time: Optional[datetime] = None
    broke_high_at: Optional[datetime] = None     # 首次向上突破第一高的時間
    broke_low_at: Optional[datetime] = None       # 首次向下跌破第一低的時間
    broke_high_price: Optional[float] = None
    broke_low_price: Optional[float] = None
    high_after_break_held: Optional[bool] = None  # 突破後是否站穩（未在 fakeout 窗口內跌回）
    low_after_break_held: Optional[bool] = None
    made_new_high_today: bool = False   # 是否創今日新高（超越第一高之後持續創高）
    made_new_low_today: bool = False


@dataclass
class StockDailyState:
    """一檔股票在當下這一刻的完整狀態，是排名/計分/劇本判斷的輸入"""
    symbol: str
    name: str
    tick: Tick
    bars_1m: list         # list[Bar]
    bars_5m: list          # list[Bar]
    ma5: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    first_range: Optional[FirstRangeState] = None
    day_high: float = 0.0
    day_low: float = 0.0
    change_pct: float = 0.0
    turnover_billion: float = 0.0
    volume_ratio_vs_yesterday: float = 0.0    # 今日截至目前量 / 昨日同一時段量
    volume_multiple_recent: float = 0.0        # 最近1分鐘量 / 近5分鐘均量
    group: Optional[str] = None
    relative_strength_pct: Optional[float] = None   # 個股漲跌% - 大盤漲跌%
    dist_to_limit_up_pct: Optional[float] = None
    dist_to_limit_down_pct: Optional[float] = None
    at_limit_up: bool = False       # 現價在漲停價（依升降單位算的實際漲停價）
    at_limit_down: bool = False     # 現價在跌停價
