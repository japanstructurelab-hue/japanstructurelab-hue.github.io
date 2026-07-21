# nikkei_vi.py
"""日経VI（^NKVI.OS）を米国版 Yahoo Finance から自動取得するヘルパー。

通常は自動取得。取れなかった日だけ manual_overrides.json の予備値に落ちる。
自動も手動もダメなら即エラーで停止する（古い値のまま黙って走らせない）。

使い方（market_report.py 側）:
    from nikkei_vi import get_nikkei_vi
    vi_value, vi_prev, vi_source = get_nikkei_vi()
    # vi_source は "yahoo:auto" / "manual:fallback"
"""

import json
import os

import requests

# ^NKVI.OS の ^ を URL エンコード（既存の ^VIX と同じ扱い）
VI_TICKER = "%5ENKVI.OS"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "manual_overrides.json")

# 明らかに壊れた値を弾くための素朴なレンジ（日経VIの現実的な範囲）
VI_MIN, VI_MAX = 5.0, 150.0


def _fetch_yahoo(ticker):
    """(最新終値, 前日終値) を返す。取れなければ (None, None)。"""
    url = CHART_URL.format(ticker)
    params = {"range": "5d", "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0"}  # UA なしだと弾かれることがある
    r = requests.get(url, params=params, headers=headers, timeout=10)
    r.raise_for_status()
    closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    valid = [c for c in closes if c is not None]  # null（休場・欠損）を除く
    if len(valid) < 2:
        return (None, None)
    latest, prev = round(valid[-1], 2), round(valid[-2], 2)
    if not (VI_MIN <= latest <= VI_MAX):  # 壊れた値なら手動へ回す
        return (None, None)
    return (latest, prev)


def _read_override():
    """manual_overrides.json の予備値。無ければ None。
    ※ キー名は実ファイルに合わせる。ここでは 'nikkei_vi' と '日経VI' を試す。
    """
    if not os.path.exists(OVERRIDES_PATH):
        return None
    with open(OVERRIDES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    vi = data.get("NIKKEI_VI") or data.get("nikkei_vi") or data.get("日経VI")
    if not vi:
        return None
    return (vi.get("value"), vi.get("prev_value"), vi.get("date"))


def get_nikkei_vi():
    """日経VIの (value, prev_value, source, date) を返す。両方ダメなら例外で停止。"""
    try:
        value, prev = _fetch_yahoo(VI_TICKER)
        if value is not None:
            return value, prev, "yahoo:auto", None
        print("[日経VI] Yahoo が有効値を返しませんでした → 手動値へフォールバック")
    except Exception as e:
        print(f"[日経VI] Yahoo 取得に失敗: {e} → 手動値へフォールバック")

    ov = _read_override()
    if ov and ov[0] is not None:
        value, prev, date = ov
        print(f"[日経VI] manual_overrides.json を使用（{date}）: {value}")
        return value, prev, "manual:fallback", date

    raise RuntimeError(
        "日経VI を自動でも手動でも取得できませんでした。"
        "manual_overrides.json に nikkei_vi を入れてから再実行してください。"
    )


if __name__ == "__main__":
    # 単体テスト用: python nikkei_vi.py で今の値を確認できる
    value, prev, source, date = get_nikkei_vi()
    print(value, prev, source, date)
