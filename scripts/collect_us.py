# -*- coding: utf-8 -*-
"""미장 마감 데이터 수집.

핵심은 **개별종목 분봉(HHDFS76950200)**이다. 한 번에 최대 120건만 오고 최신→과거 순으로
내려오므로, KEYB(다음 조회 시작 시각)를 이전 페이지의 가장 오래된 봉보다 1분 앞으로 옮겨가며
정규장 시작(09:30)까지 거슬러 올라간다. 프리/애프터가 섞여 오므로 마지막에 09:30~16:00만 남긴다.

지수 분봉(FHKST03030200)은 페이징 파라미터가 없어 최근 100여 건이 전부다.
종가·지수 레벨 확인용으로만 쓰고, 차트는 QQQ/SPY로 그린다.
"""
import datetime as dt
import kis

IDX_PATH  = "/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice"
ITEM_PATH = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"

INDICES = {"SPX": "SPX", "NASDAQ": "COMP", "VIX": "VIX"}   # 다우는 심볼이 없어 제외
STOCKS  = [("NAS", "QQQ"), ("AMS", "SPY"), ("NAS", "NVDA")]

NMIN = "1"              # 1분봉
SESSION_OPEN  = "093000"
SESSION_CLOSE = "160000"
MAX_PAGES = 6           # 120건 x 6 = 720분, 정규장 390분을 충분히 덮는다


def _keyb(row, nmin):
    """이 봉보다 nmin분 앞선 시각을 KEYB 형식(YYYYMMDDHHMMSS)으로."""
    t = dt.datetime.strptime(row["xymd"] + row["xhms"], "%Y%m%d%H%M%S")
    return (t - dt.timedelta(minutes=int(nmin))).strftime("%Y%m%d%H%M%S")


def stock_intraday(excd, symb, nmin=NMIN, max_pages=MAX_PAGES):
    """KEYB로 과거 방향 페이징. 반환: (요약, 오름차순 정렬된 정규장 봉, 호출수, 전체건수)"""
    head, rows, keyb, nxt, calls = None, [], "", "", 0
    for _ in range(max_pages):
        b = kis.fetch(ITEM_PATH, "HHDFS76950200", {
            "AUTH": "", "EXCD": excd, "SYMB": symb, "NMIN": nmin,
            "PINC": "1", "NEXT": nxt, "NREC": "120", "FILL": "", "KEYB": keyb,
        })
        calls += 1
        if head is None:
            head = b.get("output1")
        page = b.get("output2") or []
        if isinstance(page, dict):
            page = [page]
        page = [r for r in page if r.get("xymd") and r.get("xhms")]
        if not page:
            break
        rows.extend(page)
        oldest = page[-1]
        if oldest["xhms"] <= SESSION_OPEN:      # 개장 시각까지 내려왔으면 끝
            break
        keyb, nxt = _keyb(oldest, nmin), "1"

    # 중복 제거 + 오름차순
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda r: r["xymd"] + r["xhms"]):
        k = r["xymd"] + r["xhms"]
        if k not in seen:
            seen.add(k); uniq.append(r)

    # 가장 최근 영업일의 정규장만 남긴다
    day = uniq[-1]["xymd"] if uniq else ""
    reg = [r for r in uniq
           if r["xymd"] == day and SESSION_OPEN <= r["xhms"] <= SESSION_CLOSE]
    return head, reg, calls, len(uniq)


def index_snapshot(symbol):
    b = kis.fetch(IDX_PATH, "FHKST03030200", {
        "FID_COND_MRKT_DIV_CODE": "N", "FID_INPUT_ISCD": symbol,
        "FID_HOUR_CLS_CODE": "0", "FID_PW_DATA_INCU_YN": "Y",
    })
    bars = b.get("output2") or []
    if isinstance(bars, dict):
        bars = [bars]
    return {"symbol": symbol, "summary": b.get("output1"), "bars": bars}


def main():
    out = {"asof_kst": kis.now_kst().isoformat(timespec="seconds"),
           "session": "us", "nmin": NMIN,
           "session_window": f"{SESSION_OPEN}-{SESSION_CLOSE} ET",
           "indices": {}, "stocks": {}, "errors": []}

    for name, sym in INDICES.items():
        try:
            d = index_snapshot(sym)
            out["indices"][name] = d
            print(f"[idx] {name} <- {sym}  bars={len(d['bars'])}  {kis.timespan(d['bars'])}")
        except Exception as e:
            out["errors"].append(f"index {name}/{sym}: {e}")
            print(f"[idx] {name} 실패")

    for excd, symb in STOCKS:
        try:
            head, reg, calls, total = stock_intraday(excd, symb)
            out["stocks"][symb] = {"excd": excd, "summary": head, "bars": reg}
            first = reg[0]["xhms"] if reg else "-"
            last  = reg[-1]["xhms"] if reg else "-"
            print(f"[stk] {symb} 정규장 {len(reg)}봉 ({first}~{last})  "
                  f"수집 {total}건 / calls={calls}")
        except Exception as e:
            out["errors"].append(f"stock {symb}: {e}")
            print(f"[stk] {symb} 실패: {e}")

    if out["errors"]:
        print(f"-- 실패 {len(out['errors'])}건")
    kis.save("us", out)


# ─────────────────────────────────────────────────────────────
# 일회성 탐색: 지수 분봉의 심볼·간격이 어디까지 먹는지 확인한다.
# 결과 확인 후 이 블록은 지운다. (수집 데이터에는 영향 없음)
def probe():
    print("--- 지수 분봉 탐색 ---")
    for sym in ("NDX", "COMP", "SPX", "NDXT", "IXIC"):
        for code in ("0", "60", "300", "900", "1800"):
            try:
                b = kis.fetch(IDX_PATH, "FHKST03030200", {
                    "FID_COND_MRKT_DIV_CODE": "N", "FID_INPUT_ISCD": sym,
                    "FID_HOUR_CLS_CODE": code, "FID_PW_DATA_INCU_YN": "Y",
                })
                bars = b.get("output2") or []
                if isinstance(bars, dict):
                    bars = [bars]
                if bars:
                    print(f"  {sym:6s} code={code:5s} bars={len(bars):4d}  {kis.timespan(bars)}")
                else:
                    print(f"  {sym:6s} code={code:5s} 빈 응답")
            except Exception as e:
                print(f"  {sym:6s} code={code:5s} 실패: {str(e)[:70]}")
    print("--- 탐색 끝 ---")


if __name__ == "__main__":
    main()
    probe()
