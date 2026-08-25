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

포트폴리오 합계(totals.periods/periods_fx)는 구간 시작 시점에 실제로 들고
있던 수량(data/trades.json 기반 qty_as_of)으로 now·base 양쪽을 통일해서
가중한다 — 오늘 수량을 과거 시점에도 그대로 곱하면, 구간 중간에 추가매수한
몫까지 그 구간 내내 들고 있었던 것처럼 계산돼 수익률이 왜곡되기 때문이다.
종목별 periods/periods_fx는 가격 비율만 쓰므로 수량과 무관해 영향 없다.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
HOLDINGS_PATH = ROOT / "data" / "holdings.json"
TRADES_PATH = ROOT / "data" / "trades.json"
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


def fetch_period_base(ticker: str) -> dict:
    """일간·주간·월간·연간·YTD 기준 '과거 종가'와 그 실제 날짜를 함께 반환한다.
    날짜가 있어야 qty_as_of()로 그 시점의 실제 보유수량을 역산할 수 있다."""
    hist = yf.Ticker(ticker).history(period="1y", interval="1d")
    closes = hist["Close"].dropna()
    if closes.empty:
        raise RuntimeError(f"{ticker}: 과거 시세를 가져오지 못했다")

    now = closes.index[-1]

    def base_at_or_before(target):
        past = closes[closes.index <= target]
        if past.empty:
            return closes.index[0], float(closes.iloc[0])
        return past.index[-1], float(past.iloc[-1])

    ytd_start = pd.Timestamp(year=now.year, month=1, day=1, tz=now.tz)

    targets = {
        "d1": now - pd.Timedelta(days=1),
        "w1": now - pd.Timedelta(days=7),
        "m1": now - pd.Timedelta(days=30),
        "y1": now - pd.Timedelta(days=365),
        "ytd": ytd_start,
    }
    result = {}
    for k, target in targets.items():
        date, price = base_at_or_before(target)
        result[k] = {"date": date.date().isoformat(), "price": price}
    return result


def load_trades() -> list:
    if not TRADES_PATH.exists():
        return []
    trades = json.loads(TRADES_PATH.read_text(encoding="utf-8"))["trades"]
    for t in trades:
        t["qty_delta"] = t["qty"] if t["side"] == "buy" else -t["qty"]
    return trades


def qty_as_of(ticker: str, as_of_date: str, current_qty: float, trades: list) -> float:
    """as_of_date 시점에 실제로 들고 있던 수량을 역산한다 — 그 날짜'보다 뒤'에
    일어난 이 종목 거래들의 수량 변화분을 현재 수량에서 빼는 방식.
    trades.json이 비어 있으면(매매 기록이 아직 없으면) current_qty를 그대로
    돌려주므로, 매매를 기록하기 전까지는 항상 지금과 동일하게 동작한다."""
    delta_since = sum(
        t["qty_delta"] for t in trades
        if t["ticker"] == ticker and t["date"] > as_of_date
    )
    return current_qty - delta_since


def generic_period_dates(today) -> dict:
    """현금성 자산은 가격이 항상 1이라 fetch_period_base로 조회할 시세가 없다.
    qty_as_of에 넘길 날짜만 있으면 되므로, 달력 기준으로 직접 계산한다."""
    return {
        "d1": (today - timedelta(days=1)).isoformat(),
        "w1": (today - timedelta(days=7)).isoformat(),
        "m1": (today - timedelta(days=30)).isoformat(),
        "y1": (today - timedelta(days=365)).isoformat(),
        "ytd": today.replace(month=1, day=1).isoformat(),
    }


def main() -> None:
    holdings = json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))
    today = datetime.now(KST).date()
    cash_period_dates = generic_period_dates(today)

    fx = {
        "USD": fetch_last_price("KRW=X"),      # USD/KRW
        "JPY": fetch_last_price("JPYKRW=X"),   # JPY/KRW
    }
    # 기간 수익률의 환차익 포함 버전에 쓸 과거 환율 — KRW=X·JPYKRW=X 자체가
    # 야후 파이낸스 티커라 주가와 똑같은 함수로 과거값을 구할 수 있다.
    # 환율에는 수량 개념이 없어 날짜는 버리고 가격만 쓴다.
    fx_base = {
        "USD": {k: v["price"] for k, v in fetch_period_base("KRW=X").items()},
        "JPY": {k: v["price"] for k, v in fetch_period_base("JPYKRW=X").items()},
    }
    trades = load_trades()

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

        # 현금은 가격이 항상 1이라 base도 전부 1 — periods_price는 자동으로
        # 0%가 되고, periods_fx만 환율 변동을 그대로 반영한다. 날짜만 달력으로
        # 직접 계산해서 qty_as_of에 넘긴다.
        if is_cash:
            base = {k: {"date": cash_period_dates[k], "price": 1.0} for k in PERIOD_KEYS}
        else:
            base = fetch_period_base(h["ticker"])
        cur_fx_base = fx_base[h["currency"]]

        periods_price = {k: round((price / base[k]["price"] - 1) * 100, 2) for k in PERIOD_KEYS}
        periods_fx = {
            k: round((price * cur_fx / (base[k]["price"] * cur_fx_base[k]) - 1) * 100, 2)
            for k in PERIOD_KEYS
        }
        # 합계는 구간 시작 시점에 실제로 들고 있던 수량으로 now·base 양쪽을
        # 통일해서 가중한다 — 오늘 수량을 과거에도 그대로 곱하면 구간 중간에
        # 추가매수한 몫까지 그 구간 내내 보유했던 것처럼 왜곡되기 때문이다.
        # (trades.json이 비어 있으면 qty_as_of가 항상 오늘 수량을 돌려줘서
        # 지금까지와 동일하게 동작한다.)
        for k in PERIOD_KEYS:
            qty_k = qty_as_of(h["ticker"], base[k]["date"], h["qty"], trades)
            period_now_value[k] += price * qty_k * cur_fx
            period_base_value_price[k] += base[k]["price"] * qty_k * cur_fx
            period_base_value_fx[k] += base[k]["price"] * qty_k * cur_fx_base[k]

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
    benchmark_base = {k: v["price"] for k, v in fetch_period_base(BENCHMARK_TICKER).items()}
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
        # base_value가 0이면(그 시점엔 아직 아무것도 안 갖고 있었으면 — 포트폴리오
        # 전체를 그 구간 안에 새로 시작한 경우) 나눗셈이 불가능하니 null로 둔다.
        "periods": {
            k: round((period_now_value[k] / period_base_value_price[k] - 1) * 100, 2)
            if period_base_value_price[k] else None
            for k in PERIOD_KEYS
        },
        "periods_fx": {
            k: round((period_now_value[k] / period_base_value_fx[k] - 1) * 100, 2)
            if period_base_value_fx[k] else None
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
