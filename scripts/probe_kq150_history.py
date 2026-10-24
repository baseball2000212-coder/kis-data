# -*- coding: utf-8 -*-
"""KIS 선물 분봉이 1년보다 과거로 받아지는지 시험 — 분봉 간격을 바꿔가며.

1분봉은 2025-10-15 이전이 빈 응답이었다(보관기간 약 1년). 간격을 키우면 더 과거가 나오는지 본다.
날짜 × 후보코드 × 간격(1·5·10·30분)마다 15:20 기준으로 1회만 호출해 그 날짜 봉이 몇 개 오는지 기록.

결과  data/kq150/_history_probe.json
"""
import json, time, datetime as dt
import kis

CHART_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
DATES = ["20251014", "20250910", "20250612", "20250312", "20241210", "20240611", "20231211"]
INTERVALS = ["60", "300", "600", "1800"]
YEAR_LETTER = {2023: "T", 2024: "V", 2025: "W"}
MONTH_LETTER = {3: "3", 6: "6", 9: "9", 12: "C"}


def second_thursday(y, m):
    return [d for d in range(1, 22) if dt.date(y, m, d).weekday() == 3][1]


def front_quarter(day):
    for y in (day.year, day.year + 1):
        for m in (3, 6, 9, 12):
            if day <= dt.date(y, m, second_thursday(y, m)):
                return y, m


def candidates(y, m):
    L = YEAR_LETTER.get(y, "")
    return [f"106{L}{m:02d}", f"106{L}{MONTH_LETTER[m]}000", f"A06{y % 10}{m:02d}"]


def main():
    out = {"asof_kst": kis.now_kst().isoformat(timespec="seconds"), "results": []}
    for ymd in DATES:
        day = dt.datetime.strptime(ymd, "%Y%m%d").date()
        y, m = front_quarter(day)
        for code in candidates(y, m):
            for iv in INTERVALS:
                rec = {"date": ymd, "code": code, "interval_sec": iv}
                try:
                    b = kis.fetch(CHART_PATH, "FHKIF03020200", {
                        "FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code,
                        "FID_HOUR_CLS_CODE": iv, "FID_PW_DATA_INCU_YN": "Y",
                        "FID_FAKE_TICK_INCU_YN": "N",
                        "FID_INPUT_DATE_1": ymd, "FID_INPUT_HOUR_1": "152000",
                    })
                    page = b.get("output2") or []
                    if isinstance(page, dict):
                        page = [page]
                    dates = sorted({r.get("stck_bsop_date") for r in page if r.get("stck_bsop_date")})
                    same = [r for r in page if r.get("stck_bsop_date") == ymd]
                    rec.update(rows=len(page), rows_on_date=len(same), dates_returned=dates[:5],
                               sample=[(r.get("stck_cntg_hour"), r.get("futs_prpr")) for r in same[:3]])
                except Exception as e:
                    rec["err"] = str(e)[:120]
                out["results"].append(rec)
                flag = rec.get("rows_on_date", "ERR")
                print(f"[probe] {ymd} {code:9s} {int(iv)//60:>2}분봉 → 해당일 {flag}봉 "
                      f"(응답 날짜 {rec.get('dates_returned', rec.get('err'))})")
                time.sleep(0.15)
    hits = [r for r in out["results"] if r.get("rows_on_date")]
    out["summary"] = sorted({(r["date"], r["code"], r["interval_sec"]) for r in hits})
    json.dump(out, open("data/kq150/_history_probe.json", "w"), ensure_ascii=False, indent=1)
    print(f"[probe] 성공 조합 {len(hits)}개: {out['summary']}")


if __name__ == "__main__":
    main()
