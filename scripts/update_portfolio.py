"""
data/holdings.json(수동 관리 매입 정보)을 읽어 야후 파이낸스 시세·환율로
data/portfolio.json(웹페이지가 fetch하는 자동 생성 파일)을 갱신한다.

시세 조회가 하나라도 실패하면 예외를 던지고 종료한다 — 부분적으로만 갱신된
값으로 portfolio.json을 덮어써서 사이트에 깨진 숫자가 노출되는 것을 막기 위함.
GitHub Actions 워크플로에서는 이 스크립트가 실패하면 커밋을 건너뛰므로,
사이트에는 마지막으로 성공한 날짜의 데이터가 그대로 남는다.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
HOLDINGS_PATH = ROOT / "data" / "holdings.json"
OUTPUT_PATH = ROOT / "data" / "portfolio.json"

KST = timezone(timedelta(hours=9))


def fetch_last_price(ticker: str) -> float:
    price = yf.Ticker(ticker).fast_info.get("lastPrice")
    if price is None:
        raise RuntimeError(f"{ticker}: 시세를 가져오지 못했다")
    return float(price)


def main() -> None:
    holdings = json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))

    fx = {
        "USD": fetch_last_price("KRW=X"),      # USD/KRW
        "JPY": fetch_last_price("JPYKRW=X"),   # JPY/KRW
    }

    positions = []
    total_value = 0.0
    total_cost_known = 0.0
    total_value_known = 0.0       # 환차익 포함(현재 환율로 환산) 평가금액 합
    total_value_ex_fx_known = 0.0  # 환차익 미포함(매입 시점 환율 고정) 평가금액 합

    for h in holdings["positions"]:
        price = fetch_last_price(h["ticker"])
        cur_fx = fx[h["currency"]]
        value_krw = price * h["qty"] * cur_fx
        total_value += value_krw

        # 가격 수익률(환차익 미포함)은 매입환율 없이도 계산 가능 — 통화 자체의
        # 가격 변동만 보기 때문. 원화 환산 총수익률(환차익 포함)만 매입환율이 필요하다.
        return_price_pct = (price / h["buy_price"] - 1) * 100

        cost_krw = None
        return_pct = None
        buy_fx = h.get("buy_fx")
        if buy_fx is not None:
            cost_krw = h["buy_price"] * h["qty"] * buy_fx
            value_ex_fx_krw = price * h["qty"] * buy_fx  # 매입 시점 환율로 고정 환산
            return_pct = (value_krw / cost_krw - 1) * 100
            total_cost_known += cost_krw
            total_value_known += value_krw
            total_value_ex_fx_known += value_ex_fx_krw

        positions.append({
            "ticker": h["ticker"],
            "name": h["name"],
            "currency": h["currency"],
            "qty": h["qty"],
            "buy_price": h["buy_price"],
            "buy_fx": buy_fx,
            "price": round(price, 4),
            "fx": round(cur_fx, 4),
            "cost_krw": round(cost_krw) if cost_krw is not None else None,
            "value_krw": round(value_krw),
            "return_pct": round(return_pct, 2) if return_pct is not None else None,
            "return_price_pct": round(return_price_pct, 2),
        })

    totals = {
        "value_krw": round(total_value),
        "cost_krw": round(total_cost_known) if total_cost_known else None,
        "return_pct": (
            round((total_value_known / total_cost_known - 1) * 100, 2)
            if total_cost_known else None
        ),
        "return_price_pct": (
            round((total_value_ex_fx_known / total_cost_known - 1) * 100, 2)
            if total_cost_known else None
        ),
        "excluded_from_return": [
            p["ticker"] for p in positions if p["cost_krw"] is None
        ],
    }

    result = {
        "as_of": datetime.now(KST).isoformat(timespec="seconds"),
        "fx": {k: round(v, 4) for k, v in fx.items()},
        "positions": positions,
        "totals": totals,
    }

    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT_PATH} ({len(positions)} positions)")


if __name__ == "__main__":
    main()
