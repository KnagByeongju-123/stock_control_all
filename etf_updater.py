"""
한투(KIS) 일봉 → Supabase 저장 (250일 롤링)
  - 60종목 × 최근 250영업일 OHLCV 수집
  - etf_daily   : 일봉 히스토리 upsert + 250일 초과분 삭제
  - etf_position: 최신 요약(이평/추세/RSI/고점대비) 갱신 (스크리너 화면용)

환경변수(GitHub Secrets):
  SUPABASE_URL, SUPABASE_SERVICE_KEY
  KIS_APPKEY, KIS_APPSECRET, KIS_REAL(기본 true)
설치: pip install requests
"""
import os, time, math
from datetime import datetime, timedelta

import requests

# ── 추적 종목 { 코드 : 이름 } · 60종목 ───────────────────────
TICKERS = {
    "069500":"KODEX 200","102110":"TIGER 200","091210":"TIGER KRX100","229200":"KODEX 코스닥150",
    "232080":"TIGER 코스닥150","228820":"TIGER KTOP30","229720":"KODEX KTOP30","237350":"KODEX 코스피100",
    "091160":"KODEX 반도체","091230":"TIGER 반도체","396500":"TIGER Fn반도체TOP10","455850":"SOL AI반도체소부장",
    "381180":"TIGER 미국필라델피아반도체나스닥","497570":"TIGER 미국필라델피아AI반도체",
    "157490":"TIGER 소프트웨어","098560":"TIGER 방송통신","228810":"TIGER 미디어컨텐츠","139260":"TIGER 200 IT",
    "381170":"TIGER 미국테크TOP10","360750":"TIGER 미국S&P500","133690":"TIGER 미국나스닥100",
    "379810":"KODEX 미국나스닥100TR","458730":"TIGER 미국배당다우존스","489250":"KODEX 미국배당다우존스",
    "453640":"KODEX 미국S&P500헬스케어","195930":"TIGER 유로스탁스50(합성H)","241180":"TIGER 일본니케이225",
    "101280":"KODEX 일본TOPIX100","453870":"TIGER 인도니프티50","453810":"KODEX 인도Nifty50",
    "099140":"KODEX 차이나H","150460":"TIGER 중국소비테마","143860":"TIGER 헬스케어","203780":"TIGER 미국나스닥바이오",
    "462900":"KoAct 바이오헬스케어액티브","091180":"KODEX 자동차","305720":"KODEX 2차전지산업",
    "305540":"TIGER 2차전지테마","461950":"KODEX 2차전지핵심소재10","462010":"TIGER 2차전지소재Fn",
    "455860":"SOL 2차전지소부장Fn","091170":"KODEX 은행","102970":"KODEX 증권","139270":"TIGER 200 금융",
    "140700":"KODEX 보험","117680":"KODEX 철강","117460":"KODEX 에너지화학","117700":"KODEX 건설",
    "102960":"KODEX 기계장비","140710":"KODEX 운송","494670":"TIGER 조선TOP10","463250":"TIGER K방산&우주",
    "496770":"PLUS 글로벌방산","152180":"TIGER 생활필수품","228800":"TIGER 여행레저","228790":"TIGER 화장품",
    "102780":"KODEX 삼성그룹","211900":"KODEX 배당성장","329200":"TIGER 리츠부동산인프라",
    "498270":"KIWOOM 미국양자컴퓨팅",
}
KEEP_DAYS = 250  # 종목당 보관 일수
# ────────────────────────────────────────────────────────────

REAL   = (os.environ.get("KIS_REAL", "true") == "true")
DOMAIN = "https://openapi.koreainvestment.com:9443" if REAL else "https://openapivts.koreainvestment.com:29443"
APPKEY    = os.environ["KIS_APPKEY"]
APPSECRET = os.environ["KIS_APPSECRET"]
SB_URL    = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY    = os.environ["SUPABASE_SERVICE_KEY"]

SB_HEADERS = {
    "apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
    "Content-Type": "application/json",
}


def get_token() -> str:
    r = requests.post(f"{DOMAIN}/oauth2/tokenP", json={
        "grant_type": "client_credentials", "appkey": APPKEY, "appsecret": APPSECRET},
        timeout=15)
    j = r.json()
    if not j.get("access_token"):
        raise RuntimeError(f"토큰발급실패: {j}")
    return j["access_token"]


def fetch_daily(token: str, code: str):
    """한투 국내주식 기간별시세(일봉) → [{date,open,high,low,close,volume}, ...] 최신 250일"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=420)).strftime("%Y%m%d")  # 영업일 250 확보용 여유
    headers = {
        "authorization": f"Bearer {token}", "appkey": APPKEY, "appsecret": APPSECRET,
        "tr_id": "FHKST03010100", "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0",
    }
    r = requests.get(f"{DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                     headers=headers, params=params, timeout=15)
    j = r.json()
    if str(j.get("rt_cd")) != "0":
        raise RuntimeError(f"{code} 시세조회 실패 [{j.get('rt_cd')}] {j.get('msg1','')}")
    out = []
    for row in (j.get("output2") or []):
        d = row.get("stck_bsop_date")
        c = row.get("stck_clpr")
        if not d or not c:
            continue
        out.append({
            "ticker": code, "date": f"{d[0:4]}-{d[4:6]}-{d[6:8]}",
            "open": float(row.get("stck_oprc") or 0),
            "high": float(row.get("stck_hgpr") or 0),
            "low":  float(row.get("stck_lwpr") or 0),
            "close": float(c),
            "volume": int(float(row.get("acml_vol") or 0)),
        })
    out.sort(key=lambda x: x["date"])      # 과거→최신
    return out[-KEEP_DAYS:]                 # 최신 250일만


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        gains += max(diff, 0); losses += max(-diff, 0)
    avg_g, avg_l = gains / period, losses / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def ma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def pct(a, b):
    return round((a / b - 1) * 100, 1) if b else None


def build_position(code, name, rows):
    closes = [r["close"] for r in rows]
    highs  = [r["high"] for r in rows]
    price  = closes[-1]
    ma20, ma60, ma120 = ma(closes, 20), ma(closes, 60), ma(closes, 120)
    if ma20 and ma60 and ma120:
        trend = "정배열↑" if ma20 > ma60 > ma120 else ("역배열↓" if ma20 < ma60 < ma120 else "혼조")
    else:
        trend = "혼조"
    hi52 = max(highs[-250:]) if highs else None
    lo52 = min(closes[-250:]) if closes else None
    return {
        "ticker": code, "name": name, "price": round(price),
        "hi52_pct": pct(price, hi52), "lo52_pct": pct(price, lo52),
        "ma20_pct": pct(price, ma20) if ma20 else None,
        "ma60_pct": pct(price, ma60) if ma60 else None,
        "ma120_pct": pct(price, ma120) if ma120 else None,
        "trend": trend, "rsi14": rsi(closes),
        "updated_at": datetime.now().astimezone().isoformat(),
    }


def sb_upsert(table, rows, conflict):
    if not rows:
        return
    r = requests.post(f"{SB_URL}/rest/v1/{table}?on_conflict={conflict}",
                      headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates"},
                      json=rows, timeout=60)
    if r.status_code >= 300:
        print(f"  ! {table} upsert {r.status_code}: {r.text[:120]}")


def sb_prune(code, keep_from_date):
    """250일보다 오래된 행 삭제"""
    requests.delete(
        f"{SB_URL}/rest/v1/etf_daily?ticker=eq.{code}&date=lt.{keep_from_date}",
        headers=SB_HEADERS, timeout=30)


def main():
    token = get_token()
    positions, ok, fail = [], 0, 0

    for code, name in TICKERS.items():
        try:
            rows = fetch_daily(token, code)
            if not rows:
                print(f"  skip {code} {name}: 데이터 없음"); fail += 1; continue
            sb_upsert("etf_daily", rows, "ticker,date")
            sb_prune(code, rows[0]["date"])            # 최신250 시작일보다 과거 삭제
            positions.append(build_position(code, name, rows))
            ok += 1
            print(f"  ok   {code} {name} ({len(rows)}일)")
        except Exception as e:
            print(f"  err  {code} {name}: {e}"); fail += 1
        time.sleep(0.3)   # 한투 호출 제한 여유

    sb_upsert("etf_position", positions, "ticker")
    print(f"\n완료: 성공 {ok} / 실패 {fail} · etf_position {len(positions)}종목 갱신")


if __name__ == "__main__":
    main()
