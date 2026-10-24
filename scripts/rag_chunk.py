"""새 RAG 조각 만들기 (docs/cc_task_rag_build.md 1~2장) → rag_chunks (method = size | section) + 임베딩 · tsv.

    uv run python scripts/rag_chunk.py --dry-run     # 조각만 만들어 통계 (DB · API 안 씀)
    uv run python scripts/rag_chunk.py               # DB에 넣고 임베딩 (비용 상한 $3, 넘을 것 같으면 멈춤)
    uv run python scripts/rag_chunk.py --update      # 규칙만 바꿨을 때: 글이 같은 조각은 topic · 제목만 고치고, 글이 바뀐 조각만 다시 임베딩

원문 글은 data/text_dump/free_text_2978.jsonl (칸 코드 = field). 대상: 유상증자 주주배정(CI 1·2·3) · CB.
통계 · 경계 못 찾은 예시 → data/rag_eval/runs/chunk_stats.json
"""

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict

from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect

SRC = DATA_DIR / "text_dump" / "free_text_2978.jsonl"
STATS = DATA_DIR / "rag_eval" / "runs" / "chunk_stats.json"
EMBED_MODEL = "text-embedding-3-small"   # 프로젝트에서 쓰던 모델 (RAG v1과 같음)
PRICE = 0.02        # $/1M 토큰
COST_CAP = 3.0
FIELDS = {"유상증자": ["ISS_FUNC", "PRC_PLN", "FREE_TEXT"], "CB": ["EXE_REG", "MIN_RSN", "OPT_FCT", "FREE_TEXT"]}   # 원문 순서
SHORT_REF = {"ISS_FUNC", "PRC_PLN", "OPT_FCT"}   # "참조/참고"만 적힌 60자 미만이면 뺌
SIZE, OVERLAP = 500, 100
CYCLE = r"매\s*\d+\s*개월(?:\s*\d+\s*일)?|매\s*월|매\s*분기"   # 리픽싱 주기: 매 N개월 · 매 N개월 N일 · 매월 · 매 분기
# 내용 꼬리표 (2026-10-23, "빠뜨리는 것보다 더 넣는 게 낫다"): 머리줄 규칙(topic)은 그대로 두고 조각 글 안에 이 말이 있으면 topics에 더함.
# 사용자 목록 + 전수 검사에서 빠진 공시에 쓰인 표현(중도상환권 · 조기상환권 · Putl Option 오타 · 매수청구권(Call …)),
# "동반매도청구권"(tag-along, 콜 아님)은 뺌. "매수청구권"을 통째로 넣지 않는 이유: "주식매수청구권"이 걸림.
CONTENT_TAGS = {
    "CB": {"T3": re.compile(r"(?<!동반)매도\s*청구|call\s*option|콜\s*옵션|조기\s*매입\s*청구|매수\s*청구권\s*\(\s*call", re.I),
           "T4": re.compile(r"조기\s*상환\s*청구|put\s*option|putl\s*option|풋\s*옵션|중도\s*상환\s*청구|중도\s*상환권|조기\s*상환권", re.I)},
    "유상증자": {"T1": re.compile(r"발행\s*가액|초과\s*청약|실권|잔여\s*주식|미달")},
}


def tags_for(doc_type: str, topic: str, text: str) -> list[str]:
    """처음 주제(topic) + 내용 꼬리표. T가 하나도 없으면 ['기타']."""
    tags = {topic} - {"기타"}
    for t, pat in CONTENT_TAGS[doc_type].items():
        if pat.search(text):
            tags.add(t)
    if doc_type == "CB" and re.search(CYCLE, text) and re.search(r"조정\s*일", text):
        tags.add("T2")
    return sorted(tags) or ["기타"]
T1_SPLIT, MAX_LEN = 800, 1200

# ---------- 항목 표시 ----------
_B = r"(?:^|(?<=[\s\]】).]))"   # 표시 앞: 글 시작 · 공백 · 줄바꿈 · 닫는 괄호 · 마침표 ("…한다.나. 합병"처럼 붙은 것)
_GA = "가나다라마바사아자차카타파하"
STYLES = {
    "ga": (_B + r"([가나다라마바사아자차카타파하])\s?\.(?!\s?목)", list(_GA)),   # "가 ." 허용, "다.목"(앞 항목 가리킴)은 아님
    "ga_paren": (_B + r"(?<!\()([가나다라마바사아자차카타파하])\)", list(_GA)),
    "paren_ga": (r"(?:^|(?<=[\s\]】.:]))\(([가나다라마바사아자차카타파하])\)", list(_GA)),
    "alpha": (_B + r"([a-j])\.(?![a-zA-Z])", list("abcdefghij")),
    "num_dot": (_B + r"(\d{1,2})\.(?!\d)", [str(i) for i in range(1, 41)]),
    "num_paren": (_B + r"(?<!\()(\d{1,2})\)", [str(i) for i in range(1, 41)]),
    "paren_num": (r"(?:^|(?<=[\s\]】.:]))\((\d{1,2})\)", [str(i) for i in range(1, 41)]),
    "circle": (r"([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])", list("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")),
    "roman": (r"(?:^|(?<=[\s\]】.:]))\((i|ii|iii|iv|v|vi|vii|viii|ix|x)\)", "i ii iii iv v vi vii viii ix x".split()),
    "giyeok": (_B + r"([ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ])\)", list("ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ")),
}


def seq_markers(text: str, style: str, line_start: bool = False) -> list[tuple[int, str]]:
    """그 표시가 순서대로 이어지는 위치만 [(위치, 표시 글자)]. 1번부터 · 2번부터(첫 항목에 표시가 없는 글) 둘 다 보고 긴 쪽.
    line_start면 줄 맨 앞만."""
    a, b = _seq(text, style, line_start, 0), _seq(text, style, line_start, 1)
    return b if len(b) > len(a) else a


def _seq(text: str, style: str, line_start: bool, first: int) -> list[tuple[int, str]]:
    pat, order = STYLES[style]
    out, want = [], first
    for m in re.finditer(pat, text, flags=re.M):
        if line_start and not (m.start() == 0 or text[m.start() - 1] == "\n"):
            continue
        if want < len(order) and m.group(1) == order[want]:
            out.append((m.start(), m.group(0).strip()))
            want += 1
    return out


def dominant(text: str, styles, line_start: bool = False) -> tuple[str | None, list]:
    """가장 많이 쓰인(순서대로 이어진 길이가 가장 긴) 표시. 2개 미만이면 없음."""
    best = (None, [])
    for s in styles:
        ms = seq_markers(text, s, line_start)
        if len(ms) > len(best[1]) or (len(ms) == len(best[1]) and ms and best[1] and ms[0][0] < best[1][0][0]):
            best = (s, ms)
    return best if len(best[1]) >= 2 else (None, [])


def cut(text: str, pos: list[int]) -> list[str]:
    pos = sorted(set(p for p in pos if 0 < p < len(text)))
    edges = [0, *pos, len(text)]
    return [text[a:b] for a, b in zip(edges, edges[1:])]


def first_line(s: str, n: int = 60) -> str:
    line = s.strip().split("\n", 1)[0]
    m = re.search(r"\s(?:\(\d{1,2}\)|\d{1,2}\)|①)", line[2:])   # 같은 줄에 붙은 다음 단계 표시 앞에서 자름
    if m:
        line = line[:m.start() + 2]
    return line.strip()[:n]


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def shingles(s: str, n: int = 5) -> set:
    s = norm(s)
    return {s[i:i + n] for i in range(max(1, len(s) - n + 1))}


# ---------- size ----------

def size_chunks(text: str) -> list[str]:
    if len(text) <= SIZE:
        return [text]
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + SIZE])
        if i + SIZE >= len(text):
            break
        i += SIZE - OVERLAP
    return out


# ---------- section ----------

def cap(title: str, body: str) -> list[str]:
    """1,200자 넘으면 문장 끝에서 나누고 제목을 다시 붙인다. 글이 제목으로 시작하면 다시 붙이지 않음."""
    body = body.strip()
    limit = MAX_LEN - (len(title) + 1 if title else 0)   # 제목 줄을 붙여도 1,200자 안
    pieces, rest = [], body
    while len(rest) > limit:
        window = rest[:limit]
        ends = [m.end() for m in re.finditer(r"(?:다\.|\.|\n)\s?", window)]
        at = ends[-1] if ends and ends[-1] > limit // 3 else limit
        pieces.append(rest[:at].strip())
        rest = rest[at:].strip()
    if rest:
        pieces.append(rest)
    return [p if (not title or norm(p).startswith(norm(title))) else f"{title}\n{p}" for p in pieces]


HEAD = re.compile(
    r"(?:(?P<br>\[|【|<|〈)|(?:^|(?<=[\s\]】)>.]))(?P<open>(?:※|■|▶|-|[가-하]\s?\.|\d{1,2}\.|\(\d{1,2}\)|\d{1,2}\))\s*)?)"
    r"[^\n\[\]【】<>〈〉.]{0,15}?(?P<kw>조기\s*상환\s*청구권|매도\s*청구권|매수\s*청구권|콜\s*옵션|풋\s*옵션|put\s*option|call\s*option)"
    r"(?P<tail>[^\n\]】>]{0,22}?)(?:에\s*관한\s*사항|\]|】|>|:)", re.I | re.M)


def heads_in(text: str) -> list[tuple[int, str, str]]:
    """풋 · 콜 머리줄 [(위치, T4|T3, 머리줄 글)]. 줄 맨 앞이거나 여는 표시([ · < · ※ · 항목 표시)로 시작할 때만,
    키워드 뒤 끝맺음 전까지 문장 끝("다.")이 없을 때만 (문장 한가운데를 머리줄로 잡지 않게)."""
    out = []
    for m in HEAD.finditer(text):
        at_line = m.start() == 0 or text[m.start() - 1] == "\n"
        strong = re.search(r"관한\s*사항$|[\]】>]$", m.group(0)) is not None   # "…에 관한 사항" · 닫는 괄호로 끝나면 문장 뒤에 붙어 있어도 머리줄
        if not (at_line or m.group("br") or m.group("open") or strong) or "다." in m.group("tail"):
            continue
        out.append((m.start(), kind_of(m.group("kw")), m.group(0).strip()))
    return out


OTHER_BRACKET = re.compile(r"(?:^|(?<=\s))[\[【][^\]】\n]{2,50}[\]】]")
PUT_WORDS = re.compile(r"조기\s*상환|풋|put", re.I)


def kind_of(kw: str) -> str:
    return "T4" if PUT_WORDS.search(kw) else "T3"


def t1_or_etc(title: str) -> str:
    return "T1" if re.search(r"발행가|가격\s*산정|배정", title) else "기타"


def top_sections(text: str) -> list[tuple[str, str]]:
    """주주배정 FREE_TEXT: 줄 맨 앞 가. 나. 다. … (없으면 (1) · 1)) 순서대로. 번호가 처음으로 돌아가면(소항목) 그 뒤로는 머리줄 모양(40자 이하 · ':' 없음)일 때만 다음 절."""
    for style in ("ga", "paren_num", "num_paren"):
        secs = _top(text, style)
        if len(secs) > 1:
            return secs
    return secs


def _top(text: str, style: str) -> list[tuple[str, str]]:
    pat, order = STYLES[style]
    starts, want, in_sub = [], 0, False
    for m in re.finditer(pat, text, flags=re.M):
        if not (m.start() == 0 or text[m.start() - 1] == "\n"):
            continue
        idx = order.index(m.group(1)) if m.group(1) in order else -1
        line = text[m.start():].split("\n", 1)[0]
        if want and idx == 0:
            in_sub = True
            continue
        if idx == want and (not in_sub or (len(line) <= 40 and ":" not in line)):
            starts.append(m.start())
            want += 1
            in_sub = False
    secs = cut(text, starts)
    return [(first_line(s), s) for s in secs if s.strip()]


def sub_split(title: str, topic: str, body: str, styles) -> list[tuple[str, str, str]]:
    style, ms = dominant(body, styles)
    if not style:
        return [(title, topic, b) for b in cap(title, body)]
    return [(title, topic, b) for piece in cut(body, [p for p, _ in ms]) if piece.strip() for b in cap(title, piece)]


def rights_free(text: str, st: Counter) -> list[tuple[str, str, str]]:
    secs = top_sections(text)
    if len(secs) <= 1:
        st["경계 못 찾음:유상증자 FREE_TEXT"] += 1
        return [("", "기타", b) for b in cap("", text)]
    out = []
    for title, body in secs:
        topic = t1_or_etc(title)
        if topic == "T1" and len(body) > T1_SPLIT:
            out += sub_split(title, topic, body, ["paren_num", "circle", "num_paren", "roman"])
        else:
            out += [(title, topic, b) for b in cap(title, body)]
    return out


def exe_reg(text: str, label: str, st: Counter) -> list[tuple[str, str, str]]:
    style, ms = dominant(text, ["ga", "ga_paren", "paren_ga", "num_paren", "paren_num", "circle", "giyeok", "num_dot", "alpha"])
    if not style:
        st["경계 못 찾음:EXE_REG"] += 1
        return [(label, "기타", b) for b in cap(label, text)]
    out = []
    pos = [p for p, _ in ms]
    pieces = cut(text, pos)
    marks = ([""] if pos[0] > 0 else []) + [m for _, m in ms]
    for mk, piece in zip(marks, pieces):
        if not piece.strip():
            continue
        # 2026-10-23 봉인 이후: "별도로" 표현 조건을 빼고 내용 기준 — 항목 안에 주기와 "조정일"이 함께 있으면 T2
        topic = "T2" if re.search(CYCLE, piece) and re.search(r"조정\s*일", piece) else "기타"
        title = f"{label} {mk}".strip()
        out += [(title, topic, b) for b in cap(title, piece)]
    return out


def cb_free(text: str, st: Counter, ex: dict) -> list[tuple[str, str, str]]:
    heads = heads_in(text)
    head_pos = {p for p, _, _ in heads}
    others = [m.start() for m in OTHER_BRACKET.finditer(text) if m.start() not in head_pos]
    if not heads:
        if PUT_WORDS.search(text) or re.search(r"매도\s*청구|call", text, re.I):
            st["풋·콜 머리줄 못 찾음:CB FREE_TEXT"] += 1
            ex.setdefault("풋·콜 머리줄 못 찾음", []).append(text[:160])
    # 기타 절 경계: 다른 대괄호 머리줄 + 줄 맨 앞 '…에 관한 사항' 짧은 줄
    for m in re.finditer(r"^[^\n]{0,40}에\s*관한\s*사항[^\n]{0,5}$", text, flags=re.M):
        if m.start() not in head_pos:
            others.append(m.start())
    starts = sorted({p for p, _, _ in heads} | set(others))
    kinds = {p: (k, h) for p, k, h in heads}
    pieces = cut(text, starts)
    offs = [0, *[p for p in starts if 0 < p < len(text)]]
    out = []
    for off, piece in zip(offs, pieces):
        if not piece.strip():
            continue
        if off in kinds:
            topic, head = kinds[off]
            title = head[:60]
            body_end = len(piece)
            style, ms = dominant(piece[len(head):], ["ga", "ga_paren", "paren_ga", "num_paren", "paren_num", "num_dot", "circle"])
            if style:
                pos = [len(head) + p for p, _ in ms]
                out += [(title, topic, b) for part in cut(piece, pos) if part.strip() for b in cap(title, part)]
            else:
                out += [(title, topic, b) for b in cap(title, piece[:body_end])]
        else:
            title = first_line(piece)
            style, ms = dominant(piece, ["ga", "num_dot"], line_start=True)
            parts = cut(piece, [p for p, _ in ms]) if style else [piece]
            out += [(first_line(part) or title, "기타", b) for part in parts if part.strip() for b in cap(first_line(part) or title, part)]
    if len(out) <= 1 and not heads:
        st["경계 못 찾음:CB FREE_TEXT"] += 1
    return out


def opt_fct(text: str, label: str, free_chunks: list[tuple[str, str, str]], st: Counter, ex: dict) -> list[tuple[str, str, str]]:
    heads = heads_in(text)
    heads = [h for i, h in enumerate(heads) if i == 0 or h[1] != heads[i - 1][1]]   # 풋 ↔ 콜이 바뀌는 곳에서만 나눔
    if not heads:   # 머리줄 없이 내용만: 낱말로 주제
        has_put, has_call = bool(PUT_WORDS.search(text)), bool(re.search(r"매도\s*청구|매수\s*청구|콜|call", text, re.I))
        if has_put != has_call:
            heads = [(0, "T4" if has_put else "T3", label)]
        else:
            st["경계 못 찾음:OPT_FCT"] += 1
            return [(label, "기타", b) for b in cap(label, text)]
    if heads[0][0] > 0:
        heads[0] = (0, heads[0][1], heads[0][2])   # 머리줄 앞 짧은 글은 첫 부분에 붙임
    out = []
    pos = [p for p, _, _ in heads]
    for (p, topic, head), part in zip(heads, cut(text, pos)):
        if not part.strip():
            continue
        same = "".join(b for _, t, b in free_chunks if t == topic)
        a = shingles(part)
        if same and len(a & shingles(same)) / max(1, len(a)) >= 0.8:
            st["OPT_FCT 중복으로 뺌"] += 1
            continue
        title = head[:60]
        out += [(title, topic, b) for b in cap(title, part)]
    return out


# ---------- 전체 ----------

def load():
    by = defaultdict(lambda: defaultdict(list))
    meta = {}
    for line in SRC.open(encoding="utf-8"):
        r = json.loads(line)
        if r["type"] == "CB" or r["ci_mth"] in (1, 2, 3):
            meta[r["rcept_no"]] = r["type"]
            if r["field"] in FIELDS[r["type"]]:
                by[r["rcept_no"]][r["field"]].append(r)
    return meta, by


def build(meta, by) -> tuple[list[dict], Counter, dict]:
    rows, st, ex = [], Counter(), {}
    for no in sorted(meta):
        typ = meta[no]
        seq = {"size": 0, "section": 0}
        free_sec = []
        # section의 OPT_FCT 중복 판단은 같은 공시 FREE_TEXT 조각이 필요 → FREE_TEXT를 먼저 계산해 둠
        if typ == "CB":
            for r in by[no].get("FREE_TEXT", []):
                free_sec += cb_free(r["text"], Counter(), {})
        for field in FIELDS[typ]:
            for r in by[no].get(field, []):
                text = r["text"].strip()
                if field in SHORT_REF and len(text) < 60 and re.search(r"참조|참고", text):
                    st[f"참조만이라 뺌:{field}"] += 1
                    continue
                for piece in size_chunks(text):
                    seq["size"] += 1
                    rows.append(dict(method="size", rcept_no=no, doc_type=typ, field=field, section_title=None, topic=None, topics=None,
                                     seq=seq["size"], text=piece))
                label = r["label"] or field
                if typ == "유상증자":
                    secs = rights_free(text, st) if field == "FREE_TEXT" else [(label, "T1", b) for b in cap(label, text)]
                elif field == "EXE_REG":
                    secs = exe_reg(text, label, st)
                elif field == "MIN_RSN":
                    secs = [(label, "T2", b) for b in cap(label, text)]
                elif field == "OPT_FCT":
                    secs = opt_fct(text, label, free_sec, st, ex)
                else:
                    secs = cb_free(text, st, ex)
                for title, topic, body in secs:
                    seq["section"] += 1
                    rows.append(dict(method="section", rcept_no=no, doc_type=typ, field=field, section_title=title or None,
                                     topic=topic, topics=tags_for(typ, topic, body), seq=seq["section"], text=body))
    return rows, st, ex


def stats(rows, st, ex) -> dict:
    out = {"counts": dict(st), "examples": {k: v[:5] for k, v in ex.items()}}
    for m in ("size", "section"):
        rs = [r for r in rows if r["method"] == m]
        per = Counter(r["rcept_no"] for r in rs)
        lens = sorted(len(r["text"]) for r in rs)
        out[m] = {"조각": len(rs), "공시": len(per), "공시당 중앙": statistics.median(per.values()), "공시당 최대": max(per.values()),
                  "길이 중앙": statistics.median(lens), "길이 90%": lens[int(len(lens) * 0.9)], "길이 최대": lens[-1],
                  "칸별": dict(Counter(r["field"] for r in rs))}
        if m == "section":
            out[m]["topic별"] = dict(Counter(r["topic"] for r in rs))
            out[m]["topics별 (꼬리표)"] = dict(Counter(t for r in rs for t in r["topics"]))
            out[m]["꼬리표 2개 이상"] = sum(len([t for t in r["topics"] if t != "기타"]) >= 2 for r in rs)
            out[m]["topic×칸"] = {f"{k[0]}·{k[1]}": v for k, v in Counter((r["topic"], r["field"]) for r in rs).items()}
    return out


def embed_all(conn, method: str):
    from openai import OpenAI
    client = OpenAI(api_key=get_settings().openai_api_key.get_secret_value(), timeout=120, max_retries=3)
    rows = conn.execute("SELECT id, text FROM rag_chunks WHERE method = %s AND embedding IS NULL ORDER BY id", (method,)).fetchall()
    total_chars = sum(len(t) for _, t in rows)
    tokens, done_chars = 0, 0
    for i in range(0, len(rows), 256):
        batch = rows[i:i + 256]
        resp = client.embeddings.create(model=EMBED_MODEL, input=[t for _, t in batch])
        tokens += resp.usage.total_tokens
        done_chars += sum(len(t) for _, t in batch)
        projected = tokens / done_chars * total_chars * PRICE / 1e6
        if i == 0:
            print(f"  {method}: 예상 ${projected:.3f} ({len(rows):,}조각)")
            if projected > COST_CAP:
                raise SystemExit(f"예상 비용 ${projected:.2f} > ${COST_CAP} — 멈춤")
        with conn.transaction():
            for (cid, _), d in zip(batch, resp.data):
                conn.execute("UPDATE rag_chunks SET embedding = %s::vector WHERE id = %s",
                             ("[" + ",".join(f"{x:.7g}" for x in d.embedding) + "]", cid))
    return tokens


def update(conn, rows) -> dict:
    """(method, rcept_no, seq)로 맞춰 글 · 칸이 같으면 topic · section_title만 고치고, 다르면 지우고 새로 넣어 embedding을 비운다."""
    old = {(m, n.strip(), q): (i, f, t, st, x, ts) for i, m, n, q, f, t, st, x, ts in conn.execute(
        "SELECT id, method, rcept_no, seq, field, topic, section_title, text, topics FROM rag_chunks").fetchall()}
    new = {(r["method"], r["rcept_no"], r["seq"]): r for r in rows}
    c = Counter()
    with conn.transaction():
        for key, r in new.items():
            o = old.get(key)
            if o and o[1] == r["field"] and o[4] == r["text"]:
                if (o[2], o[3], o[5]) != (r["topic"], r["section_title"], r["topics"]):
                    conn.execute("UPDATE rag_chunks SET topic = %s, section_title = %s, topics = %s WHERE id = %s",
                                 (r["topic"], r["section_title"], r["topics"], o[0]))
                    c[f"꼬리표만 바뀜:{r['method']}"] += 1
                    if o[2] != r["topic"]:
                        c[f"topic {o[2]}→{r['topic']}"] += 1
                    for t in set(r["topics"] or []) - set(o[5] or []):
                        c[f"topics에 더함 {t}"] += 1
                continue
            if o:
                conn.execute("DELETE FROM rag_chunks WHERE id = %s", (o[0],))
                c[f"글 바뀜:{r['method']}"] += 1
            else:
                c[f"새 조각:{r['method']}"] += 1
            conn.execute("""INSERT INTO rag_chunks (method, rcept_no, doc_type, field, section_title, topic, topics, seq, text, tsv)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('simple', %s))""",
                         (r["method"], r["rcept_no"], r["doc_type"], r["field"], r["section_title"], r["topic"], r["topics"], r["seq"], r["text"], r["text"]))
        for key, o in old.items():
            if key not in new:
                conn.execute("DELETE FROM rag_chunks WHERE id = %s", (o[0],))
                c[f"없어진 조각:{key[0]}"] += 1
    return dict(c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()
    meta, by = load()
    rows, st, ex = build(meta, by)
    s = stats(rows, st, ex)
    STATS.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps({k: v for k, v in s.items() if k != "examples"}, ensure_ascii=False, indent=1))
    if a.dry_run:
        STATS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    import time
    t0 = time.time()
    if a.update:
        with connect() as conn:
            diff = update(conn, rows)
            tok = {m: embed_all(conn, m) for m in ("size", "section")}
        s["update"] = {**diff, "re_embed_tokens": tok, "re_embed_cost_usd": round(sum(tok.values()) * PRICE / 1e6, 5), "seconds": round(time.time() - t0)}
        old = json.loads(STATS.read_text(encoding="utf-8")) if STATS.exists() else {}
        s["embedding"] = old.get("embedding", {})   # 처음 전체 임베딩 기록은 그대로
        STATS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        print(s["update"])
        return
    with connect() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM rag_chunks")
            with conn.cursor().copy("COPY rag_chunks (method, rcept_no, doc_type, field, section_title, topic, topics, seq, text) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row([r[k] for k in ("method", "rcept_no", "doc_type", "field", "section_title", "topic", "topics", "seq", "text")])
            conn.execute("UPDATE rag_chunks SET tsv = to_tsvector('simple', text)")
        tok = {m: embed_all(conn, m) for m in ("size", "section")}
    s["embedding"] = {"model": EMBED_MODEL, "tokens": tok, "cost_usd": round(sum(tok.values()) * PRICE / 1e6, 4), "seconds": round(time.time() - t0)}
    STATS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(s["embedding"])


if __name__ == "__main__":
    main()
