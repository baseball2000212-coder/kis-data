# -*- coding: utf-8 -*-
"""KIS Open API 최소 클라이언트 — 토큰 발급 + 시세 조회(연속조회 포함)."""
import os, json, time, datetime as dt
import requests

PROD = "https://openapi.koreainvestment.com:9443"
VTS  = "https://openapivts.koreainvestment.com:29443"

APP_KEY    = os.environ["KIS_APP_KEY"]
APP_SECRET = os.environ["KIS_APP_SECRET"]
BASE       = VTS if os.environ.get("KIS_ENV", "prod") == "vps" else PROD

KST = dt.timezone(dt.timedelta(hours=9))
_token = None

# 토큰 캐시 파일. GitHub Actions 에서는 actions/cache 로 실행 간에 이 폴더를 물려준다.
# 이게 없으면 실행마다 새 토큰을 발급받아 한투에서 알림톡이 하루 4번씩 온다.
TOKEN_FILE = os.environ.get("KIS_TOKEN_FILE", ".tokencache/token.json")


def now_kst():
    return dt.datetime.now(KST)


def _load_cached():
    """유효기간이 30분 이상 남은 캐시 토큰만 재사용."""
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            d = json.load(f)
        left = float(d["expires_at"]) - time.time()
        if left > 1800:
            print(f"[auth] 캐시 토큰 재사용 (남은 유효기간 {left/3600:.1f}시간)")
            return d["access_token"]
        print(f"[auth] 캐시 토큰 만료 임박({left/60:.0f}분) — 재발급")
    except FileNotFoundError:
        print("[auth] 캐시 토큰 없음 — 신규 발급")
    except Exception as e:
        print(f"[auth] 캐시 읽기 실패({e}) — 신규 발급")
    return None


def _save_cached(access_token, expires_in):
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE) or ".", exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"access_token": access_token,
                       "expires_at": time.time() + float(expires_in)}, f)
        os.chmod(TOKEN_FILE, 0o600)
    except Exception as e:
        print(f"[auth] 캐시 저장 실패({e}) — 다음 실행에서 재발급됨")


def _issue():
    """접근토큰 신규 발급. 한투는 1일 1회 발급이 원칙이고 잦으면 제한될 수 있다."""
    r = requests.post(
        f"{BASE}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET},
        headers={"content-type": "application/json"}, timeout=20,
    )
    r.raise_for_status()
    b = r.json()
    tok = b["access_token"]
    _save_cached(tok, b.get("expires_in", 86400))
    print("[auth] 신규 토큰 발급 완료 (알림톡 1건 발송됨)")
    return tok


def token(force_new=False):
    """접근토큰. 캐시 → 신규 순. 실행당 1번만 실제로 발급한다."""
    global _token
    if _token and not force_new:
        return _token
    if not force_new:
        _token = _load_cached()
        if _token:
            return _token
    _token = _issue()
    return _token


def _call(path, tr_id, params, tr_cont=""):
    """단건 호출. (body, 응답헤더) 반환."""
    h = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token()}",
        "appkey": APP_KEY, "appsecret": APP_SECRET,
        "tr_id": tr_id, "tr_cont": tr_cont, "custtype": "P",
    }
    for attempt in range(3):
        r = requests.get(f"{BASE}{path}", headers=h, params=params, timeout=25)
        if r.status_code == 200:
            b = r.json()
            if str(b.get("rt_cd")) != "0":
                raise RuntimeError(f"{tr_id} rt_cd={b.get('rt_cd')} msg={b.get('msg1')}")
            return b, r.headers
        if r.status_code == 401 and attempt == 0:
            print("[auth] 401 — 캐시 토큰이 무효. 강제 재발급 후 1회 재시도")
            h["authorization"] = f"Bearer {token(force_new=True)}"
            continue
        if r.status_code in (429, 500, 502, 503):
            time.sleep(1.5 * (attempt + 1)); continue
        raise RuntimeError(f"{tr_id} HTTP {r.status_code}: {r.text[:300]}")
    raise RuntimeError(f"{tr_id} 재시도 실패")


def fetch(path, tr_id, params, tr_cont=""):
    return _call(path, tr_id, params, tr_cont)[0]


def fetch_all(path, tr_id, params, max_pages=8, pause=0.35):
    """연속조회로 output2를 끝까지 모은다.
    응답헤더 tr_cont 가 'M'/'F' 면 다음 페이지가 있고, 요청헤더에 'N'을 실어 이어받는다.
    반환: (output1, output2 전체 리스트, 실제 호출 횟수)"""
    head, rows, cont = None, [], ""
    calls = 0
    for _ in range(max_pages):
        b, h = _call(path, tr_id, params, cont)
        calls += 1
        if head is None:
            head = b.get("output1")
        out = b.get("output2") or []
        if isinstance(out, dict):
            out = [out]
        if not out:
            break
        rows.extend(out)
        if (h.get("tr_cont") or "").strip() not in ("M", "F"):
            break
        cont = "N"
        time.sleep(pause)
    return head, rows, calls


DATE_KEYS = ("stck_bsop_date", "xymd", "bsop_date", "kymd", "tymd")
TIME_KEYS = ("stck_cntg_hour", "xhms", "khms", "cntg_hour", "hms")


def timespan(rows):
    """봉 리스트의 시각 범위를 사람이 읽을 수 있게. 로그로 커버리지 확인용."""
    def stamp(r):
        d = next((r[k] for k in DATE_KEYS if r.get(k)), "")
        t = next((r[k] for k in TIME_KEYS if r.get(k)), "")
        return f"{d} {t}".strip() or "?"
    if not rows:
        return "없음"
    a, b = stamp(rows[0]), stamp(rows[-1])
    return f"{a} ~ {b}" if a != b else a


def try_fetch_all(path, tr_id, params, label="", **kw):
    """실패해도 파이프라인을 세우지 않는다."""
    try:
        return fetch_all(path, tr_id, params, **kw), None
    except Exception as e:
        return (None, [], 0), f"{label or tr_id}: {e}"


def save(kind, payload, outdir="data"):
    d = os.path.join(outdir, kind)
    os.makedirs(d, exist_ok=True)
    stamp = payload["asof_kst"][:10].replace("-", "")
    for name in (f"{stamp}.json", "latest.json"):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"saved {d}/{stamp}.json ({os.path.getsize(os.path.join(d, 'latest.json')):,} bytes)")
