from config_loader import get
from data_source.mock_source import MockDataSource
from data_source.spark_source import SparkDataSource
from data_source.fugle_source import FugleDataSource


def build_data_source():
    provider = get("data_source.provider", "mock")
    if provider == "mock":
        return MockDataSource(poll_interval_sec=get("data_source.poll_interval_sec", 1))
    elif provider == "spark":
        spark_cfg = get("data_source.spark", {})
        return SparkDataSource(
            api_key=spark_cfg.get("api_key", ""),
            api_secret=spark_cfg.get("api_secret", ""),
            account=spark_cfg.get("account", ""),
        )
    elif provider == "fugle":
        fugle_cfg = get("data_source.fugle", {})
        return FugleDataSource(api_token=fugle_cfg.get("api_token", ""))
    else:
        raise ValueError(f"未知的 data_source.provider: {provider}")
