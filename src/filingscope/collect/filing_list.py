"""B001(주요사항보고서) 공시 목록 수집 → 원본 JSON 저장 → b001_filings에 전부 저장 (거르지 않음).

corp_code 없이 조회하면 기간이 3개월로 제한되므로 3개월 구간으로 나눠 모든 페이지를 순회한다.
"""

import calendar
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator

from filingscope.collect.dart_client import CallResult, DartClient
from filingscope.config import PROJECT_ROOT
from filingscope.models.filing import B001Filing

PAGE_COUNT = 100


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def date_windows(start: date, end: date, months: int = 3) -> list[tuple[date, date]]:
    """[start, end]를 겹치거나 빠지는 날 없이 months개월 이하 구간으로 나눈다."""
    windows = []
    bgn = start
    while bgn <= end:
        last = min(add_months(bgn, months) - timedelta(days=1), end)
        windows.append((bgn, last))
        bgn = last + timedelta(days=1)
    return windows


@dataclass
class PageResult:
    bgn: date
    end: date
    page_no: int
    call: CallResult
    raw_path: Path | None
    items: list[dict]


def fetch_window(
    client: DartClient, bgn: date, end: date, raw_dir: Path
) -> Iterator[PageResult]:
    """한 구간의 모든 페이지. 1페이지가 실패하면 전체 페이지 수를 몰라 그 구간은 멈춘다."""
    window_dir = raw_dir / f"{bgn:%Y%m%d}_{end:%Y%m%d}"
    page_no, total_page = 1, 1
    while page_no <= total_page:
        params = {
            "bgn_de": f"{bgn:%Y%m%d}",
            "end_de": f"{end:%Y%m%d}",
            "pblntf_ty": "B",
            "pblntf_detail_ty": "B001",
            "page_no": page_no,
            "page_count": PAGE_COUNT,
        }
        call = client.get_json("list.json", params)
        raw_path = None
        if call.body is not None:
            window_dir.mkdir(parents=True, exist_ok=True)
            raw_path = window_dir / f"page_{page_no:03d}.json"
            raw_path.write_text(
                json.dumps(
                    {"request": params, "fetched_at": datetime.now().isoformat(timespec="seconds"),
                     "response": call.body},
                    ensure_ascii=False, indent=1,
                ),
                encoding="utf-8",
            )
        items = call.body.get("list", []) if call.ok and call.body else []
        yield PageResult(bgn, end, page_no, call, raw_path, items)

        if call.ok and call.body and call.status == "000":
            total_page = int(call.body.get("total_page", 1))
        elif page_no == 1:
            break  # 013(데이터 없음) 또는 실패
        page_no += 1


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path)


@dataclass
class CollectSummary:
    run_id: str
    pages_ok: int = 0
    pages_failed: list[PageResult] = field(default_factory=list)
    fetched: int = 0
    by_cls: Counter = field(default_factory=Counter)
    inserted: int = 0
    duplicates: int = 0


def collect(
    client: DartClient,
    start: date,
    end: date,
    raw_dir: Path,
    save_filings: Callable[[list[B001Filing]], int],
    log_call: Callable[..., None],
    run_id: str,
) -> CollectSummary:
    """save_filings: 새로 저장된 줄 수를 돌려준다. log_call: collect_log 한 줄 기록."""
    summary = CollectSummary(run_id=run_id)
    for bgn, last in date_windows(start, end):
        for page in fetch_window(client, bgn, last, raw_dir):
            log_call(
                run_id=run_id, endpoint="list.json", bgn_de=page.bgn, end_de=page.end,
                page_no=page.page_no, ok=page.call.ok, status=page.call.status,
                message=page.call.message, attempts=page.call.attempts,
                raw_path=_display_path(page.raw_path),
            )
            if not page.call.ok:
                summary.pages_failed.append(page)
                continue
            summary.pages_ok += 1

            filings = [B001Filing.from_list_item(item) for item in page.items]
            summary.fetched += len(filings)
            summary.by_cls.update(f.corp_cls for f in filings)
            n = save_filings(filings)
            summary.inserted += n
            summary.duplicates += len(filings) - n
    return summary
