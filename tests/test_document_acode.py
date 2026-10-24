"""원문 받기(이어받기·본문 파일 고르기·020 멈춤)와 범용 ACODE 읽기. 가짜 응답과 케이에스 정답 XML 사용."""

import io
import zipfile

import httpx

from conftest import FIXTURES_DIR
from filingscope.collect.dart_client import DartClient
from filingscope.collect.document import choose_main_file, fetch_documents
from filingscope.parse.acode import read_document

RIGHTS = FIXTURES_DIR / "ks_rights_issue_20261015000184.xml"
CB_CORR = FIXTURES_DIR / "ks_cb19_correction_20260930000448.xml"
ATTACH = '<?xml version="1.0" encoding="utf-8"?><DOCUMENT><DOCUMENT-NAME ACODE="00760">첨부</DOCUMENT-NAME></DOCUMENT>'.encode()


# ---------- ACODE 읽기 ----------

def test_rights_issue_cells():
    doc = read_document(RIGHTS)
    assert doc.doc_code == "11306" and doc.formula_version == "6.4" and not doc.has_correction
    ci = doc.first("CI_MTH")
    assert ci.kind == "AUNIT" and ci.value == "6" and ci.text == "제3자배정증자"
    assert doc.first("CST_ISS_VAL").text == "1,199"
    assert [c.text for c in doc.body_cells() if c.code == "DC_RATE"] == ["-9.95", "-9.95"]
    assert doc.first("PIN_EXT_YN").value == "N"


def test_rule_cell_text_is_not_free_text():
    """정관 근거(RULE) 칸 안의 '출자전환'은 자유 서술로 잡히면 안 된다."""
    doc = read_document(RIGHTS)
    assert "출자전환" in doc.first("RULE").text
    assert not any("출자전환" in p.text for p in doc.body_paragraphs())


def test_correction_block_separated_from_body():
    doc = read_document(CB_CORR)
    assert doc.has_correction
    assert not any(c.in_correction for c in doc.cells)  # 정정 신고 부분에는 코드 칸이 없다
    assert doc.first("EXE_PRC").text == "1,623"  # 본문 값 (정정 후)


# ---------- 원문 받기 ----------

def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in files.items():
            zf.writestr(n, d)
    return buf.getvalue()


def fake_client(responses: dict, calls: list) -> DartClient:
    def handler(req):
        no = req.url.params["rcept_no"]
        calls.append(no)
        v = responses[no]
        if isinstance(v, bytes):
            return httpx.Response(200, content=v)
        return httpx.Response(200, text=f"<result><status>{v}</status><message>m{v}</message></result>")
    http = httpx.Client(base_url="https://t", transport=httpx.MockTransport(handler))
    return DartClient("SECRET", http=http, max_retries=0, backoff=0, sleep=lambda s: None)


def run(client, nos, tmp_path, done=()):
    saved, logs = [], []
    s = fetch_documents(client, nos, tmp_path, set(done), saved.append, lambda **r: logs.append(r), "t",
                        pause=0, sleep=lambda s: None)
    return s, saved, logs


def test_fetch_extracts_all_files_and_picks_main(tmp_path):
    no = "20261015000184"
    z = make_zip({f"{no}_00760.xml": ATTACH, f"{no}.xml": RIGHTS.read_bytes()})
    calls = []
    s, saved, logs = run(fake_client({no: z, "20260101000001": "013"}, calls), [no, "20260101000001", "X"],
                         tmp_path, done={"X"})
    assert calls == [no, "20260101000001"] and s.skipped == 1
    info = saved[0]
    assert info.main_file == f"{no}.xml" and info.doc_code == "11306" and info.formula_version == "6.4"
    assert {f["name"] for f in info.files} == {f"{no}.xml", f"{no}_00760.xml"}
    assert (tmp_path / no / f"{no}_00760.xml").exists()
    assert [l["ok"] for l in logs] == [True, False] and logs[1]["status"] == "013"
    assert logs[0]["rcept_no"] == no


def test_fetch_stops_on_daily_limit(tmp_path):
    calls = []
    s, _, _ = run(fake_client({"A": "020", "B": "000"}, calls), ["A", "B"], tmp_path)
    assert s.stopped_on_limit and calls == ["A"]


def test_choose_main_file_without_rcept_name():
    files = [{"name": "a.xml", "doc_code": "00760", "doc_name": "첨부"},
             {"name": "b.xml", "doc_code": "11306", "doc_name": "주요사항보고서(유상증자결정)"}]
    assert choose_main_file("NOPE", files) == "b.xml"


def test_body_section_nested_inside_correction():
    """본문 섹션이 <CORRECTION> 안에 들어간 정정본(실제 2026년 4건): 섹션 안 칸은 본문, 섹션 밖 칸만 정정 신고."""
    xml = """<?xml version="1.0" encoding="utf-8"?><DOCUMENT>
    <DOCUMENT-NAME ACODE="11306">주요사항보고서(유상증자결정)</DOCUMENT-NAME>
    <BODY><LIBRARY><CORRECTION><TITLE>정 정 신 고 (보고)</TITLE>
      <TABLE><TBODY><TR><TD>정정 전</TD><TE ACODE="OLD_ONLY">1</TE></TR></TBODY></TABLE>
      <SECTION-1><TITLE>유상증자 결정</TITLE>
        <TABLE><TBODY><TR><TD>보통주식 (원)</TD><TE ACODE="CST_ISS_VAL">5,000</TE></TR></TBODY></TABLE>
      </SECTION-1></CORRECTION></LIBRARY></BODY></DOCUMENT>""".encode()
    doc = read_document(xml)
    assert doc.has_correction
    assert doc.first("CST_ISS_VAL").text == "5,000" and doc.first("CST_ISS_VAL").label == "보통주식 (원)"
    assert [c.code for c in doc.cells if c.in_correction] == ["OLD_ONLY"]


def test_row_index_is_stable():
    """같은 줄의 칸은 같은 row_idx (대상자 표에서 줄 단위로 묶기 위해)."""
    doc = read_document(RIGHTS)
    part, cnt = doc.first("PART"), doc.first("ALL_CNT")
    assert part.row_idx >= 0 and (part.table_idx, part.row_idx) == (cnt.table_idx, cnt.row_idx)
    assert doc.first("CST_CNT").row_idx >= 0


# ---------- 본문 글의 꺾쇠괄호 ----------

ANGLE = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT><DOCUMENT-NAME ACODE="11306">주요사항보고서(유상증자결정)</DOCUMENT-NAME><BODY><SECTION-1>
<TABLE><TBODY><TR>
<TE ACODE="PART">(주)위허브</TE><TE ACODE="MNTH">&lt;증자결정 전&gt;-비품매입 : 2백만원<증자결정 후>-임대수입</TE>
<TE ACODE="ALL_CNT">1,009,591</TE><TE ACODE="ETC">1년 <Note> 보호예수 <Better than</TE>
</TR></TBODY></TABLE>
<P>정관 <개정 2018.3.2> 제10조 <A사> 참조</P>
</SECTION-1></BODY></DOCUMENT>""".encode()


def test_angle_brackets_in_text_do_not_break_rows():
    """'<증자결정 후>' 같은 본문 글자 때문에 대상자 줄이 깨지면 안 된다 (포커스에이아이 20250704000281)."""
    doc = read_document(ANGLE)
    part, cnt = doc.first("PART"), doc.first("ALL_CNT")
    assert cnt.text == "1,009,591" and cnt.row_idx == part.row_idx >= 0
    assert "<증자결정 후>" in doc.first("MNTH").text
    assert "<Note>" in doc.first("ETC").text and "<Better than" in doc.first("ETC").text
    assert any("<개정 2018.3.2>" in p.text and "<A사>" in p.text for p in doc.body_paragraphs())


def test_real_tags_untouched():
    from filingscope.parse.acode import escape_non_tags
    raw = b'<?xml version="1.0"?><!-- c --><TABLE-GROUP><TABLE><TR><TD>x</TD></TR></TABLE></TABLE-GROUP><CORRECTION/><A HREF="y">z</A>'
    assert escape_non_tags(raw) == raw
