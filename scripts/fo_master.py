# -*- coding: utf-8 -*-
"""지수선물옵션 종목 마스터 (fo_idx_code_mts.mst).

한투가 공개하는 마스터 파일로, **행사가와 ATM 구분이 그대로 들어있다.**
이게 필요한 이유:
  - 옵션 전광판(FHPIF05030100)은 output1/output2 각각 100건 하드 제한이라
    행사가가 많은 날은 ATM 근처가 통째로 안 온다(공식 문서에 명시된 제약).
  - 정규 K200 선물 코드도 전광판 시장구분으로는 미니선물만 나온다.
마스터에서 최근월물 + ATM 근처 종목코드를 직접 뽑아, 개별 시세 API로 조회한다.

레이아웃 ('|' 구분 9필드, cp949):
  0 상품종류  1 단축코드  2 표준코드  3 한글종목명  4 ATM구분
  5 행사가    6 월물구분  7 기초자산 단축코드  8 기초자산명

※ 2026.9.14 마스터 실측으로 확정된 코드표:
   상품종류 1=K200선물 B=미니선물 7=변동성선물 3=코스닥150선물 H=KRX300선물
            9=섹터선물 2/8/C/4/I/A=스프레드
            **5=K200콜 6=K200풋** (월물옵션, 기초명 KOSPI200)
            D/E=미니콜/풋  J/K=코스닥150콜/풋  L/M=위클리  N/O=위클리M
            P/Q/R/S=코스닥 위클리
   월물구분: **선물만 1~7(1=최근월물). 옵션은 전부 빈 값** — 월물은 종목명의
            YYYYMM 으로 읽어야 한다(9/14 1차 실패 원인).
   ATM구분: 1=ATM 2=ITM 3=OTM 인데 **기준가가 낡았다**. 9/14 실측에서 선물이
            1051인데 마스터 ATM 은 1090 을 가리켰다. ATM 중심은 반드시
            호출부에서 실시간 기초자산 가격(center=)을 넘겨서 잡을 것.
"""
import io, re, ssl, zipfile, datetime, urllib.request

URL = "https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip"
COLS = ["종류", "단축코드", "표준코드", "종목명", "ATM구분",
        "행사가", "월물구분", "기초코드", "기초명"]

IDX_FUT, MINI_FUT = "1", "B"          # front_future() 가 이 순서로 시도
IDX_CALL, IDX_PUT = "5", "6"          # K200 월물 콜/풋 (실측 확정)
MINI_CALL, MINI_PUT = "D", "E"        # 미니 콜/풋
UNDERLYING = "KOSPI200"
NEAR = "1"                            # 월물구분 최근월물 (선물에서 실측 확인)

_cache = None
_YM = re.compile(r"(20\d{4})")


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
    ctx.verify_mode = ssl.CERT_NONE      # 한투 배포 서버 인증서 체인이 종종 끊겨 있다
    with urllib.request.urlopen(URL, timeout=60, context=ctx) as r:
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".mst"))
        raw = z.read(name)
    rows = []
    for line in raw.decode("cp949", errors="replace").splitlines():
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


# ── 판별 ────────────────────────────────────────────────────────────────
def _month(r):
    m = _YM.search(r.get("종목명", "") or "")
    return m.group(1) if m else (r.get("월물구분") or "")


def _cp(r):
    """콜/풋/선물 판별 — 종목명 첫 글자 우선, 없으면 행사가 유무."""
    nm = (r.get("종목명") or "").strip().upper()
    if nm[:1] == "C":
        return "C"
    if nm[:1] == "P":
        return "P"
    if nm[:1] == "F":
        return "F"
    return "C" if _f(r.get("행사가"), 0) else "F"


def _is_opt(r):
    """행사가가 있는 모든 옵션(코스닥150·위클리 포함) — summary 통계용."""
    return _f(r.get("행사가"), 0) > 0 and _cp(r) in ("C", "P")


def _kinds(mini=False):
    return (MINI_CALL, MINI_PUT) if mini else (IDX_CALL, IDX_PUT)


def _is_monthly(r, mini=False):
    """K200(또는 미니) **월물** 옵션만. 코스닥150(J/K)·위클리(L/M/N/O)는 뺀다.
    종류 코드로 1차 거르고, 종목명에 YYYYMM 이 있는지로 위클리를 2차로 뺀다."""
    ck, pk = _kinds(mini)
    if r.get("종류") not in (ck, pk):
        return False
    if UNDERLYING not in (r.get("기초명") or "").upper().replace(" ", ""):
        return False
    return bool(_YM.fullmatch(_month(r) or ""))


def summary(rows=None):
    """상품종류·월물 분포 — 레이아웃/코드값이 바뀌었는지 _diag.json 에서 보는 용도."""
    rows = rows or load()
    kinds = {}
    for r in rows:
        k = r["종류"]
        d = kinds.setdefault(k, {"행": 0, "옵션행": 0, "월물구분값": set(),
                                 "샘플종목명": r.get("종목명"),
                                 "샘플행사가": r.get("행사가"),
                                 "샘플기초명": r.get("기초명")})
        d["행"] += 1
        if _is_opt(r):
            d["옵션행"] += 1
        if len(d["월물구분값"]) < 8:
            d["월물구분값"].add(r.get("월물구분"))
    for d in kinds.values():
        d["월물구분값"] = sorted(d["월물구분값"])
    opts = [r for r in rows if _is_opt(r)]
    months = sorted({_month(r) for r in opts})
    return {"총행": len(rows), "옵션행": len(opts),
            "옵션월물": months[:12], "상품종류별": kinds}


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


def _expiry(ym):
    """YYYYMM 의 옵션 만기일(둘째 목요일)."""
    y, m = int(ym[:4]), int(ym[4:])
    d = datetime.date(y, m, 1)
    first_thu = 1 + (3 - d.weekday()) % 7        # 목요일=3
    return datetime.date(y, m, first_thu + 7)


def front_month(rows=None, today=None, mini=False):
    """옵션 최근월물 YYYYMM. **만기 당일이면 다음 월물** (만기일엔 근월물 미결제가
    소멸해 P/C OI 가 왜곡된다 — 9/11 실측)."""
    rows = rows or load()
    today = today or datetime.date.today()
    months = sorted({_month(r) for r in rows if _is_monthly(r, mini)})
    for ym in months:
        if _expiry(ym) > today:
            return ym
    return months[0] if months else None


def atm_options(rows=None, span=20, mini=False, month=None, today=None, center=None):
    """최근월물 콜·풋을 행사가 순으로 정렬해 ATM 기준 ±span 개만 반환.

    ATM 기준은 ① 마스터의 ATM구분='1' 을 우선 사용하고
    ② 없으면 콜 행사가 목록의 중앙값을 쓴다.
    반환: {"month": YYYYMM, "center": 행사가, "calls": [...], "puts": [...]}
    """
    rows = rows or load()
    ym = month or front_month(rows, today=today, mini=mini)
    if not ym:
        raise RuntimeError("마스터에서 옵션 월물을 못 찾았다")

    ck, pk = _kinds(mini)
    sel = [r for r in rows if _is_monthly(r, mini) and _month(r) == ym]
    calls = sorted([r for r in sel if r["종류"] == ck], key=lambda r: _f(r["행사가"], 0))
    puts = sorted([r for r in sel if r["종류"] == pk], key=lambda r: _f(r["행사가"], 0))
    if not calls or not puts:
        raise RuntimeError(f"{ym} 월물 옵션이 비었다 (콜 {len(calls)} 풋 {len(puts)})")

    # 중심: ① 호출부가 넘긴 실시간 기초자산 가격 ② 마스터 ATM구분 ③ 행사가 중앙값
    atm = _f(center)
    if atm:
        src = "실시간"
    else:
        atm = next((_f(r["행사가"]) for r in calls if r["ATM구분"] == "1"), None)
        src = "마스터ATM"
        if atm is None:
            atm = _f(calls[len(calls) // 2]["행사가"]); src = "중앙값"

    def around(lst):
        i = min(range(len(lst)), key=lambda k: abs(_f(lst[k]["행사가"], 0) - atm))
        return lst[max(0, i - span): i + span + 1]

    c, p = around(calls), around(puts)
    print(f"[mst] {ym} 월물 중심 {atm}({src}) · 콜 {len(calls)}개 중 {len(c)}개 / "
          f"풋 {len(puts)}개 중 {len(p)}개 선택 "
          f"(행사가 {_f(c[0]['행사가'])}~{_f(c[-1]['행사가'])})")
    return {"month": ym, "center": atm, "center_src": src, "calls": c, "puts": p}
