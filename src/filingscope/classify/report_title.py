"""공시 제목(report_nm)으로 유상증자·CB 여부를 판정한다.

정답표: tests/fixtures/title_cases.csv (LLM이 만들고, 애매한 것은 사용자가 결정).
판정 결과가 바뀌는 수정을 하면 CLASSIFIER_VERSION을 올린다.

- 제목 앞 [ ] 태그(기재정정, 첨부정정, 발행조건확정 등)가 있으면 정정본
- 띄어쓰기는 무시하고 비교
- 유무상증자결정은 유상증자로 잡되 is_mixed로 표시
- 전환사채'권' 유무는 상관없음
"""

import re
from dataclasses import dataclass
from typing import Literal

CLASSIFIER_VERSION = "title-v1"

EventType = Literal["rights_issue", "cb"]

_LEADING_TAGS = re.compile(r"^\s*((?:\[[^\]]*\]\s*)+)")
_TAG = re.compile(r"\[([^\]]*)\]")
_SPACES = re.compile(r"\s+")
_CB = re.compile(r"전환사채권?발행결정")


@dataclass(frozen=True)
class TitleInfo:
    event_type: EventType | None  # None이면 유상증자·CB가 아님
    is_correction: bool
    report_tag: str | None  # 태그가 여러 개면 ","로 잇는다
    is_mixed: bool


def classify_title(report_nm: str) -> TitleInfo:
    tags: list[str] = []
    body = report_nm
    m = _LEADING_TAGS.match(report_nm)
    if m:
        tags = [t.strip() for t in _TAG.findall(m.group(1))]
        body = report_nm[m.end():]

    compact = _SPACES.sub("", body)
    event_type: EventType | None = None
    is_mixed = False
    if "유무상증자결정" in compact:
        event_type, is_mixed = "rights_issue", True
    elif "유상증자결정" in compact:
        event_type = "rights_issue"
    elif _CB.search(compact):
        event_type = "cb"

    return TitleInfo(
        event_type=event_type,
        is_correction=bool(tags),
        report_tag=",".join(tags) if tags else None,
        is_mixed=is_mixed,
    )
