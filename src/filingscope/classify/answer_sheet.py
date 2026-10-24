"""제목 판정 정답표(tests/fixtures/title_cases.csv) 읽기와 코드 판정 비교."""

import csv
from dataclasses import dataclass
from pathlib import Path

from filingscope.classify.report_title import TitleInfo, classify_title
from filingscope.config import PROJECT_ROOT

ANSWER_SHEET = PROJECT_ROOT / "tests" / "fixtures" / "title_cases.csv"


def _bool(s: str) -> bool:
    return s.strip().lower() in {"true", "1", "y", "yes", "t"}


def _none_if_blank(s: str) -> str | None:
    s = (s or "").strip()
    return None if s.lower() in {"", "none", "null", "-"} else s


@dataclass(frozen=True)
class AnswerRow:
    report_nm: str
    event_type: str | None  # rights_issue / cb / None(none) / "unsure"
    is_correction: bool
    report_tag: str | None
    is_mixed: bool
    reason: str
    source: str

    @property
    def is_unsure(self) -> bool:
        return self.event_type == "unsure"

    def as_tuple(self) -> tuple:
        return (self.event_type, self.is_correction, self.report_tag, self.is_mixed)


def load_answers(path: Path = ANSWER_SHEET) -> list[AnswerRow]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [
            AnswerRow(
                report_nm=r["report_nm"],
                event_type=_none_if_blank(r["expected_event_type"]),
                is_correction=_bool(r["expected_is_correction"]),
                report_tag=_none_if_blank(r["expected_report_tag"]),
                is_mixed=_bool(r["expected_is_mixed"]),
                reason=r.get("reason", ""),
                source=r.get("source", ""),
            )
            for r in csv.DictReader(f)
        ]


def code_tuple(t: TitleInfo) -> tuple:
    return (t.event_type, t.is_correction, t.report_tag, t.is_mixed)


def matches(row: AnswerRow) -> bool:
    return code_tuple(classify_title(row.report_nm)) == row.as_tuple()
