"""DART 주요사항보고서 전용 API(DS005) 회사별 호출. 크로스체크용.

- corp_code가 필요해 회사 × API마다 1번 호출한다 (3년 6개월을 한 번에 조회 가능 — 시범 호출로 확인).
- 사건마다 최종본 1건만 돌려준다 (원본이 정정되면 정정본 접수번호만 나옴).
- 이미 성공한 (API, 회사)는 건너뛰고, 하루 한도 초과(020)면 즉시 멈춘다.
"""

import io
import json
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

import httpx
from lxml import etree

from filingscope.collect.dart_client import DART_BASE, CallResult, DartClient

# API → 우리 판정의 (event_type, is_mixed)
DS005_APIS: dict[str, tuple[str, bool]] = {
    "piicDecsn": ("rights_issue", False),
    "pifricDecsn": ("rights_issue", True),
    "cvbdIsDecsn": ("cb", False),
}
LIMIT_EXCEEDED = "020"


@dataclass
class Ds005Summary:
    calls: int = 0
    skipped: int = 0
    failed: list[tuple[str, str, CallResult]] = field(default_factory=list)
    rows: int = 0
    stopped_on_limit: bool = False


def run_ds005(
    client: DartClient,
    corp_codes: Iterable[str],
    bgn: date,
    end: date,
    raw_dir: Path,
    done: set[tuple[str, str]],
    save_rows: Callable[[str, list[dict]], None],
    log_call: Callable[..., None],
    run_id: str,
    pause: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
) -> Ds005Summary:
    """done: 이미 성공한 (api, corp_code). save_rows(api, items), log_call(**collect_log 한 줄)."""
    s = Ds005Summary()
    for corp_code in corp_codes:
        for api in DS005_APIS:
            if (api, corp_code) in done:
                s.skipped += 1
                continue
            params = {"corp_code": corp_code, "bgn_de": f"{bgn:%Y%m%d}", "end_de": f"{end:%Y%m%d}"}
            call = client.get_json(f"{api}.json", params)
            s.calls += 1
            raw_path = None
            if call.body is not None:
                raw_path = raw_dir / api / f"{corp_code}.json"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(json.dumps(call.body, ensure_ascii=False), encoding="utf-8")
            log_call(run_id=run_id, endpoint=f"{api}.json", corp_code=corp_code, bgn_de=bgn, end_de=end,
                     page_no=None, ok=call.ok, status=call.status, message=call.message,
                     attempts=call.attempts, raw_path=str(raw_path) if raw_path else None)
            if call.status == LIMIT_EXCEEDED:
                s.failed.append((api, corp_code, call))
                s.stopped_on_limit = True
                return s
            if not call.ok:
                s.failed.append((api, corp_code, call))
                continue
            items = call.body.get("list", []) if call.body else []
            if items:
                save_rows(api, items)
                s.rows += len(items)
            sleep(pause)
    return s


def fetch_corp_codes(api_key: str, http: httpx.Client | None = None) -> dict[str, str]:
    """DART 기업코드 파일(corpCode.xml) → {stock_code: corp_code}. 종목코드가 있는 회사만."""
    http = http or httpx.Client(base_url=DART_BASE, timeout=60)
    resp = http.get("/corpCode.xml", params={"crtfc_key": api_key})
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        raise RuntimeError(f"zip이 아닌 응답: {resp.text[:200]}")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        root = etree.fromstring(zf.read(zf.namelist()[0]))
    mapping = {}
    for el in root.iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        if stock:
            mapping[stock] = el.findtext("corp_code").strip()
    return mapping
