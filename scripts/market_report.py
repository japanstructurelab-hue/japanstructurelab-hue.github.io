"""
毎朝のマーケットレポート用データ取得スクリプト。

Yahoo Finance の chart API (query1.finance.yahoo.com/v8/finance/chart/{ticker}) から
VIX / WTI原油 / Brent原油 / (手動)日経VI を取得し、異常値チェック付きで表示する。

依存: 標準ライブラリのみ（requests等のインストール不要）。
実行: python scripts/market_report.py
"""

import json
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
import os

from nikkei_vi import get_nikkei_vi

# Windows端末が既定でcp932の場合、日本語や⚠絵文字の出力で落ちるためUTF-8に固定する。
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
USER_AGENT = "Mozilla/5.0"
TIMEOUT = 10
RETRY = 2

# 自動取得する指数
TICKERS = [
    {"key": "VIX", "label": "VIX", "ticker": "^VIX"},
    {"key": "WTI", "label": "WTI原油 (CL=F)", "ticker": "CL=F"},
    {"key": "BRENT", "label": "Brent原油 (BZ=F)", "ticker": "BZ=F"},
]

# 日経VIはYahoo Finance chart APIで安定取得できるティッカーが見つからなかったため
# (^N225VI, ^VXJ, 1552.T 等を確認したが該当なし/古すぎ)、手動入力で補う。
MANUAL_OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "manual_overrides.json")

ABNORMAL_PCT_THRESHOLD = 15.0   # ±15%超で要確認フラグ
STALE_DAYS_THRESHOLD = 4        # 直近データが4日以上前なら警告（週末+バッファ考慮）


def fetch_chart(ticker):
    """指定ティッカーのchart APIレスポンス(JSON dict)を返す。失敗時は例外を投げる。"""
    url = CHART_API.format(ticker=urllib.parse.quote(ticker)) + "?interval=1d&range=10d"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    last_err = None
    for attempt in range(1, RETRY + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("chart", {}).get("error"):
                raise ValueError(str(data["chart"]["error"]))
            result = data["chart"]["result"]
            if not result:
                raise ValueError("empty result")
            return result[0]
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"fetch failed after {RETRY} attempts: {last_err}")


def extract_latest_and_previous(chart_result):
    """終値配列から (最新終値, 前日終値, 最新の日付) を取り出す。null(欠損)はスキップする。"""
    closes = chart_result["indicators"]["quote"][0]["close"]
    timestamps = chart_result["timestamp"]
    gmtoffset = chart_result["meta"].get("gmtoffset", 0)

    pairs = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
    if len(pairs) < 2:
        raise ValueError("not enough valid close data points")

    (latest_ts, latest_close) = pairs[-1]
    (prev_ts, prev_close) = pairs[-2]

    latest_date = datetime.fromtimestamp(latest_ts + gmtoffset, tz=timezone.utc).date()
    return latest_close, prev_close, latest_date


def check_staleness(latest_date):
    today = datetime.now(timezone(timedelta(hours=9))).date()  # JST基準の「今日」
    age_days = (today - latest_date).days
    return age_days


def fetch_one(item):
    """1指数分のデータを取得して結果dictを返す。失敗しても例外を外に投げない。"""
    label = item["label"]
    ticker = item["ticker"]
    try:
        chart = fetch_chart(ticker)
        latest, prev, latest_date = extract_latest_and_previous(chart)
        pct = (latest - prev) / prev * 100 if prev else None
        age_days = check_staleness(latest_date)

        flags = []
        if pct is not None and abs(pct) > ABNORMAL_PCT_THRESHOLD:
            flags.append("⚠要確認(変化率)")
        if age_days > STALE_DAYS_THRESHOLD:
            flags.append(f"⚠データ古い({age_days}日前)")

        return {
            "key": item["key"],
            "label": label,
            "ok": True,
            "latest": latest,
            "prev": prev,
            "pct": pct,
            "date": latest_date,
            "flags": flags,
        }
    except Exception as e:
        return {
            "key": item["key"],
            "label": label,
            "ok": False,
            "error": str(e),
        }


def load_manual_overrides():
    if not os.path.exists(MANUAL_OVERRIDES_PATH):
        return {}
    try:
        with open(MANUAL_OVERRIDES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def build_nikkei_vi_result():
    """日経VIは nikkei_vi.get_nikkei_vi() 経由で取得する(自動取得 or 手動値へのフォールバック)。"""
    label = "日経VI"

    try:
        vi_value, vi_prev, vi_source, vi_date = get_nikkei_vi()
        pct = (vi_value - vi_prev) / vi_prev * 100 if vi_prev else None

        if vi_source == "yahoo:auto":
            flags = ["(自動)"]
            latest_date = datetime.now(timezone(timedelta(hours=9))).date()
        else:
            flags = [f"(手動 {vi_date})"]
            latest_date = datetime.strptime(vi_date, "%Y-%m-%d").date()

        age_days = check_staleness(latest_date)
        if pct is not None and abs(pct) > ABNORMAL_PCT_THRESHOLD:
            flags.append("⚠要確認(変化率)")
        if age_days > STALE_DAYS_THRESHOLD:
            flags.append(f"⚠データ古い({age_days}日前)")

        return {
            "key": "NIKKEI_VI",
            "label": label,
            "ok": True,
            "latest": vi_value,
            "prev": vi_prev,
            "pct": pct,
            "date": latest_date,
            "flags": flags,
        }
    except Exception as e:
        return {
            "key": "NIKKEI_VI",
            "label": label,
            "ok": False,
            "error": str(e),
        }


def fmt_num(x, digits=2):
    return f"{x:,.{digits}f}"


def print_table(results):
    headers = ["指数", "直近終値", "前日終値", "前日比", "日付", "フラグ"]
    rows = []
    for r in results:
        if r["ok"]:
            pct_str = f"{r['pct']:+.2f}%" if r["pct"] is not None else "N/A"
            rows.append([
                r["label"],
                fmt_num(r["latest"]),
                fmt_num(r["prev"]) if r["prev"] is not None else "N/A",
                pct_str,
                str(r["date"]),
                " ".join(r["flags"]) if r["flags"] else "-",
            ])
        else:
            rows.append([r["label"], "取得失敗", "取得失敗", "取得失敗", "-", f"⚠ {r['error']}"])

    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]

    def fmt_row(cells):
        return " | ".join(c.ljust(w) for c, w in zip(cells, widths))

    print(fmt_row(headers))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(fmt_row(row))


def print_spread(results_by_key):
    brent = results_by_key.get("BRENT")
    wti = results_by_key.get("WTI")
    print()
    if brent and brent["ok"] and wti and wti["ok"]:
        spread = brent["latest"] - wti["latest"]
        print(f"Brent-WTIスプレッド: {spread:+.2f} ドル (Brent {fmt_num(brent['latest'])} - WTI {fmt_num(wti['latest'])})")
    else:
        print("Brent-WTIスプレッド: 算出不可（Brent/WTIのいずれかが取得失敗）")


def print_copy_paste(results):
    print()
    print("--- コピペ用 ---")
    for r in results:
        if r["ok"]:
            pct_str = f"{r['pct']:+.1f}%" if r["pct"] is not None else "N/A"
            flag = f" {' '.join(r['flags'])}" if r["flags"] else ""
            print(f"{r['label']}: {fmt_num(r['latest'], 1)} ({pct_str}){flag}")
        else:
            print(f"{r['label']}: 取得失敗（{r['error']}）")


def main():
    results = [fetch_one(item) for item in TICKERS]
    results.append(build_nikkei_vi_result())

    print(f"=== マーケットレポート {datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M JST')} ===\n")
    print_table(results)

    results_by_key = {r["key"]: r for r in results}
    print_spread(results_by_key)

    print_copy_paste(results)


if __name__ == "__main__":
    main()
