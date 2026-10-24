"""b001_filings의 제목 종류 중 정답표에 없는 것만 LLM으로 판정해 정답표에 추가한다.

    uv run python scripts/label_titles_llm.py
"""

import anthropic

from filingscope.classify.answer_sheet import ANSWER_SHEET
from filingscope.config import PROJECT_ROOT, get_settings
from filingscope.db import connect
from filingscope.db.filings import distinct_titles
from filingscope.llm.title_labeler import MODEL, PROMPT_VERSION, build_answer_sheet


def main() -> None:
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
    with connect() as conn:
        titles = distinct_titles(conn)

    print(f"제목 종류 {len(titles)}개, 모델 {MODEL}, 프롬프트 {PROMPT_VERSION}")
    run = build_answer_sheet(client, titles, ANSWER_SHEET)
    print(f"새로 판정 {len(run.labeled)}개, 실패 {len(run.failed)}개 → {ANSWER_SHEET.relative_to(PROJECT_ROOT)}")
    print(f"토큰 입력 {run.input_tokens:,} / 출력 {run.output_tokens:,} → 약 ${run.cost_usd:.2f}")
    for t, err in run.failed.items():
        print(f"  실패: {t} — {err}")
    unsure = {t: l for t, l in run.labeled.items() if l.event_type == "unsure"}
    if unsure:
        print(f"\nunsure {len(unsure)}개 (사용자 결정 필요):")
        for t, l in sorted(unsure.items()):
            print(f"  {t} — {l.reason}")


if __name__ == "__main__":
    main()
