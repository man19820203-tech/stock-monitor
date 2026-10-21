"""
元大證券 SPARK API adapter（骨架 / 待補真實串接）。

申請流程（提醒）：
  1. 到元大證券官網申請 SPARK API 使用資格（需開戶 + 簽署API使用同意書）
  2. 取得 api_key / api_secret，填入 config/settings.yaml -> data_source.spark
  3. 安裝元大提供的 SDK（通常是 pysolace 或官方 python wheel，依當時官方文件為準）
  4. 把下面標記 TODO 的地方換成真實的 SDK 呼叫

這個檔案刻意保留「介面已經定義好、內部尚未串接」的狀態，
這樣其他模組（engine/scoring/scenario/web）完全不用等 API 核准下來就能開發測試，
核准後只要把 TODO 補完，整套系統立刻可以吃真即時資料。
"""
from datetime import datetime
from typing import Callable, List

from data_source.base import DataSourceBase
from models import Tick


class SparkDataSource(DataSourceBase):
    def __init__(self, api_key: str, api_secret: str, account: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.account = account
        self._client = None

    def connect(self):
        if not self.api_key:
            raise RuntimeError(
                "尚未設定 SPARK API 金鑰。請到 config/settings.yaml 的 "
                "data_source.spark 區塊填入 api_key / api_secret，"
                "或先把 data_source.provider 改回 mock 測試。"
            )
        # TODO: 換成 SPARK 官方 SDK 的登入/連線程式碼，例如：
        # from spark_sdk import SparkClient
        # self._client = SparkClient(api_key=self.api_key, api_secret=self.api_secret)
        # self._client.login(self.account)
        raise NotImplementedError(
            "SPARK API 尚未串接。請依元大官方 SDK 文件實作 connect()。"
        )

    def subscribe(self, symbols: List[str], on_tick: Callable[[Tick], None]):
        # TODO: 呼叫 SPARK 的即時報價訂閱（通常是 WebSocket / Solace push），
        # 在收到每筆推播時組成 Tick 物件並呼叫 on_tick(tick)。
        #
        # 範例邏輯（需依實際 SDK 回傳格式調整欄位對應）：
        # def _on_spark_message(msg):
        #     tick = Tick(
        #         symbol=msg["symbol"],
        #         name=msg["name"],
        #         ts=datetime.now(),
        #         price=msg["last_price"],
        #         open=msg["open"],
        #         high=msg["high"],
        #         low=msg["low"],
        #         prev_close=msg["prev_close"],
        #         volume=msg["total_volume"],
        #         turnover=msg["total_turnover"],
        #         bid_prices=msg["bid_prices"],
        #         bid_volumes=msg["bid_volumes"],
        #         ask_prices=msg["ask_prices"],
        #         ask_volumes=msg["ask_volumes"],
        #         inner_volume=msg["inner_volume"],
        #         outer_volume=msg["outer_volume"],
        #         is_disposition=msg.get("disposition", False),
        #         is_attention=msg.get("attention", False),
        #         is_short_restricted=msg.get("short_restricted", False),
        #     )
        #     on_tick(tick)
        # self._client.subscribe(symbols, callback=_on_spark_message)
        raise NotImplementedError("SPARK 訂閱尚未串接。")

    def get_index_quote(self) -> Tick:
        # TODO: 訂閱 TSE 加權指數（通常代號類似 "TSE01" 或官方文件指定代號）
        raise NotImplementedError("SPARK 大盤報價尚未串接。")

    def disconnect(self):
        if self._client:
            # TODO: self._client.logout()
            pass
