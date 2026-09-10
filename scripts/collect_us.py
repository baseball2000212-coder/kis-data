# -*- coding: utf-8 -*-
"""미장 마감 데이터 수집 — 미국 지수 정규장 분봉 + 개별종목 분봉.

해외지수분봉조회  FHKST03030200  FID_HOUR_CLS_CODE 0=정규장 / 1=시간외
해외주식분봉조회  HHDFS76950200
한 번 호출에 100건 안팎만 오므로 tr_cont 연속조회로 장 전체를 이어받는다.
"""
import kis

IDX_PATH  = "/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice"
ITEM_PATH = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"

# 2026-09-10 첫 실행에서 확인된 심볼: SPX / COMP / VIX 는 성공, 다우는 전부 실패.
INDEX_CANDIDATES = {
    "SPX":    ["SPX"],
    "NASDAQ": ["COMP"],
    "VIX":    ["VIX"],
    "DOW":    ["DJI", "INDU", "DJIA", "DOWJONES", "US30", "DJX", "COMPDJ"],
}
STOCKS = [("NAS", "QQQ"), ("NAS", "NVDA"), ("AMS", "SPY")]
NMIN = "5"
MAX_PAGES = 8   # 지수 1분봉 390개를 덮으려면 100건 x 4~5페이지


def index_series(symbol, hour_cls="0"):
    (head, rows, calls), err = kis.try_fetch_all(IDX_PATH, "FHKST03030200", {
        "FID_COND_MRKT_DIV_CODE": "N",
        "FID_INPUT_ISCD": symbol,
        "FID_HOUR_CLS_CODE": hour_cls,      # 0 = 미국 정규장만
        "FID_PW_DATA_INCU_YN": "Y",
    }, label=f"index {symbol}", max_pages=MAX_PAGES)
    if err:
        return None, err
    return {"summary": head, "bars": rows, "calls": calls}, None


def stock_series(excd, symb):
    (head, rows, calls), err = kis.try_fetch_all(ITEM_PATH, "HHDFS76950200", {
        "AUTH": "", "EXCD": excd, "SYMB": symb, "NMIN": NMIN,
        "PINC": "1", "NEXT": "", "NREC": "120", "FILL": "", "KEYB": "",
    }, label=f"stock {symb}", max_pages=3)
    if err:
        return None, err
    return {"excd": excd, "summary": head, "bars": rows, "calls": calls}, None


def main():
    out = {"asof_kst": kis.now_kst().isoformat(timespec="seconds"),
           "session": "us", "nmin": NMIN, "indices": {}, "stocks": {}, "errors": []}

    for name, cands in INDEX_CANDIDATES.items():
        for sym in cands:
            data, err = index_series(sym)
            if data and data["bars"]:
                out["indices"][name] = {"symbol": sym, **data}
                print(f"[idx] {name} <- {sym}  bars={len(data['bars'])}  "
                      f"calls={data['calls']}  {kis.timespan(data['bars'])}")
                break
            out["errors"].append(f"{name}/{sym}: {err or 'bars 비어있음'}")
        else:
            print(f"[idx] {name} 전부 실패")

    for excd, symb in STOCKS:
        data, err = stock_series(excd, symb)
        if data:
            out["stocks"][symb] = data
            print(f"[stk] {symb} bars={len(data['bars'])}  calls={data['calls']}  "
                  f"{kis.timespan(data['bars'])}")
        else:
            out["errors"].append(err)

    if out["errors"]:
        print(f"-- 실패 {len(out['errors'])}건 (JSON errors 배열 참고)")
    kis.save("us", out)


if __name__ == "__main__":
    main()
