"""各投信的抓取器（fetcher）。

每個 provider 模組要提供：

    fetch(etf_cfg: dict, data_date: datetime.date, client: HttpClient) -> dict

data_date 是「持股資料日」（不是投信的公告日）。回傳值是 fetchers.common 定義的「單日快照」dict；
該日資料不存在（非營業日、尚未公告）時丟 NoDataError。
新增投信時：在 etfs.json 加設定、在這裡的 PROVIDERS 註冊模組名即可。
"""

import importlib

PROVIDERS = {
    "uni": "fetchers.uni",        # 統一投信
    "fubon": "fetchers.fubon",    # 富邦投信
    "nomura": "fetchers.nomura",  # 野村投信
    "cathay": "fetchers.cathay",  # 國泰投信
    "jpm": "fetchers.jpm",        # 摩根投信
}


def get_fetcher(provider: str):
    """依 provider 名稱載入模組並回傳其 fetch 函式；未實作時回傳 None。"""
    modname = PROVIDERS.get(provider)
    if modname is None:
        return None
    try:
        mod = importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name == modname:
            return None
        raise
    return getattr(mod, "fetch")
