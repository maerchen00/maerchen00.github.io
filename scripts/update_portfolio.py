"""
data/holdings.json(수동 관리 매입 정보)을 읽어 야후 파이낸스 시세·환율로
data/portfolio.json(웹페이지가 fetch하는 자동 생성 파일)을 갱신한다.

시세 조회가 하나라도 실패하면 예외를 던지고 종료한다 — 부분적으로만 갱신된
값으로 portfolio.json을 덮어써서 사이트에 깨진 숫자가 노출되는 것을 막기 위함.
GitHub Actions 워크플로에서는 이 스크립트가 실패하면 커밋을 건너뛰므로,
사이트에는 마지막으로 성공한 날짜의 데이터가 그대로 남는다.

기간 수익률(일간·주간·월간·연간·YTD)은 가격 기준(periods)과 환차익 포함 기준
(periods_fx) 둘 다 계산한다 — 과거 환율도 fetch_period_base_prices로 똑같이
구할 수 있어서(KRW=X·JPYKRW=X 자체가 야후 파이낸스의 티커다) 총수익률처럼
두 버전을 다 낼 수 있었다.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
HOLDINGS_PATH = ROOT / "data" / "holdings.json"
OUTPUT_PATH = ROOT / "data" / "portfolio.json"
HISTORY_PATH = ROOT / "data" / "portfolio_history.json"

KST = timezone(timedelta(hours=9))

PERIOD_KEYS = ["d1", "w1", "m1", "y1", "ytd"]
BENCHMARK_TICKER = "^GSPC"


def fetch_last_price(ticker: str) -> float:
    price = yf.Ticker(ticker).fast_info.get("lastPrice")
    if price is None:
        raise RuntimeError(f"{ticker}: 시세를 가져오지 못했다")
    return float(price)


def fetch_period_base_prices(ticker: str, current_price: float) -> dict:
    """일간·주간·월간·연간·YTD 기준 '과거 종가'를 반환한다 (현재가는 별도로 넘겨받음)."""
    hist = yf.Ticker(ticker).history(period="1y", interval="1d")
    closes = hist["Close"].dropna()
    if closes.empty:
        raise RuntimeError(f"{ticker}: 과거 시세를 가져오지 못했다")

    now = closes.index[-1]

    def base_at_or_before(target):
        past = closes[closes.index <= target]
        return float(past.iloc[-1]) if not past.empty else float(closes.iloc[0])

    ytd_start = pd.Timestamp(year=now.year, month=1, day=1, tz=now.tz)

    return {
        "d1": base_at_or_before(now - pd.Timedelta(days=1)),
        "w1": base_at_or_before(now - pd.Timedelta(days=7)),
        "m1": base_at_or_before(now - pd.Timedelta(days=30)),
        "y1": base_at_or_before(now - pd.Timedelta(days=365)),
        "ytd": base_at_or_before(ytd_start),
    }


def main() -> None:
    holdings = json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))

    fx = {
        "USD": fetch_last_price("KRW=X"),      # USD/KRW
        "JPY": fetch_last_price("JPYKRW=X"),   # JPY/KRW
    }
    # 기간 수익률의 환차익 포함 버전에 쓸 과거 환율 — KRW=X·JPYKRW=X 자체가
    # 야후 파이낸스 티커라 주가와 똑같은 함수로 과거값을 구할 수 있다.
    fx_base = {
        "USD": fetch_period_base_prices("KRW=X", fx["USD"]),
        "JPY": fetch_period_base_prices("JPYKRW=X", fx["JPY"]),
    }

    positions = []
    total_value = 0.0
    total_cost_known = 0.0
    total_value_known = 0.0        # 환차익 포함(현재 환율로 환산) 평가금액 합
    total_value_ex_fx_known = 0.0  # 환차익 미포함(매입 시점 환율 고정) 평가금액 합
    # 기간 수익률 집계용. price 버전은 오늘 환율로 통일해서 환산하므로 환율
    # 변동은 상쇄되고 가격 변동분만 남는다. fx 버전은 그 시점의 실제 환율을
    # 써서 환차익까지 포함한다.
    period_now_value = {k: 0.0 for k in PERIOD_KEYS}
    period_base_value_price = {k: 0.0 for k in PERIOD_KEYS}
    period_base_value_fx = {k: 0.0 for k in PERIOD_KEYS}

    for h in holdings["positions"]:
        is_cash = h.get("type") == "cash"
        price = 1.0 if is_cash else fetch_last_price(h["ticker"])
        cur_fx = fx[h["currency"]]
        value_krw = price * h["qty"] * cur_fx
        total_value += value_krw

        # 가격 수익률(환차익 미포함)은 매입환율 없이도 계산 가능 — 통화 자체의
        # 가격 변동만 보기 때문. 원화 환산 총수익률(환차익 포함)만 매입환율이 필요하다.
        return_price_pct = (price / h["buy_price"] - 1) * 100

        cost_krw = None
        return_pct = None
        profit_krw = None
        profit_price_krw = None
        buy_fx = h.get("buy_fx")
        if buy_fx is not None:
            cost_krw = h["buy_price"] * h["qty"] * buy_fx
            value_ex_fx_krw = price * h["qty"] * buy_fx  # 매입 시점 환율로 고정 환산
            return_pct = (value_krw / cost_krw - 1) * 100
            profit_krw = value_krw - cost_krw
            profit_price_krw = value_ex_fx_krw - cost_krw
            total_cost_known += cost_krw
            total_value_known += value_krw
            total_value_ex_fx_known += value_ex_fx_krw

        # 현금은 가격이 항상 1이라 base_prices도 전부 1 — periods_price는
        # 자동으로 0%가 되고, periods_fx만 환율 변동을 그대로 반영한다.
        base_prices = {k: 1.0 for k in PERIOD_KEYS} if is_cash else fetch_period_base_prices(h["ticker"], price)
        cur_fx_base = fx_base[h["currency"]]

        periods_price = {k: round((price / base_prices[k] - 1) * 100, 2) for k in PERIOD_KEYS}
        periods_fx = {
            k: round((price * cur_fx / (base_prices[k] * cur_fx_base[k]) - 1) * 100, 2)
            for k in PERIOD_KEYS
        }
        for k in PERIOD_KEYS:
            period_now_value[k] += price * h["qty"] * cur_fx
            period_base_value_price[k] += base_prices[k] * h["qty"] * cur_fx
            period_base_value_fx[k] += base_prices[k] * h["qty"] * cur_fx_base[k]

        positions.append({
            "ticker": h["ticker"],
            "name": h["name"],
            "label": h.get("label"),
            "type": "cash" if is_cash else "security",
            "currency": h["currency"],
            "qty": h["qty"],
            "buy_price": h["buy_price"],
            "buy_fx": buy_fx,
            "price": round(price, 4),
            "fx": round(cur_fx, 4),
            "cost_krw": round(cost_krw) if cost_krw is not None else None,
            "value_krw": round(value_krw),
            "profit_krw": round(profit_krw) if profit_krw is not None else None,
            "profit_price_krw": round(profit_price_krw) if profit_price_krw is not None else None,
            "return_pct": round(return_pct, 2) if return_pct is not None else None,
            "return_price_pct": round(return_price_pct, 2),
            "periods": periods_price,
            "periods_fx": periods_fx,
        })

    # 총 평가금액이 확정된 뒤에야 비중을 계산할 수 있어 별도 루프로 뺐다.
    for p in positions:
        p["weight_pct"] = round(p["value_krw"] / total_value * 100, 2) if total_value else None

    # 벤치마크(S&P500) — S&P500은 USD 포인트라 그 자체로는 환차익 개념이 없다.
    # periods(가격만)는 원래 지수 그대로, periods_fx는 "그 시점에 원화로 S&P500을
    # 그대로 들고 있었다면"을 가정해 USD/KRW 환율을 곱해 원화 환산까지 반영한
    # 버전이다 — 환차익 포함 토글일 때 포트폴리오의 원화 실수익률과 견줄 수 있게.
    benchmark_price = fetch_last_price(BENCHMARK_TICKER)
    benchmark_base = fetch_period_base_prices(BENCHMARK_TICKER, benchmark_price)
    usd_fx_base = fx_base["USD"]
    benchmark = {
        "ticker": BENCHMARK_TICKER,
        "name": "S&P 500",
        "periods": {k: round((benchmark_price / benchmark_base[k] - 1) * 100, 2) for k in PERIOD_KEYS},
        "periods_fx": {
            k: round(
                (benchmark_price * fx["USD"] / (benchmark_base[k] * usd_fx_base[k]) - 1) * 100, 2
            )
            for k in PERIOD_KEYS
        },
    }

    totals = {
        "value_krw": round(total_value),
        "cost_krw": round(total_cost_known) if total_cost_known else None,
        "profit_krw": round(total_value_known - total_cost_known) if total_cost_known else None,
        "profit_price_krw": round(total_value_ex_fx_known - total_cost_known) if total_cost_known else None,
        "return_pct": (
            round((total_value_known / total_cost_known - 1) * 100, 2)
            if total_cost_known else None
        ),
        "return_price_pct": (
            round((total_value_ex_fx_known / total_cost_known - 1) * 100, 2)
            if total_cost_known else None
        ),
        "periods": {
            k: round((period_now_value[k] / period_base_value_price[k] - 1) * 100, 2)
            for k in PERIOD_KEYS
        },
        "periods_fx": {
            k: round((period_now_value[k] / period_base_value_fx[k] - 1) * 100, 2)
            for k in PERIOD_KEYS
        },
        "benchmark": benchmark,
        "excluded_from_return": [
            p["ticker"] for p in positions if p["cost_krw"] is None
        ],
    }

    as_of = datetime.now(KST)
    result = {
        "as_of": as_of.isoformat(timespec="seconds"),
        "fx": {k: round(v, 4) for k, v in fx.items()},
        "positions": positions,
        "totals": totals,
    }

    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT_PATH} ({len(positions)} positions)")

    update_history(as_of, totals)


def update_history(as_of: datetime, totals: dict) -> None:
    """날짜별 총 평가금액·수익률을 data/portfolio_history.json에 누적한다.
    같은 날짜에 여러 번 실행되면(수동 재실행 등) 그날 항목을 덮어쓴다 —
    하루 한 행만 남긴다."""
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8")) if HISTORY_PATH.exists() else []
    today = as_of.date().isoformat()

    entry = {
        "date": today,
        "value_krw": totals["value_krw"],
        "return_pct": totals["return_pct"],
        "return_price_pct": totals["return_price_pct"],
    }

    history = [h for h in history if h["date"] != today]
    history.append(entry)
    history.sort(key=lambda h: h["date"])

    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {HISTORY_PATH} ({len(history)} days)")


if __name__ == "__main__":
    main()
