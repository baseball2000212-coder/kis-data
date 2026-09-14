# -*- coding: utf-8 -*-
"""미장 마감 데이터 수집.

핵심은 **개별종목 분봉(HHDFS76950200)**이다. 한 번에 최대 120건만 오고 최신→과거 순으로
내려오므로, KEYB(다음 조회 시작 시각)를 이전 페이지의 가장 오래된 봉보다 1분 앞으로 옮겨가며
정규장 시작(09:30)까지 거슬러 올라간다. 프리/애프터가 섞여 오므로 마지막에 09:30~16:00만 남긴다.

지수 분봉(FHKST03030200)은 페이징 파라미터가 없어 최근 100여 건이 전부다.
종가·지수 레벨 확인용으로만 쓰고, 차트는 QQQ/SPY로 그린다.
"""
import datetime as dt
import json
import os
import kis

IDX_PATH  = "/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice"
ITEM_PATH = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"

# 지수 분봉은 페이징이 없고 항상 최근 102봉이 온다. 대신 간격을 키우면 커버 범위가 늘어난다.
# 정규장 390분 → 5분봉(code 300) 78봉이면 하루가 통째로 덮인다. (2026.9.10 실측 확인)
INDICES = {"NDX": "NDX", "SPX": "SPX", "COMP": "COMP", "VIX": "VIX"}
IDX_INTERVAL = "300"        # 60=1분 300=5분 900=15분 1800=30분
IDX_DATE_KEY, IDX_TIME_KEY = "stck_bsop_date", "stck_cntg_hour"
STOCKS  = [("NAS", "QQQ"), ("AMS", "SPY"), ("NAS", "NVDA")]

NMIN = "1"              # 1분봉
# SESSION=regular : 정규장 09:30~16:00 ET (기본, 미장 마감 카드용)
# SESSION=premarket: 프리마켓 04:00~09:30 ET — CPI·고용지표처럼 개장 전 발표 이벤트용.
#   발표 시각(08:30 ET) 전후 반응이 여기에 찍힌다. 정규장 봉은 이 시각엔 아직 존재하지 않는다.
SESSION = os.environ.get("SESSION", "regular")
if SESSION == "premarket":
    SESSION_OPEN, SESSION_CLOSE = "070000", "093000"
    MAX_PAGES = 3       # 150분이면 충분
else:
    SESSION_OPEN, SESSION_CLOSE = "093000", "160000"
    MAX_PAGES = 6       # 120건 x 6 = 720분, 정규장 390분을 충분히 덮는다


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


def index_intraday(symbol, interval=IDX_INTERVAL):
    """지수 분봉 1회 호출 → 최근 영업일의 정규장만 오름차순으로."""
    b = kis.fetch(IDX_PATH, "FHKST03030200", {
        "FID_COND_MRKT_DIV_CODE": "N", "FID_INPUT_ISCD": symbol,
        "FID_HOUR_CLS_CODE": interval, "FID_PW_DATA_INCU_YN": "Y",
    })
    bars = b.get("output2") or []
    if isinstance(bars, dict):
        bars = [bars]
    bars = [r for r in bars if r.get(IDX_DATE_KEY) and r.get(IDX_TIME_KEY)]
    bars.sort(key=lambda r: r[IDX_DATE_KEY] + r[IDX_TIME_KEY])
    day = bars[-1][IDX_DATE_KEY] if bars else ""
    reg = [r for r in bars if r[IDX_DATE_KEY] == day
           and SESSION_OPEN <= r[IDX_TIME_KEY] <= SESSION_CLOSE]
    return {"symbol": symbol, "interval_sec": interval, "summary": b.get("output1"),
            "bars": reg, "raw_count": len(bars)}


def main():
    out = {"asof_kst": kis.now_kst().isoformat(timespec="seconds"),
           "session": f"us-{SESSION}", "nmin": NMIN, "idx_interval_sec": IDX_INTERVAL,
           "session_window": f"{SESSION_OPEN}-{SESSION_CLOSE} ET",
           "indices": {}, "stocks": {}, "errors": []}

    for name, sym in (INDICES.items() if SESSION == "regular" else []):
        try:
            d = index_intraday(sym)
            out["indices"][name] = d
            t = [r[IDX_TIME_KEY] for r in d["bars"]]
            rng = f"{t[0]}~{t[-1]}" if t else "-"
            print(f"[idx] {name} <- {sym}  정규장 {len(d['bars'])}봉 ({rng})  "
                  f"수집 {d['raw_count']}건")
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
    kis.save("us" if SESSION == "regular" else "us_premarket", out)

# ── 나스닥100 선물(NQ) 연결 가능 여부 탐침 ───────────────────────────────
# 해외선물옵션 계좌가 있어야 열리는 경우가 많다. 열리면 data/us/_nq_probe.json 에
# 실제 분봉이 찍히고, 안 열리면 사유가 남는다. 실패해도 본 수집엔 영향 없음.
NQ_CHART_PATH = "/uapi/overseas-futureoption/v1/quotations/inquire-time-futurechartprice"
NQ_PRICE_PATH = "/uapi/overseas-futureoption/v1/quotations/inquire-price"

def nq_probe():
    res = {}
    # 월물코드: 3=H 6=M 9=U 12=Z. 9월물/12월물 둘 다 찔러본다.
    for srs in ("NQU26", "NQZ26", "MNQU26", "MNQZ26"):
        try:
            b = kis.fetch(NQ_PRICE_PATH, "HHDFC55010000",
                          {"SRS_CD": srs, "EXCH_CD": "CME"})
            o = b.get("output1") or b.get("output") or {}
            res[f"price/{srs}"] = {k: o.get(k) for k in list(o)[:8]} if o else "빈 응답"
        except Exception as e:
            res[f"price/{srs}"] = f"실패: {str(e)[:140]}"
    for srs in ("NQU26", "NQZ26"):
        try:
            b = kis.fetch(NQ_CHART_PATH, "HHDFC55020400", {
                "SRS_CD": srs, "EXCH_CD": "CME", "START_DATE_TIME": "",
                "CLOSE_DATE_TIME": "", "QRY_TP": "Q", "QRY_CNT": "120",
                "QRY_GAP": "5", "INDEX_KEY": ""})
            rows = b.get("output2") or []
            if isinstance(rows, dict):
                rows = [rows]
            res[f"chart/{srs}"] = {"n": len(rows), "sample": rows[:2]}
        except Exception as e:
            res[f"chart/{srs}"] = f"실패: {str(e)[:140]}"
    os.makedirs("data/us", exist_ok=True)
    with open("data/us/_nq_probe.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    ok = [k for k, v in res.items() if not isinstance(v, str)]
    print(f"[nq ] 탐침 결과 저장 · 열린 엔드포인트 {len(ok)}/{len(res)} {ok}")


if __name__ == "__main__":
    main()
    try:
        nq_probe()
    except Exception as e:
        print(f"[nq ] 탐침 자체 실패: {str(e)[:120]}")
