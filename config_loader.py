"""
統一設定讀取器。所有模組都應該透過這裡拿參數，禁止在程式內寫死數字。
"""
import yaml
import os
from pathlib import Path
from threading import Lock

_CONFIG_PATH = Path(__file__).parent / "config" / "settings.yaml"
# 金鑰（富果 API Key、Telegram Bot Token…）不放在 settings.yaml，避免上傳 GitHub 時外流：
#   - 本機：寫在 config/secrets.yaml（已列入 .gitignore，格式見 config/secrets.example.yaml）
#   - GitHub Actions：設在 repo 的 Secrets，由 workflow 以環境變數傳進來
# 兩者都會蓋過 settings.yaml 的同名欄位，環境變數優先。
_SECRETS_PATH = Path(__file__).parent / "config" / "secrets.yaml"
_ENV_OVERRIDES = {
    "FUGLE_API_TOKEN": ("data_source", "fugle", "api_token"),
    "TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
    "TELEGRAM_CHAT_ID": ("telegram", "chat_id"),
}
_cache = None
_lock = Lock()


def load_config(force_reload: bool = False) -> dict:
    """讀取 config/settings.yaml。支援熱重載（force_reload=True 時重新讀檔），
    這樣後台改參數不用重啟整個系統。"""
    global _cache
    with _lock:
        if _cache is None or force_reload:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            if _SECRETS_PATH.exists():
                with open(_SECRETS_PATH, "r", encoding="utf-8") as f:
                    _deep_merge(cfg, yaml.safe_load(f) or {})
            for env_name, keys in _ENV_OVERRIDES.items():
                value = os.environ.get(env_name, "").strip()
                if value:
                    node = cfg
                    for k in keys[:-1]:
                        node = node.setdefault(k, {})
                    node[keys[-1]] = value
            _cache = cfg
        return _cache


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def get(path: str, default=None):
    """用 'a.b.c' 的路徑語法取值，例如 get('first_range.duration_minutes')"""
    cfg = load_config()
    node = cfg
    for key in path.split("."):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return default
    return node
