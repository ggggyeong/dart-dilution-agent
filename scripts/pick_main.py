"""본 실행 문제 목록 (질문지 파일은 그대로, qid 목록만) → data/eval_v2/main_qids.json.

    uv run python scripts/pick_main.py --seed 11

2026-10-24 지시: Q1~Q7 학습 전부 + QE 학습 전부 + QD 50 (T1~T4 각 10 = 공시 말투 F 5 · 투자자 말투 I 5, 함정 10).
시험 운행 qids.json의 QD는 반드시 넣고, 나머지는 seed로 무작위. 시험 운행 50문제가 전부 들어 있는지 확인.
"""

import argparse
import json
import random

from filingscope.config import DATA_DIR

EVAL = DATA_DIR / "eval_v2"
TRIAL = EVAL / "runs" / "trial-v7-20261024" / "qids.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    items = json.loads((EVAL / "questions_train.json").read_text(encoding="utf-8"))["items"]
    ids = [q["id"] for q in sorted(items, key=lambda q: q["id"])]
    trial = {i for v in json.loads(TRIAL.read_text(encoding="utf-8"))["paths"].values() for i in v}
    rng = random.Random(a.seed)

    def fill(pool: list[str], n: int) -> list[str]:
        must = [i for i in pool if i in trial]
        rest = [i for i in pool if i not in trial]
        assert len(must) <= n, (pool, must)
        return sorted(must + rng.sample(rest, n - len(must)))

    paths = {f"Q{k}": [i for i in ids if i.startswith(f"Q{k}-")] for k in range(1, 8)}
    for t in ("T1", "T2", "T3", "T4"):
        for style in ("F", "I"):
            paths[f"QD {t} {style}"] = fill([i for i in ids if i.startswith(f"QD-{t}-") and i.endswith(f"-{style}")], 5)
    paths["QD 함정"] = fill([i for i in ids if i.startswith("QD-X-")], 10)
    paths["QE"] = [i for i in ids if i.startswith("QE-")]

    chosen = {i for v in paths.values() for i in v}
    missing = sorted(trial - chosen)
    doc = {"seed": a.seed, "split": "train", "n": len(chosen), "trial_ids": sorted(trial), "trial_missing": missing,
           "paths": paths}
    (EVAL / "main_qids.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in paths.items():
        print(f"{k}: {len(v)}")
    print(f"합계 {len(chosen)} · 시험 운행 50 중 빠진 것 {missing}")
    assert len(chosen) == 178 and not missing


if __name__ == "__main__":
    main()
