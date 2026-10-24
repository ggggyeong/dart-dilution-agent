"""pykrx 시세. 날짜별 시장 전체 시세를 data/raw/krx/에 저장해 다시 쓴다.

pykrx는 KRX 로그인 정보를 환경 변수로 읽으므로, 이 모듈을 쓰기 전에 get_settings()가 먼저 호출돼야 한다.
"""

import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from filingscope.config import DATA_DIR, get_settings

CACHE = DATA_DIR / "raw" / "krx"
WARMUP_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"


class KrxBlocked(RuntimeError):
    """KRX가 접속을 막음 (차단 안내 페이지 blockError로 이동). 재시도하지 말고 멈춘다."""


def is_blocked() -> bool:
    """pykrx와 같은 방식(requests)으로 한 번 접속해 차단 안내 페이지로 가는지 본다. curl의 302는 판단 근거가 못 된다."""
    r = requests.get(WARMUP_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    return "blockError" in r.url or r.status_code == 403


def _stock():
    get_settings()  # KRX_ID/KRX_PW를 환경 변수로 넘긴다
    from pykrx import stock
    return stock


def trading_calendar(start: date, end: date) -> list[date]:
    """삼성전자(005930) 거래일 = 한국 증시 거래일."""
    df = _stock().get_market_ohlcv(f"{start:%Y%m%d}", f"{end:%Y%m%d}", "005930")
    return [ts.date() for ts in df.index]


class MarketSnapshots:
    """(날짜, 시장) → {종목코드: 거래량}. 파일 캐시."""

    def __init__(self, cache_dir: Path = CACHE, pause: float = 1.0, batch: int = 50, rest: float = 0.0):
        """pause: 새로 받을 때마다 쉬는 초 (1초에 1번). batch개 받을 때마다 rest초 쉰다 (사용자: 쉬지 않아도 됨 → 기본 0).
        KRX는 1분에 100번 넘게 요청하면 그 IP를 차단한다 (사용자가 받은 안내, 2026-10-16).
        실제로 몇 분에 170번 넘게 연속 호출했다가 차단됐다."""
        self.cache_dir = cache_dir
        self.pause = pause
        self.batch = batch
        self.rest = rest
        self._mem: dict[tuple[date, str], dict[str, float]] = {}
        self.fetched = 0

    def volumes(self, d: date, market: str) -> dict[str, float]:
        key = (d, market)
        if key not in self._mem:
            path = self.cache_dir / f"{d:%Y%m%d}_{market}.csv"
            if path.exists():
                df = pd.read_csv(path, dtype={"code": str})
            else:
                raw = self._fetch(d, market)
                df = pd.DataFrame({"code": raw.index.astype(str), "volume": raw["거래량"].values})
                path.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(path, index=False)
                self.fetched += 1
                time.sleep(self.pause)
                if self.rest and self.fetched % self.batch == 0:
                    print(f"  ... 시세 {self.fetched}개 받음 — {self.rest:.0f}초 쉼")
                    time.sleep(self.rest)
            self._mem[key] = dict(zip(df["code"], df["volume"]))
        return self._mem[key]

    def _fetch(self, d: date, market: str, retries: int = 4) -> pd.DataFrame:
        """KRX가 가끔 JSON이 아닌 응답을 줘서 pykrx가 실패하거나 빈 표를 돌려준다 → 간격을 늘려 재시도.
        거래일에 빈 표는 비정상이므로 저장하지 않는다."""
        last = None
        for attempt in range(retries + 1):
            try:
                raw = _stock().get_market_ohlcv(f"{d:%Y%m%d}", market=market)
                if "거래량" in raw.columns and len(raw):
                    return raw
                last = RuntimeError("빈 결과")
            except Exception as e:  # pykrx 내부 오류(JSON 파싱 실패 등)
                last = e
            if is_blocked():
                raise KrxBlocked(f"KRX 차단 중 ({d} {market}에서 멈춤) — 받은 시세는 저장돼 있음")
            time.sleep(2 * 2 ** attempt)
        raise RuntimeError(f"KRX 시세 받기 실패 {d} {market}: {last}")

    def volume_on(self, d: date, market: str, code: str) -> float | None:
        return self.volumes(d, market).get(code)
