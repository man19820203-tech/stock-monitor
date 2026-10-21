"""
資料來源抽象介面。所有 adapter（mock / spark / fugle）都必須實作這個介面，
上層（engine / main.py）永遠只跟這個介面溝通，換券商時不用改任何計算邏輯。
"""
from abc import ABC, abstractmethod
from typing import Callable, List, Optional
from models import Bar, Tick


class DataSourceBase(ABC):
    @abstractmethod
    def connect(self):
        """建立連線 / 登入。"""
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, symbols: List[str], on_tick: Callable[[Tick], None]):
        """訂閱標的，每次有新報價時呼叫 on_tick(tick)。
        真即時來源（SPARK/Fugle）應該用 WebSocket push；
        mock 來源用內部 timer 模擬。"""
        raise NotImplementedError

    def unsubscribe(self, symbols: List[str]):
        """取消訂閱標的（動態候選名單輪動時使用，例如全市場掃描把某檔換掉時）。
        非抽象方法：預設是no-op，因為 spark 的 stub 目前不需要它；
        真正有實作動態訂閱管理的來源（目前是 fugle）會覆寫這個方法。"""
        pass

    def get_quote_tick(self, symbol: str) -> Optional[Tick]:
        """用 REST 立即查一次目前報價（格式同 subscribe 推來的 Tick），讓剛開始追蹤的股票
        不用等 WebSocket 第一筆推送就能先上儀表板。預設 None = 不支援（mock 本來就會馬上推）。"""
        return None

    def get_intraday_candles(self, symbol: str) -> List[Bar]:
        """當日 1 分K（由舊到新），給晚開/重開/中途加入的股票補回第一波高低點用。
        非抽象方法：預設回傳空清單（= 不回補，只靠之後收到的 tick），
        mock 是即時模擬、沒有「今天稍早」的資料；目前只有 fugle 有實作。"""
        return []

    def get_previous_day_candles(self, symbol: str) -> List[Bar]:
        """「今天以前最近一個交易日」的 1 分K，給「今日量比昨日同期」（量能分數）用。
        預設空清單 = 不支援（mock 模式改讀 data/history 的模擬 CSV）。"""
        return []

    @abstractmethod
    def get_index_quote(self) -> Tick:
        """取得大盤（加權指數）目前狀態，格式借用 Tick（open/high/low/price 有效，量能欄位可為0）"""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self):
        raise NotImplementedError
