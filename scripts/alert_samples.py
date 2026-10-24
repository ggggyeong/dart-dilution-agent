"""알림 10건 생성 + 검사 (명세 6장) → data/reports/alert/.

    uv run python scripts/alert_samples.py          # 이미 만든 것은 다시 만들지 않음
    uv run python scripts/alert_samples.py --redo   # 지시문을 고친 뒤 전부 다시
"""

import argparse
import json

import anthropic

from filingscope.alert import PRICE, alert, build_input, model_name
from filingscope.alert.checks import counted_signals, forbidden_check, number_check, repeat_check, signal_count_check
from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect

OUT = DATA_DIR / "reports" / "alert"
SAMPLES = [
    ("20260820000443", "엔투텍 — 예시와 같은 내용인가"),
    ("20260522000636", "코이즈 — 신호 0 → 특별한 신호는 없어요"),
    ("20250909000002", "압타머 — shares_before_missing · major_holder(변경 예정 최대주주)"),
    ("20260828001315", "뷰노 — 예시와 같은 내용인가"),
    ("20260706000282", "뉴온 — 3자배정 · 참여 불가 · major_holder · acquire_target"),
    ("20240722000152", "아스트 — non_cash 빚→주식 · 대상자 7곳"),
    ("20240328001867", "카프로 — non_cash 섞임"),
    ("20260520000273", "코다코 — 대상자 10명 (indirect인 코다코 공시는 없음)"),
    ("20250225004081", "제일엠앤에스 — dilution() max_pct 89.7%의 기준 CB"),
    ("20241115000316", "STX — dilution() max_pct 84.5%의 기준 CB"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=180.0)
    rows, cost = [], 0.0
    with connect() as conn:
        for no, why in SAMPLES:
            path = OUT / f"{no}.json"
            if path.exists() and not a.redo:
                r = json.loads(path.read_text(encoding="utf-8"))
            else:
                r = alert(no, conn=conn, client=client)
                path.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
            pin, pout = PRICE.get(r["model"], (0, 0))
            cost += (r["usage"]["input_tokens"] * pin + r["usage"]["output_tokens"] * pout) / 1e6
            data = {"card": r["card"], "company_signals": r["company_signals"], "dilution_flags": r.get("dilution_flags", {})}
            rows.append(dict(no=no, why=why, r=r, nums=number_check(r["text"], data), words=forbidden_check(r["text"]),
                             sig=signal_count_check(r["paragraphs"], r["card"]), rep=repeat_check(r["paragraphs"], data), n=counted_signals(r["card"])))
    L = [f"# 알림 10건 ({model_name()}) — 검사 결과", "",
         f"- 비용: ${cost:.3f}",
         f"- 숫자 검사 실패: {sum(bool(x['nums']) for x in rows)}건 · 금지어 실패: {sum(bool(x['words']) for x in rows)}건 · 문단 5 신호 수 불일치: {sum(bool(x['sig']) for x in rows)}건 · 반복 조달 숫자 불일치: {sum(bool(x['rep']) for x in rows)}건", "",
         "| 공시 | 숫자 검사 | 금지어 | 문단 5 신호 수 (세는 신호) | 반복 조달 숫자 |", "|---|---|---|---|---|"]
    for x in rows:
        L.append(f"| {x['r']['corp_name']} {x['no']} | {'통과' if not x['nums'] else '실패: ' + ', '.join(x['nums'])} | "
                 f"{'통과' if not x['words'] else '실패: ' + ', '.join(x['words'])} | {'맞음' if not x['sig'] else x['sig']} ({x['n']}) | {x['rep'] or '맞음'} |")
    for x in rows:
        L += ["", f"## {x['r']['corp_name']} {x['no']} ({x['r']['type']}) — {x['why']}", "", x["r"]["text"]]
    (OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
