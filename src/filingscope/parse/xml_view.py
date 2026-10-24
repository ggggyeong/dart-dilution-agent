"""DART 원문 XML → 공시 화면처럼 펼친 HTML. 칸마다 코드 배지, 자유 서술(<P>)은 색으로 구분. 공시 종류 무관."""

import html
from pathlib import Path

from filingscope.parse.acode import CELL_TAGS, read_root

_BLOCK = {"DOCUMENT", "BODY", "LIBRARY", "SECTION-1", "SECTION-2", "SECTION-3", "TABLE-GROUP", "COVER", "PART"}
_SKIP = {"COLGROUP", "COL", "DOCUMENT-NAME", "FORMULA-VERSION", "COMPANY-NAME"}

CSS = """
:root { --bg:#ffffff; --fg:#1d1d1f; --muted:#6b7280; --line:#d1d5db; --badge:#1d4ed8; --badge-bg:#dbeafe;
        --unit:#7c2d12; --unit-bg:#ffedd5; --free-bg:#ecfdf5; --free-line:#10b981; --corr-bg:#fef2f2; --corr-line:#ef4444; }
@media (prefers-color-scheme: dark) { :root { --bg:#111827; --fg:#e5e7eb; --muted:#9ca3af; --line:#374151;
        --badge:#93c5fd; --badge-bg:#1e3a8a; --unit:#fdba74; --unit-bg:#7c2d12; --free-bg:#064e3b; --free-line:#34d399;
        --corr-bg:#450a0a; --corr-line:#f87171; } }
body { background:var(--bg); color:var(--fg); font:14px/1.5 -apple-system, "Apple SD Gothic Neo", sans-serif; margin:0; padding:16px; }
main { max-width:1000px; margin:0 auto; }
table { border-collapse:collapse; width:100%; margin:8px 0; }
td, th { border:1px solid var(--line); padding:4px 6px; vertical-align:top; }
.badge { display:inline-block; font:11px/1.4 ui-monospace, monospace; padding:0 4px; border-radius:4px; margin-right:4px;
         color:var(--badge); background:var(--badge-bg); }
.badge.unit { color:var(--unit); background:var(--unit-bg); }
p.free { background:var(--free-bg); border-left:3px solid var(--free-line); padding:4px 8px; margin:4px 0; }
.correction { background:var(--corr-bg); border:2px solid var(--corr-line); padding:8px; margin:8px 0; }
.correction > .tag { color:var(--corr-line); font-weight:600; }
header { border-bottom:1px solid var(--line); margin-bottom:12px; padding-bottom:8px; color:var(--muted); }
.legend span { margin-right:12px; }
a { color:var(--badge); }
"""


def _render(el, in_coded: bool) -> str:
    if not isinstance(el.tag, str):
        return html.escape(el.tail or "")
    tag = el.tag
    tail = html.escape(el.tail or "")
    if tag in _SKIP:
        return tail
    text = html.escape(el.text or "")
    code, unit = el.get("ACODE"), el.get("AUNIT")
    coded = bool((code or unit) and tag in CELL_TAGS)
    inner = text + "".join(_render(c, in_coded or coded) for c in el)

    if tag in CELL_TAGS:
        attrs = "".join(f' {a.lower()}="{html.escape(el.get(a))}"' for a in ("COLSPAN", "ROWSPAN") if el.get(a))
        badge = ""
        if code:
            badge = f'<span class="badge">{html.escape(code)}</span>'
        elif unit:
            v = el.get("AUNITVALUE")
            badge = f'<span class="badge unit">{html.escape(unit)}{"=" + html.escape(v) if v else ""}</span>'
        cell = "th" if tag == "TH" else "td"
        return f"<{cell}{attrs}>{badge}{inner}</{cell}>{tail}"
    if tag == "TABLE":
        return f"<table>{inner}</table>{tail}"
    if tag in ("TBODY", "THEAD", "TR"):
        return f"<{tag.lower()}>{inner}</{tag.lower()}>{tail}"
    if tag == "P":
        cls = "" if in_coded else ' class="free"'
        return f"<p{cls}>{inner}</p>{tail}"
    if tag == "TITLE":
        return f"<h3>{inner}</h3>{tail}"
    if tag == "PGBRK":
        return f"<hr>{tail}"
    if tag == "CORRECTION":
        return f'<div class="correction"><div class="tag">정정 신고 부분 (&lt;CORRECTION&gt;)</div>{inner}</div>{tail}'
    if tag in _BLOCK:
        return f"<div>{inner}</div>{tail}"
    return f"<span>{inner}</span>{tail}"


def render_html(xml_path: Path, title: str, meta: dict[str, str], back_link: str | None = None) -> str:
    root = read_root(xml_path)
    meta_html = " · ".join(f"{html.escape(k)}: {html.escape(str(v))}" for k, v in meta.items())
    back = f'<a href="{html.escape(back_link)}">← 목록</a> · ' if back_link else ""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body><main>
<header>{back}{meta_html}
<div class="legend"><span><span class="badge">ACODE</span>값 칸</span><span><span class="badge unit">AUNIT=값</span>선택 칸</span>
<span><p class="free" style="display:inline;padding:0 6px">자유 서술</p></span></div></header>
{_render(root, False)}
</main></body></html>"""
