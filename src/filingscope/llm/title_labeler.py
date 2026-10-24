"""LLM으로 제목 판정 정답표(tests/fixtures/title_cases.csv)를 만든다.

- LLM에는 제목과 기준만 준다. 코드 판정 결과는 보여주지 않는다.
- 제목당 한 번만 부르고 정답표에 저장한다. 이미 있는 제목은 다시 부르지 않고, 기존 줄(사용자가 고친 줄 포함)은 덮어쓰지 않는다.
"""

import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5-5"
PROMPT_VERSION = "title-prompt-v1"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PRICE_PER_MTOK = {"input": 4.00, "output": 20.00}  # claude-opus-5-5

CSV_COLUMNS = [
    "report_nm", "expected_event_type", "expected_is_correction",
    "expected_report_tag", "expected_is_mixed", "reason", "source",
]

SYSTEM_PROMPT = """너는 DART 주요사항보고서(B001) 공시 제목을 분류한다. 제목 하나를 보고 아래 기준으로 판정하라.

event_type
- rights_issue: 유상증자 결정. 유무상증자 결정도 rights_issue이고, 이때 is_mixed=true.
- cb: 전환사채 발행 결정. '전환사채권'의 '권' 유무와 띄어쓰기는 상관없다.
- none: 무상증자, 감자, 자기전환사채 취득, 교환사채, 신주인수권부사채 등 그 밖의 공시.
- unsure: 기준으로 판단하기 애매한 경우. reason에 왜 애매한지 적어라.

is_correction: 제목 앞에 [ ]로 감싼 태그가 있으면 true, 없으면 false.
report_tag: 그 태그의 대괄호 안 글자 원문. 태그가 여러 개면 쉼표(,)로 잇는다. 태그가 없으면 빈 문자열.
is_mixed: 유무상증자 결정이면 true, 아니면 false.
reason: 판정 이유 한 줄 (한국어)."""


class TitleLabel(BaseModel):
    event_type: Literal["rights_issue", "cb", "none", "unsure"]
    is_correction: bool
    report_tag: str
    is_mixed: bool
    reason: str


@dataclass
class LabelRun:
    labeled: dict[str, TitleLabel] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * PRICE_PER_MTOK["input"]
                + self.output_tokens * PRICE_PER_MTOK["output"]) / 1_000_000


def label_title(client: anthropic.Anthropic, report_nm: str) -> tuple[TitleLabel, int, int]:
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=4000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        output_config={"effort": "low"},
        output_format=TitleLabel,
        messages=[{"role": "user", "content": f"제목: {report_nm}"}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"refusal: {resp.stop_details}")
    if resp.parsed_output is None:
        raise RuntimeError(f"파싱 실패 (stop_reason={resp.stop_reason})")
    return resp.parsed_output, resp.usage.input_tokens, resp.usage.output_tokens


def load_answer_sheet(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row["report_nm"]: row for row in csv.DictReader(f)}


def write_answer_sheet(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for nm in sorted(rows):
            w.writerow({c: rows[nm].get(c, "") for c in CSV_COLUMNS})


def label_to_row(report_nm: str, label: TitleLabel) -> dict:
    return {
        "report_nm": report_nm,
        "expected_event_type": label.event_type,
        "expected_is_correction": str(label.is_correction).lower(),
        "expected_report_tag": label.report_tag,
        "expected_is_mixed": str(label.is_mixed).lower(),
        "reason": label.reason,
        "source": f"llm:{MODEL}:{PROMPT_VERSION}",
    }


def build_answer_sheet(
    client: anthropic.Anthropic, titles: list[str], path: Path, workers: int = 8
) -> LabelRun:
    """정답표에 없는 제목만 LLM으로 판정해 추가한다. 중간에 멈춰도 그때까지 결과는 저장된다."""
    rows = load_answer_sheet(path)
    todo = [t for t in titles if t not in rows]
    run = LabelRun()
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(label_title, client, t): t for t in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            t = futures[fut]
            try:
                label, n_in, n_out = fut.result()
            except Exception as e:  # 한 제목 실패가 전체를 멈추지 않게
                run.failed[t] = f"{type(e).__name__}: {e}"
                continue
            with lock:
                run.labeled[t] = label
                run.input_tokens += n_in
                run.output_tokens += n_out
                rows[t] = label_to_row(t, label)
                if i % 20 == 0:
                    write_answer_sheet(path, rows)
    write_answer_sheet(path, rows)
    return run
