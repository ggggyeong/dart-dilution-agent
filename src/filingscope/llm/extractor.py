"""실제 판정용 LLM. 정답표용(llm/answer_key.py)과 분리해서 관리한다.

원문 전체가 아니라 과제별로 잘라낸 부분(parse/sections.py)만 준다 (명세 6장).
실제 모델: claude-opus-5-5 (extract-v1 비교에서 결정, data/reports/llm_compare/).
"""

from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field, create_model

PROMPT_VERSION = "extract-v4"  # v4: 매매대금 상계 = 현물 규칙 추가 (돌리지 않음). v3: 납입 방식 입력 = 원문 전체. v2: 잘라 넣음
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MODELS = {  # 모델 → (입력 단가, 출력 단가, 서버 대체 모델 사용 여부)
    "claude-haiku-5-5": (0.10, 0.50, False),   # Haiku는 서버 대체 모델 없음
    "claude-sonnet-5-5": (2.00, 10.00, True),
    "claude-opus-5-5": (4.00, 20.00, True),
}
EFFORT = "medium"

SYSTEM_PROMPT = """너는 한국 DART 유상증자 공시(주요사항보고서)의 원문 전체 또는 잘라낸 부분을 읽고, 요청받은 항목만 판정한다.

공통 규칙
- 주어진 글만 근거로 한다. 추측하거나 계산해서 숫자를 만들지 않는다.
- evidence는 주어진 글에서 근거 부분을 글자 그대로 복사한다 (200자 이내). 바꿔 쓰거나 요약하지 않는다.
- 글 앞의 [대괄호] 이름표는 읽기 쉽게 붙인 것이다. evidence에 넣지 않는다.
- 판단할 수 없으면 값은 null(납입 방식은 "불명").
- reason은 한국어 한 줄.

[납입 방식 payment] 신주 대금을 무엇으로 내는지.
- 현금: 인수인이 현금으로 납입
- 빚→주식: 인수인이 회사에 대해 가진 채권(대여금, 차입금, 매입채무, 회생채권 등)을 출자전환하거나 주금납입채무와 상계. 금전채권을 '현물출자' 형식으로 넣는 경우 포함
- 현물: 주식, 지분, 특허, 부동산 등 금전채권이 아닌 자산을 출자
- 섞임: 같은 증자에서 현금과 현금 외가 함께
- 불명: 판단할 수 없음
상계·대용납입이라도 상계되는 채권이 같은 거래에서 자산(주식·부동산 등)을 사며 생긴 매매대금이면 현물, 그 전부터 있던 빚(대여금·차입금·예전 사채 원금)이면 빚→주식.
주의: "현금으로 납입하거나 상계할 수 있음" 같은 선택지 문구, "해당없음" 같은 부정 문장, 정관 조항, 과거 다른 증자의 내역, "대용치" 같은 다른 뜻의 낱말은 이번 증자의 납입 방식이 아니다.
근거는 납입 방식을 직접 말하는 문장을 고르세요. 그런 문장이 없어도 정황으로 판단할 수 있으면 값을 고르고 정황 판단이라고 표시하세요 (indirect = true). "불명"은 정황으로도 판단할 수 없을 때만 씁니다.

[할인율 discount] 주주배정 증자의 발행가액(예정발행가액)을 정할 때 기준주가에 적용하는 할인율(%). 할인 양수, 할증 음수.
1차·2차 발행가 등에 다른 할인율이 있으면 예정발행가액(1차)에 쓰는 값.

[권리 매도 기간 right_sale] 신주인수권증서 매매(상장 거래) 기간의 시작일 · 종료일 (YYYY-MM-DD)."""


class PaymentOut(BaseModel):
    payment_type: Literal["현금", "빚→주식", "현물", "섞임", "불명"]
    evidence: Optional[str]
    indirect: bool = Field(description="납입 방식을 직접 말하는 문장이 없어 정황으로 판단했으면 true")
    reason: str


class DiscountOut(BaseModel):
    discount_rate: Optional[float]
    evidence: Optional[str]
    reason: str


class RightSaleOut(BaseModel):
    start: Optional[str]
    end: Optional[str]
    evidence: Optional[str]
    reason: str


TASKS = {"payment": PaymentOut, "discount": DiscountOut, "right_sale": RightSaleOut}
NAMES = {"payment": "납입 방식", "discount": "할인율", "right_sale": "권리 매도 기간"}


def build_messages(sections: dict) -> list[dict]:
    parts = [f"판정할 항목: {', '.join(f'{NAMES[t]}({t})' for t in sections)}"]
    same: dict[str, list[str]] = {}  # 할인율 · 권리 매도 기간은 같은 24번 글 → 한 번만 넣는다
    for t, s in sections.items():
        same.setdefault(s.text, []).append(t)
    for text, ts in same.items():
        tag = "_".join(ts)
        parts.append(f"<{tag}_글>\n{text}\n</{tag}_글>")
    return [{"role": "user", "content": "\n\n".join(parts)}]


def output_model(tasks) -> type[BaseModel]:
    return create_model("Extract", **{t: (TASKS[t], ...) for t in tasks})


def request_kwargs(model: str, sections: dict, system: str = SYSTEM_PROMPT) -> dict:
    kw = dict(model=model, max_tokens=8000, system=system, output_config={"effort": EFFORT},
              messages=build_messages(sections))
    if MODELS[model][2]:
        kw.update(betas=[FALLBACK_BETA], fallbacks="default")
    return kw


def extract(client: anthropic.Anthropic, model: str, sections: dict, system: str = SYSTEM_PROMPT):
    resp = client.beta.messages.parse(output_format=output_model(list(sections)), **request_kwargs(model, sections, system))
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"refusal: {resp.stop_details}")
    return resp.parsed_output, resp.usage, resp.stop_reason


def tasks_for(ci_mth: int, stock_type: str | None, right_sale: str | None, payment_source: str | None) -> list[str]:
    """한 사건에 LLM이 할 일 (명세 6장). 납입 방식은 규칙으로 못 정한 것만, 할인율 · 권리 매도 기간은 주주배정 보통주만."""
    tasks = []
    if payment_source == "pending:llm" or (payment_source or "").startswith("llm:"):
        tasks.append("payment")
    if ci_mth in (1, 2, 3) and stock_type == "보통":
        tasks.append("discount")
        if right_sale == "시장":
            tasks.append("right_sale")
    return tasks


RESULT_COLS = ["payment_type", "payment_indirect", "payment_evidence", "payment_evidence_ok", "payment_reason",
               "discount_rate", "discount_evidence", "discount_evidence_ok", "discount_reason",
               "right_sale_start", "right_sale_end", "right_sale_evidence", "right_sale_evidence_ok", "right_sale_reason",
               "input_tokens", "output_tokens", "error"]


def judge(client: anthropic.Anthropic, model: str, path, tasks: list[str]) -> dict:
    """원문 파일 하나를 판정해 결과 한 줄(RESULT_COLS)을 돌려준다. 근거는 모델에 넣은 글에 있는지 확인한다."""
    from filingscope.llm.answer_key import evidence_in_text
    from filingscope.parse.sections import sections_for

    secs = sections_for(path, tasks)
    given = "\n".join(s.text for s in secs.values())
    row = {"error": ""}
    try:
        out, usage, _ = extract(client, model, secs)
    except Exception as e:  # 한 건 실패가 전체를 멈추지 않게
        row["error"] = f"{type(e).__name__}: {e}"[:300]
        return row
    row.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    if "payment" in tasks:
        x = out.payment
        row.update(payment_type=x.payment_type, payment_indirect=x.indirect, payment_evidence=x.evidence,
                   payment_reason=x.reason, payment_evidence_ok=evidence_in_text(x.evidence, given))
    if "discount" in tasks:
        x = out.discount
        row.update(discount_rate=x.discount_rate, discount_evidence=x.evidence, discount_reason=x.reason,
                   discount_evidence_ok=evidence_in_text(x.evidence, given))
    if "right_sale" in tasks:
        x = out.right_sale
        row.update(right_sale_start=x.start, right_sale_end=x.end, right_sale_evidence=x.evidence,
                   right_sale_reason=x.reason, right_sale_evidence_ok=evidence_in_text(x.evidence, given))
    return row
