"""정답표용 LLM (Opus). 실제 판정에 쓸 LLM 설정과 분리해서 관리한다.

- 원문 전체를 준다 (실제 판정용 모델은 필요한 부분만 받음 — 명세 6장).
- 한 공시의 여러 과제(납입 방식 · 할인율 · 권리 매도 기간)를 한 번에 묻는다.
- 근거 문장은 원문에서 그대로 옮기게 하고, 원문에 실제로 있는지 코드가 확인한다.
"""

import re
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field, create_model

from filingscope.parse.acode import read_root, text_of

MODEL = "claude-opus-5-5"
EFFORT = "high"  # 충분히 생각
PROMPT_VERSION = "answer-key-v1"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PRICE_PER_MTOK = {"input": 4.00, "output": 20.00}

SYSTEM_PROMPT = """너는 한국 DART 유상증자 공시(주요사항보고서) 원문을 읽고, 요청받은 항목만 판정한다.

공통 규칙
- 오직 주어진 원문만 근거로 한다. 추측하거나 계산해서 숫자를 만들지 않는다. 원문에 적힌 값만 쓴다.
- evidence(근거 문장)는 원문에서 판정의 근거가 되는 부분을 **글자 그대로 복사**한다 (한 문장 또는 짧은 구절, 200자 이내). 바꿔 쓰거나 요약하지 않는다.
- 원문으로 판단할 수 없으면 값은 null(또는 납입 방식은 "불명")로 두고 reason에 이유를 적는다.
- reason은 한국어 한 줄.

[납입 방식 payment]
신주 대금을 무엇으로 내는지 판정한다.
- 현금: 인수인이 현금으로 납입
- 빚→주식: 인수인이 회사에 대해 가진 채권(대여금, 차입금, 매입채무, 회생채권 등)을 출자전환하거나 주금납입채무와 상계해 주식으로 바꿈. 금전채권을 '현물출자' 형식으로 넣는 경우도 여기에 해당
- 현물: 주식, 지분, 특허, 부동산 등 금전채권이 아닌 자산을 출자
- 섞임: 같은 증자에서 현금과 현금 외(빚→주식 또는 현물)가 함께 쓰임
- 불명: 원문으로 판단할 수 없음
주의: "현금으로 납입하거나 상계할 수 있음" 같은 선택지 문구, "현물출자: 해당없음" 같은 부정 문장, 정관 조항을 옮겨 적은 부분은 실제 납입 방식이 아니다.

[할인율 discount]
주주배정 계열 증자에서 발행가액(예정발행가액)을 정할 때 기준주가에 적용하는 할인율(%)을 숫자로 적는다.
보통 "가. 신주 발행가액 산정방법" 같은 부분에 "할인율 25%를 적용"처럼 적혀 있다. 할인이면 양수, 할증이면 음수.
1차·2차 발행가 등에 서로 다른 할인율이 있으면 예정발행가액(1차)에 쓰는 값을 적고 reason에 밝힌다.

[권리 매도 기간 right_sale]
신주인수권증서의 매매(상장 거래) 기간 시작일과 종료일을 YYYY-MM-DD로 적는다.
보통 "신주인수권에 관한 사항" 같은 부분에 적혀 있다."""


class PaymentLabel(BaseModel):
    payment_type: Literal["현금", "빚→주식", "현물", "섞임", "불명"]
    evidence: Optional[str] = Field(description="원문 그대로 복사한 근거")
    reason: str


class DiscountLabel(BaseModel):
    discount_rate: Optional[float] = Field(description="할인 양수, 할증 음수, 없으면 null")
    evidence: Optional[str]
    reason: str


class RightSaleLabel(BaseModel):
    start: Optional[str] = Field(description="YYYY-MM-DD 또는 null")
    end: Optional[str] = Field(description="YYYY-MM-DD 또는 null")
    evidence: Optional[str]
    reason: str


TASK_MODELS = {"payment": PaymentLabel, "discount": DiscountLabel, "right_sale": RightSaleLabel}
TASK_NAMES = {"payment": "납입 방식", "discount": "할인율", "right_sale": "권리 매도 기간"}


def output_model(tasks: list[str]) -> type[BaseModel]:
    """요청 과제만 담은 출력 형식."""
    return create_model("AnswerKey", **{t: (TASK_MODELS[t], ...) for t in tasks})


def document_text(path) -> str:
    """원문 전체를 읽기 좋은 글로: 표는 줄마다 칸을 ' | '로, 표 밖 문단은 한 줄씩. 정정 신고 부분 포함."""
    root = read_root(path)
    body = root.find(".//BODY")
    lines: list[str] = []

    def walk(el):
        if not isinstance(el.tag, str):
            return
        if el.tag == "TR":
            cells = [text_of(c) for c in el if isinstance(c.tag, str) and c.tag in ("TD", "TH", "TE", "TU")]
            line = " | ".join(c for c in cells if c)
            if line:
                lines.append(line)
            return
        if el.tag in ("P", "TITLE"):
            t = text_of(el)
            if t:
                lines.append(t)
            return
        for ch in el:
            walk(ch)

    walk(body if body is not None else root)
    return "\n".join(lines)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def evidence_in_text(evidence: str | None, text: str) -> bool | None:
    if not evidence:
        return None
    return _norm(evidence) in _norm(text)


def label(client: anthropic.Anthropic, text: str, tasks: list[str], system: str = SYSTEM_PROMPT):
    """(parsed, usage, stop_reason)."""
    want = ", ".join(f"{TASK_NAMES[t]}({t})" for t in tasks)
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=system,
        output_config={"effort": EFFORT},
        output_format=output_model(tasks),
        messages=[{"role": "user", "content": f"판정할 항목: {want}\n\n<원문>\n{text}\n</원문>"}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"refusal: {resp.stop_details}")
    return resp.parsed_output, resp.usage, resp.stop_reason
