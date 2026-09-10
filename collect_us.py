# -*- coding: utf-8 -*-
"""미장 마감 데이터 수집 — 미국 지수 정규장 분봉 + 개별종목(QQQ 등) 분봉.

해외지수분봉조회  FHKST03030200  /uapi/overseas-price/v1/quotations/inquire-time-indexchartprice
  FID_HOUR_CLS_CODE : 0=정규장  1=시간외   <-- 본장만 잘라내는 핵심 파라미터
해외주식분봉조회  HHDFS76950200  /uapi/overseas-price/v1/quotations/inquire-time-itemchartprice
"""
import kis

IDX_PATH  = "/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice"
ITEM_PATH = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"

# 문서에 확인된 심볼은 SPX. 나스닥/다우 심볼은 계정마다 표기가 달라서
# 후보를 순서대로 시도하고 성공한 것만 저장한다(첫 실행 로그에 무엇이 됐는지 남는다).
INDEX_CANDIDATES = {
    "SPX":    ["SPX"],
    "NASDAQ": ["COMP", "IXIC", "NDX", "CCMP"],
    "DOW":    ["DJI", "INDU", "DJIA"],
    "VIX":    ["VIX", "SPVIX"],
}
STOCKS = [("NAS", "QQQ"), ("NAS", "NVDA"), ("AMS", "SPY")]
NMIN = "5"  # 분봉 단위(분)


def index_series(symbol, hour_cls="0"):
    b, err = kis.try_fetch(IDX_PATH, "FHKST03030200", {
        "FID_COND_MRKT_DIV_CODE": "N",
        "FID_INPUT_ISCD": symbol,
        "FID_HOUR_CLS_CODE": hour_cls,      # 0 = 미국 정규장만
        "FID_PW_DATA_INCU_YN": "Y",
    }, label=f"index {symbol}")
    if err:
        return None, err
    return {"summary": b.get("output1"), "bars": b.get("output2")}, None


def stock_series(excd, symb):
    b, err = kis.try_fetch(ITEM_PATH, "HHDFS76950200", {
        "AUTH": "", "EXCD": excd, "SYMB": symb, "NMIN": NMIN,
        "PINC": "1", "NEXT": "", "NREC": "120", "FILL": "", "KEYB": "",
    }, label=f"stock {symb}")
    if err:
        return None, err
    return {"summary": b.get("output1"), "bars": b.get("output2")}, None


def main():
    out = {"asof_kst": kis.now_kst().isoformat(timespec="seconds"),
           "session": "us", "nmin": NMIN, "indices": {}, "stocks": {}, "errors": []}

    for name, cands in INDEX_CANDIDATES.items():
        for sym in cands:
            data, err = index_series(sym)
            if data and data.get("bars"):
                out["indices"][name] = {"symbol": sym, **data}
                print(f"[idx] {name} <- {sym}  bars={len(data['bars'])}")
                break
            out["errors"].append(f"{name}/{sym} 실패: {err or 'bars 비어있음'}")
        else:
            print(f"[idx] {name} 전부 실패")

    for excd, symb in STOCKS:
        data, err = stock_series(excd, symb)
        if data:
            out["stocks"][symb] = {"excd": excd, **data}
            print(f"[stk] {symb} bars={len(data.get('bars') or [])}")
        else:
            out["errors"].append(err)

    kis.save("us", out)


if __name__ == "__main__":
    main()
