"""평가 실행 v2 (archive/2026-10-23_rag_v1/scripts/run_eval.py를 질문지 v2용으로): questions_{split}.json 문제를 answer_many로
→ data/eval_v2/runs/{run_id}/results.jsonl · meta.json.

    FILINGSCOPE_AS_OF=2026-10-19 uv run python scripts/run_eval_v2.py --run-id trial-v7-20261024 \
        --ids data/eval_v2/runs/trial-v7-20261024/qids.json   # 비용 상한 기본 $8 (--cost-cap)

- 문제 순서는 id 순. 한 문제 끝날 때마다 results.jsonl에 한 줄씩. seconds = 앞 문제가 끝난 뒤부터 이 문제가 끝날 때까지
  (묶음 첫 문제는 MCP 서버 띄우는 시간 포함).
- --cost-cap: 5문제씩 묶어 돌리고, 묶음마다 (지금까지 비용 + 문제당 평균 × 다음 묶음 수)가 상한을 넘을 것 같으면 멈춘다.
- 프롬프트 · 모델 · 제외 도구는 agent 코드 기본값 / 환경 변수 그대로.
"""

import argparse
import json
import os
import time

from filingscope.agent import answer_many
from filingscope.agent.graph import model_name, prompt_version
from filingscope.config import DATA_DIR

EVAL = DATA_DIR / "eval_v2"
CHUNK = 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--split", default="train", choices=["train", "sealed"])
    ap.add_argument("--ids", help="qids.json (paths: {경로: [id]}) — 이 문제들만")
    ap.add_argument("--cost-cap", type=float, default=8.0, help="달러 상한 (기본 $8, 2026-10-24 fix2)")
    a = ap.parse_args()
    doc = json.loads((EVAL / f"questions_{a.split}.json").read_text(encoding="utf-8"))
    as_of = os.environ.get("FILINGSCOPE_AS_OF") or doc["as_of"]
    items = sorted(doc["items"], key=lambda i: i["id"])
    if a.ids:
        want = {i for v in json.loads(open(a.ids, encoding="utf-8").read())["paths"].values() for i in v}
        items = [i for i in items if i["id"] in want]
        assert len(items) == len(want), f"질문지에 없는 id: {want - {i['id'] for i in items}}"
    out = EVAL / "runs" / a.run_id
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.jsonl"
    path.write_text("", encoding="utf-8")
    meta = {"run_id": a.run_id, "split": a.split, "as_of": as_of, "model": model_name(), "prompt_version": prompt_version(),
            "n": len(items), "cost_cap": a.cost_cap, "exclude_tools": os.environ.get("FILINGSCOPE_AGENT_EXCLUDE_TOOLS") or None,
            "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    t0, spent, done, stopped = time.time(), [0.0], [0], None
    last = [t0]

    for k in range(0, len(items), CHUNK):
        chunk = items[k:k + CHUNK]
        if a.cost_cap is not None and done[0]:
            est = spent[0] + spent[0] / done[0] * len(chunk)
            if est > a.cost_cap:
                stopped = f"비용 상한: 지금까지 ${spent[0]:.2f} ({done[0]}문제), 다음 {len(chunk)}문제까지 예상 ${est:.2f} > ${a.cost_cap}"
                print(stopped, flush=True)
                break

        def on_result(r, chunk=chunk, k=k):
            it = chunk[done[0] - k]
            done[0] += 1
            spent[0] += r["usage"]["cost_usd"]
            now = time.time()
            sec, last[0] = round(now - last[0], 1), now
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"id": it["id"], **r, "seconds": sec}, ensure_ascii=False, default=str) + "\n")
            print(f"  {done[0]}/{len(items)} {it['id']} · ${spent[0]:.2f} · {time.time() - t0:.0f}초", flush=True)

        answer_many([i["question"] for i in chunk], as_of=as_of, on_result=on_result)

    meta.update(done=done[0], cost_usd=round(spent[0], 4), stopped=stopped,
                seconds=round(time.time() - t0, 1), finished=time.strftime("%Y-%m-%d %H:%M:%S"))
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
