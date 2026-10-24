"""시험 운행 문제 고르기 (학습 질문지에서만, 봉인 금지) → data/eval_v2/runs/{run_id}/qids.json.

    uv run python scripts/pick_trial.py --run-id trial-v7-20261024 --seed 7

경로별 개수 (2026-10-24 지시):
- 회사 조회 1번 (Q1 · Q2 · Q4) 8 · 비교 Q3 6 · 공시 찾기 Q6 6 · 이력 Q5 4
- 조항 QD 10 = T1~T4 각 2 + 함정 2
- 특수 8 = 감자 · unit_mismatch 회사 Q1 전부 + 나머지는 QE (flag별 고르게)
- Q7 8 = 모호 · 없음 · 범위 밖 섞어서 (고르게)
모자라면 있는 만큼.
"""

import argparse
import json
import random
from collections import defaultdict

from filingscope.config import DATA_DIR

EVAL = DATA_DIR / "eval_v2"


def special_q1(q: dict) -> bool:
    """정답 원자료에서 dilution flag 감자(capital_reduction) · unit_mismatch가 켜진 Q1."""
    s = json.dumps(q.get("source", {}), ensure_ascii=False)
    return q["id"].startswith("Q1") and ('"capital_reduction": true' in s or '"unit_mismatch": true' in s)


def spread(rng: random.Random, groups: dict, n: int) -> list[dict]:
    """그룹들에서 돌아가며 1개씩 (그룹 순서 · 그룹 안 순서는 섞음)."""
    pools = {k: rng.sample(v, len(v)) for k, v in sorted(groups.items())}
    keys, out = list(pools), []
    while len(out) < n and any(pools.values()):
        for k in keys:
            if pools[k] and len(out) < n:
                out.append(pools[k].pop())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    items = json.loads((EVAL / "questions_train.json").read_text(encoding="utf-8"))["items"]
    rng = random.Random(a.seed)
    by = lambda pre: [q for q in items if q["id"].startswith(pre)]
    picked: dict[str, list[dict]] = {}

    sp_q1 = [q for q in items if special_q1(q)]
    sp_ids = {q["id"] for q in sp_q1}
    picked["회사 조회 1번 (Q1·Q2·Q4)"] = spread(rng, {p: [q for q in by(p) if q["id"] not in sp_ids] for p in ("Q1", "Q2", "Q4")}, 8)
    picked["회사 조회 → 비교 (Q3)"] = rng.sample(by("Q3"), min(6, len(by("Q3"))))
    picked["공시 찾기 (Q6)"] = rng.sample(by("Q6"), min(6, len(by("Q6"))))
    picked["이력 (Q5)"] = rng.sample(by("Q5"), min(4, len(by("Q5"))))
    qd = []
    for t in ("T1", "T2", "T3", "T4"):
        pool = [q for q in items if q["id"].startswith(f"QD-{t}-")]
        qd += rng.sample(pool, min(2, len(pool)))
    traps = [q for q in items if q["category"] == "clause_trap"]
    qd += rng.sample(traps, min(2, len(traps)))
    picked["조항 RAG (QD)"] = qd
    qe = defaultdict(list)
    for q in by("QE"):
        qe[q["id"].split("-")[1]].append(q)
    picked["특수 사례 (QE + 감자·unit_mismatch Q1)"] = sp_q1[:8] + spread(rng, qe, max(0, 8 - len(sp_q1)))
    q7 = defaultdict(list)
    for q in by("Q7"):
        q7[q["expected"]["refuse"]].append(q)
    picked["도구 안 부름 · 되묻기 (Q7)"] = spread(rng, q7, 8)

    out = EVAL / "runs" / a.run_id
    out.mkdir(parents=True, exist_ok=True)
    doc = {"seed": a.seed, "split": "train", "n": sum(len(v) for v in picked.values()),
           "paths": {k: [q["id"] for q in v] for k, v in picked.items()}}
    (out / "qids.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in picked.items():
        print(f"{k} {len(v)}: {', '.join(q['id'] for q in v)}")
    print("합계", doc["n"])


if __name__ == "__main__":
    main()
