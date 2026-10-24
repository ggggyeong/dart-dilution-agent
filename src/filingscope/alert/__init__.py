"""알림 (입구 1, 명세 docs/alert_spec.md): 공시 1건의 사실 카드 JSON → LLM(Sonnet) → 문단 5개.

LLM은 문장만 쓴다. 숫자 · 사실은 카드에 있는 것만. 지시문은 docs/alert_prompt.md (이 파일을 그대로 읽는다).
모델은 환경변수 FILINGSCOPE_ALERT_MODEL로 바꿀 수 있다 (기본 Sonnet).
"""

import json
import os

import anthropic
from pydantic import BaseModel, Field

from filingscope.config import PROJECT_ROOT, get_settings
from filingscope.db import connect
from filingscope.facts import cb_facts, company_signals, dilution, rights_facts

PROMPT_PATH = PROJECT_ROOT / "docs" / "alert_prompt.md"
DEFAULT_MODEL = "claude-sonnet-5-5"
EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PRICE = {"claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00), "claude-haiku-5-5": (0.10, 0.50)}


class AlertOut(BaseModel):
    paragraphs: list[str] = Field(description="문단 5개. 각 문단 = 제목 줄 + 본문. 근거 줄은 넣지 않는다")


def model_name() -> str:
    return os.environ.get("FILINGSCOPE_ALERT_MODEL", DEFAULT_MODEL)


def build_input(rcept_no: str, conn=None) -> dict:
    """rcept_no → event_type → rights_facts / cb_facts + company_signals + 회사 dilution()의 flags (감자 추정)."""
    def go(c):
        t = c.execute("SELECT e.event_type FROM filings f JOIN events e ON e.event_id = f.event_id WHERE f.rcept_no = %s", (rcept_no,)).fetchone()
        if t is None:
            raise KeyError(f"공시 없음: {rcept_no}")
        card = (rights_facts if t[0] == "유상증자" else cb_facts)(rcept_no, conn=c)
        d = dilution(card["corp_code"], conn=c)
        return {"card": card, "company_signals": company_signals(card["corp_code"], conn=c),
                "dilution_flags": {**d["flags"], "reason": (d["dilution"] or {}).get("reason")}}
    if conn is not None:
        return go(conn)
    with connect() as c:
        return go(c)


def render(paragraphs: list[str], rcept_no: str) -> str:
    return "\n\n".join(p.strip() for p in paragraphs) + f"\n\n📎 근거: 접수번호 {rcept_no}"


def alert(rcept_no: str, conn=None, client: anthropic.Anthropic | None = None) -> dict:
    data = build_input(rcept_no, conn)
    card = data["card"]
    client = client or anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=180.0)
    model = model_name()
    kw = dict(model=model, max_tokens=4000, system=PROMPT_PATH.read_text(encoding="utf-8"), output_config={"effort": EFFORT},
              output_format=AlertOut,
              messages=[{"role": "user", "content": json.dumps(data, ensure_ascii=False)}])
    if not model.startswith("claude-haiku"):
        kw.update(betas=[FALLBACK_BETA], fallbacks="default")
    resp = client.beta.messages.parse(**kw)
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"refusal: {resp.stop_details}")
    paras = resp.parsed_output.paragraphs
    if len(paras) != 5:
        raise ValueError(f"문단 {len(paras)}개 (5개여야 함): {rcept_no}")
    return {"rcept_no": rcept_no, "corp_name": card["corp_name"], "type": card["type"], "paragraphs": paras,
            "evidence": [rcept_no], "model": model, "card": card, "company_signals": data["company_signals"],
            "dilution_flags": data["dilution_flags"],
            "text": render(paras, rcept_no),
            "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}}
