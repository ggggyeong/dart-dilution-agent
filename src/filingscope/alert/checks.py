"""알림 문장 검사 (명세 6장): 숫자 검사 · 금지어 검사 · 문단 5 신호 수.

숫자 검사: 문장의 숫자가 전부 카드 · company_signals에서 나올 수 있는 숫자인가.
  허용 = JSON의 모든 숫자 (글자 안의 숫자 포함: 근거 인용 · 이름) + 원→억(소수 0~2자리) · 만 + 소수 반올림(0~2자리)
         + 비 × 100 (debt_ratio) + 자금 칸 비중(÷ 조달액, 정수 · 1자리) + 대상자 수("외 ○곳" 포함) · 세는 신호 수
         + 최근 2년 유형별 건수 + 날짜의 연 · 월 · 일 + 지시문 고정 숫자(1년 · 2년 · 30억).
금지어 검사: 제목 줄("🔍 이 회사 괜찮나" 등 명세가 정한 제목)은 빼고 본문만.
"""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

FORBIDDEN = ["추천", "매수", "매도", "위험하", "안전하", "괜찮", "좋아요", "나빠요"]
TITLES = ["📊 얼마나 큰가", "💰 왜 조달하나", "👤 누가 사나", "📅 언제 풀리나", "🙋 나는 뭘 할 수 있나 · 언제", "🔍 이 회사 괜찮나"]
COUNTED = ("non_cash", "major_holder", "debt_heavy", "halted", "public_single")
TEMPLATE_NUMBERS = {1, 2, 30}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
FUNDS = ["fund_facility", "fund_business_acq", "fund_operating", "fund_debt", "fund_other_corp", "fund_other"]


def _d(x) -> Decimal | None:
    try:
        return Decimal(str(x).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _q(x: Decimal, places: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP).normalize()


def _walk(o, nums: set, dates: set):
    if isinstance(o, dict):
        for v in o.values():
            _walk(v, nums, dates)
    elif isinstance(o, list):
        for v in o:
            _walk(v, nums, dates)
    elif isinstance(o, bool) or o is None:
        return
    elif isinstance(o, (int, float)):
        nums.add(Decimal(str(o)))
    elif isinstance(o, str):
        for m in _DATE.finditer(o):
            dates.add(m.group())
        for m in _NUM.finditer(_DATE.sub(" ", o)):
            d = _d(m.group())
            if d is not None:
                nums.add(d)


def counted_signals(card: dict) -> int:
    n = sum(1 for s in card["signals"] if s["name"] in COUNTED)
    return n + bool(card["company"]["repeat"]) + bool(card["company"]["withdrawn"])


def allowed(data: dict) -> tuple[set, set]:
    card, cs = data["card"], data["company_signals"]
    nums, dates = set(), set()
    _walk(data, nums, dates)
    out = set()
    for x in nums:
        out.add(x.normalize())
        if abs(x) >= 10000:
            for p in (0, 1, 2):
                out.add(_q(x / Decimal(10) ** 8, p))
            for p in (0, 1):
                out.add(_q(x / Decimal(10) ** 4, p))
        if x != x.to_integral_value():
            for p in (0, 1, 2):
                out.add(_q(x, p))
        if 0 < abs(x) <= 1:
            for p in (0, 1):
                out.add(_q(x * 100, p))
    v = card["values"]
    base = card["calc"].get("raised") if card["type"] == "유상증자" else v.get("amount")
    if base:
        for k in FUNDS:
            if v.get(k):
                for p in (0, 1):
                    out.add(_q(Decimal(v[k]) / Decimal(base) * 100, p))
    out |= {Decimal(len(card["allottees"])), Decimal(counted_signals(card)), Decimal(card["company"]["repeat_count"])}
    out |= {Decimal(len(card["allottees"]) - k) for k in range(len(card["allottees"]) + 1)}  # "외 ○곳"
    as_of = date.fromisoformat(cs["as_of"])
    try:
        since = as_of.replace(year=as_of.year - 2)
    except ValueError:
        since = as_of.replace(year=as_of.year - 2, day=28)
    win = [e for e in cs["events"] if since <= date.fromisoformat(e["first_date"]) <= as_of]
    for t in ("유상증자", "CB"):
        out.add(Decimal(sum(1 for e in win if e["type"] == t)))
    for dt in dates:
        y, m, d = dt.split("-")
        out |= {Decimal(y), Decimal(int(m)), Decimal(int(d))}
    out |= {Decimal(n) for n in TEMPLATE_NUMBERS}
    return {x.normalize() for x in out}, dates


def body_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and ln.strip() not in TITLES and not ln.startswith("📎 근거")]


def number_check(text: str, data: dict) -> list[str]:
    """카드에서 나올 수 없는 숫자 · 날짜 목록 (빈 목록이면 통과)."""
    ok, dates = allowed(data)
    bad = []
    for ln in body_lines(text):
        for m in _DATE.finditer(ln):
            if m.group() not in dates:
                bad.append(f"날짜 {m.group()}")
        for m in _NUM.finditer(_DATE.sub(" ", ln)):
            d = _d(m.group())
            if d is not None and d.normalize() not in ok:
                bad.append(m.group())
    return bad


def forbidden_check(text: str) -> list[str]:
    body = "\n".join(body_lines(text))
    return [w for w in FORBIDDEN if w in body]


def signal_count_check(paragraphs: list[str], card: dict) -> str | None:
    """문단 5의 '주의할 점 N가지' · ⚠️ 줄 수가 세는 신호 수와 같은가. 다르면 이유."""
    n = counted_signals(card)
    p5 = paragraphs[4]
    if n == 0:
        return None if "특별한 신호는 없어요" in p5 else "신호 0인데 '특별한 신호는 없어요'가 없음"
    m = re.search(r"주의할 점 (\d+)가지", p5)
    warns = sum(1 for ln in p5.splitlines() if ln.strip().startswith("⚠️"))
    if not m or int(m.group(1)) != n or warns != n:
        return f"세는 신호 {n}, 문장 N={m.group(1) if m else '-'}, ⚠️ 줄 {warns}"
    return None


def repeat_check(paragraphs: list[str], data: dict) -> str | None:
    """'최근 2년 ○번째 조달이에요 (유상증자 ○ · CB ○)'의 숫자가 카드 · events와 같은가. 작은 정수는 숫자 검사로 못 잡아서 따로."""
    card, cs = data["card"], data["company_signals"]
    m = re.search(r"최근 2년 (\d+)번째 조달이에요 \(유상증자 (\d+) · CB (\d+)\)", paragraphs[4])
    if not card["company"]["repeat"]:
        return "repeat가 꺼져 있는데 반복 조달 문장이 있음" if m else None
    if not m:
        return "repeat가 켜져 있는데 반복 조달 문장이 없음"
    as_of = date.fromisoformat(cs["as_of"])
    try:
        since = as_of.replace(year=as_of.year - 2)
    except ValueError:
        since = as_of.replace(year=as_of.year - 2, day=28)
    win = [e for e in cs["events"] if since <= date.fromisoformat(e["first_date"]) <= as_of]
    want = (card["company"]["repeat_count"], sum(e["type"] == "유상증자" for e in win), sum(e["type"] == "CB" for e in win))
    got = tuple(int(x) for x in m.groups())
    return None if got == want else f"반복 조달 {got} ≠ 실제 {want} (번째, 유상증자, CB)"
