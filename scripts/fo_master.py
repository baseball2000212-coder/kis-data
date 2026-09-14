# -*- coding: utf-8 -*-
"""지수선물옵션 종목 마스터 (fo_idx_code_mts.mst).

한투가 공개하는 마스터 파일로, **행사가와 ATM 구분이 그대로 들어있다.**
이게 필요한 이유:
  - 옵션 전광판(FHPIF05030100)은 output1/output2 각각 100건 하드 제한이라
    행사가가 많은 날은 ATM 근처가 통째로 안 온다(공식 문서에 명시된 제약).
  - 정규 K200 선물 코드도 전광판 시장구분으로는 미니선물만 나온다.
마스터에서 최근월물 + ATM 근처 종목코드를 직접 뽑아, 개별 시세 API로 조회한다.

레이아웃 (공식 예제 stocks_info/domestic_index_future_code.py 기준, '|' 구분 9필드):
  0 상품종류  1 단축코드  2 표준코드  3 한글종목명  4 ATM구분
  5 행사가    6 월물구분  7 기초자산 단축코드  8 기초자산명

상품종류: 1=지수선물(정규 K200)  5=지수콜옵션  6=지수풋옵션
          B=미니선물  D=미니콜옵션  E=미니풋옵션
ATM구분:  1=ATM  2=ITM  3=OTM
월물구분: 0=연결선물  1=최근월물  2=차근월물  3=차차근월물  4=차차차근월물
"""
import io, os, zipfile, urllib.request, ssl

URL = "https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip"
COLS = ["종류", "단축코드", "표준코드", "종목명", "ATM구분",
        "행사가", "월물구분", "기초코드", "기초명"]

IDX_FUT, IDX_CALL, IDX_PUT = "1", "5", "6"
MINI_FUT, MINI_CALL, MINI_PUT = "B", "D", "E"
NEAR = "1"                                    # 최근월물

_cache = None


def _f(v, default=None):
    try:
        return float(str(v).strip())
    except Exception:
        return default


def load(force=False):
    """마스터를 받아 dict 리스트로. 실패하면 예외를 그대로 올린다(수집기가 잡아 폴백)."""
    global _cache
    if _cache is not None and not force:
        return _cache
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # 한투 배포 서버 인증서가 종종 체인이 끊겨 있다
    with urllib.request.urlopen(URL, timeout=60, context=ctx) as r:
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".mst"))
        raw = z.read(name)
    text = raw.decode("cp949", errors="replace")
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        p = [c.strip() for c in line.split("|")]
        if len(p) < len(COLS):
            p += [""] * (len(COLS) - len(p))
        rows.append(dict(zip(COLS, p[:len(COLS)])))
    if not rows:
        raise RuntimeError("마스터 파싱 결과가 비었다")
    _cache = rows
    print(f"[mst] 종목 마스터 {len(rows):,}행 로드")
    return rows


def summary(rows=None):
    """상품종류·월물구분 분포 — 레이아웃이 바뀌었는지 한눈에 보는 용도."""
    rows = rows or load()
    kinds, months = {}, {}
    for r in rows:
        kinds[r["종류"]] = kinds.get(r["종류"], 0) + 1
        months[r["월물구분"]] = months.get(r["월물구분"], 0) + 1
    return {"총행": len(rows), "상품종류별": kinds, "월물구분별": months,
            "샘플": rows[:3]}


def front_future(rows=None, prefer_regular=True):
    """최근월물 선물 1종목. 정규 K200(종류 1) 우선, 없으면 미니(B).
    반환: (단축코드, 종목명, 종류) 또는 None"""
    rows = rows or load()
    for kind in ([IDX_FUT, MINI_FUT] if prefer_regular else [MINI_FUT, IDX_FUT]):
        cand = [r for r in rows if r["종류"] == kind and r["월물구분"] == NEAR]
        if cand:
            r = cand[0]
            print(f"[mst] 선물 최근월물 {r['단축코드']} {r['종목명']} (종류 {kind})")
            return r["단축코드"], r["종목명"], kind
    return None


def atm_options(rows=None, span=20, mini=False):
    """최근월물 콜·풋을 행사가 순으로 정렬해 ATM 기준 ±span 개만 반환.

    ATM 기준은 ① 마스터의 ATM구분='1' 을 우선 사용하고
    ② 없으면 콜·풋 행사가 목록의 중앙값을 쓴다.
    반환: {"center": 행사가, "calls": [...], "puts": [...]} — 각 원소는 마스터 행
    """
    rows = rows or load()
    ck, pk = (MINI_CALL, MINI_PUT) if mini else (IDX_CALL, IDX_PUT)
    calls = sorted([r for r in rows if r["종류"] == ck and r["월물구분"] == NEAR],
                   key=lambda r: _f(r["행사가"], 0))
    puts = sorted([r for r in rows if r["종류"] == pk and r["월물구분"] == NEAR],
                  key=lambda r: _f(r["행사가"], 0))
    if not calls or not puts:
        raise RuntimeError(f"최근월물 옵션이 비었다 (콜 {len(calls)} 풋 {len(puts)})")

    atm = next((_f(r["행사가"]) for r in calls if r["ATM구분"] == "1"), None)
    if atm is None:
        atm = _f(calls[len(calls) // 2]["행사가"])

    def around(lst):
        i = min(range(len(lst)), key=lambda k: abs(_f(lst[k]["행사가"], 0) - atm))
        return lst[max(0, i - span): i + span + 1]

    c, p = around(calls), around(puts)
    print(f"[mst] 최근월물 ATM {atm} · 콜 {len(calls)}개 중 {len(c)}개 / "
          f"풋 {len(puts)}개 중 {len(p)}개 선택 "
          f"(행사가 {_f(c[0]['행사가'])}~{_f(c[-1]['행사가'])})")
    return {"center": atm, "calls": c, "puts": p}
