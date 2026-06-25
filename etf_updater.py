"""
ETF 위치 계산 → Supabase upsert  (yfinance 배치, 60종목)
  국내 ETF는 .KS 붙여 조회. 야후에 없는 종목은 자동 skip.
환경변수: SUPABASE_URL, SUPABASE_SERVICE_KEY
설치:     pip install yfinance pandas requests
"""
import os
from datetime import datetime
import pandas as pd
import requests
import yfinance as yf

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


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    return (100 - 100 / (1 + gain / loss)).iloc[-1]


def pct(a, b):
    return round((a / b - 1) * 100, 1) if b else None


def build_row(code, name, df):
    close = df["Close"].dropna()
    high = df["High"].dropna()
    price = close.iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ma120 = close.rolling(120).mean().iloc[-1]
    if ma20 > ma60 > ma120:
        trend = "정배열↑"
    elif ma20 < ma60 < ma120:
        trend = "역배열↓"
    else:
        trend = "혼조"
    return {
        "ticker": code, "name": name, "price": round(float(price)),
        "hi52_pct": pct(price, high.tail(250).max()),
        "lo52_pct": pct(price, close.tail(250).min()),
        "ma20_pct": pct(price, ma20), "ma60_pct": pct(price, ma60), "ma120_pct": pct(price, ma120),
        "trend": trend, "rsi14": round(float(rsi(close)), 1),
        "updated_at": datetime.now().astimezone().isoformat(),
    }


def main():
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    syms = [f"{c}.KS" for c in TICKERS]
    raw = yf.download(syms, period="2y", interval="1d",
                      auto_adjust=False, group_by="ticker", progress=False, threads=True)
    have = set(raw.columns.get_level_values(0)) if hasattr(raw.columns, "levels") else set()

    rows = []
    for code, name in TICKERS.items():
        sym = f"{code}.KS"
        try:
            if sym not in have:
                print(f"  skip {code} {name}: 야후에 없음"); continue
            df = raw[sym].dropna(how="all")
            if df.empty or df["Close"].dropna().empty:
                print(f"  skip {code} {name}: 데이터 없음"); continue
            rows.append(build_row(code, name, df))
            print(f"  ok   {code} {name}")
        except Exception as e:
            print(f"  err  {code} {name}: {e}")

    if not rows:
        print("업데이트할 데이터 없음"); return

    resp = requests.post(
        f"{url}/rest/v1/etf_position?on_conflict=ticker",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"},
        json=rows, timeout=60)
    print(f"\n{len(rows)}종목 저장 → Supabase:", resp.status_code, resp.text[:150])


if __name__ == "__main__":
    main()
