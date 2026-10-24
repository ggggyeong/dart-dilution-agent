"""A 거르기: 공시 당시 코스피·코스닥에서 거래 중이었는가.

공시일 직전 1거래일의 시장 전체 시세(pykrx, 날짜별)를 본다. 지금 상장폐지된 회사도 당시 기록이 남아 있다.
(직전 1거래일: "공시가 나오기 직전에 거래 중이었나"를 보되 공시 뒤의 반응이 섞이지 않게. 사용자 결정 2026-10-16.
 처음엔 직전 5거래일이었으나, 공시 직전 며칠 정지된 종목도 통과시켜 목적에 덜 맞아 바꿈)
- 코스피·코스닥 목록에 있고 거래량 > 0 → 통과
- 코스피·코스닥 목록에는 있으나 거래량 0 → 거래정지
- 코넥스 목록에만 있음 → 코넥스
- 어느 목록에도 없음 → 공시 당시 미상장 (상장 전이거나 이미 상장폐지)
- 종목코드 없음 → 비상장
"""

from dataclasses import dataclass
from datetime import date
from typing import Callable

LISTED_MARKETS = ("KOSPI", "KOSDAQ")
WINDOW_DAYS = 1


@dataclass(frozen=True)
class ListingCheck:
    passed: bool
    reason: str
    market: str | None
    listed_days: int
    traded_days: int
    window: tuple[date, date] | None


def prior_trading_days(calendar: list[date], filed: date, n: int = WINDOW_DAYS) -> list[date]:
    """공시일 직전(공시일 제외) n거래일."""
    return [d for d in calendar if d < filed][-n:]


def check_listing(
    stock_code: str | None,
    days: list[date],
    volume_on: Callable[[date, str, str], float | None],
) -> ListingCheck:
    """volume_on(day, market, stock_code) → 그날 그 시장 목록에 있으면 거래량, 없으면 None."""
    if not stock_code:
        return ListingCheck(False, "비상장(종목코드 없음)", None, 0, 0, None)
    window = (days[0], days[-1]) if days else None
    listed = traded = 0
    market = None
    for d in days:
        for m in LISTED_MARKETS:
            v = volume_on(d, m, stock_code)
            if v is not None:
                listed += 1
                market = m
                traded += v > 0
                break
    if traded:
        return ListingCheck(True, "통과", market, listed, traded, window)
    if listed:
        return ListingCheck(False, "거래정지", market, listed, 0, window)
    if any(volume_on(d, "KONEX", stock_code) is not None for d in days):
        return ListingCheck(False, "코넥스", "KONEX", 0, 0, window)
    return ListingCheck(False, "공시 당시 미상장", None, 0, 0, window)
