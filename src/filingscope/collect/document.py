"""원문(document.xml) ZIP 받기 → data/raw/document/{rcept_no}/ 압축 해제 → 본문 파일 고르기. 공시 종류 무관.

이미 documents 표에 있는 접수번호는 건너뛰고, 호출마다 collect_log에 기록한다.
"""

import io
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from filingscope.collect.dart_client import DartClient
from filingscope.parse.acode import doc_header, read_root

LIMIT_EXCEEDED = "020"


@dataclass
class DocumentInfo:
    rcept_no: str
    main_file: str | None
    files: list[dict]  # [{name, doc_code, doc_name}]
    doc_code: str | None
    doc_name: str | None
    formula_version: str | None
    formula_adate: object


def extract_zip(rcept_no: str, content: bytes, out_dir: Path) -> DocumentInfo:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in zf.namelist():
            data = zf.read(name)
            safe = Path(name).name  # ZIP 안 경로는 버리고 파일 이름만 (경로 탈출 방지)
            (out_dir / safe).write_bytes(data)
            info = {"name": safe, "doc_code": None, "doc_name": None}
            if safe.lower().endswith(".xml"):
                try:
                    code, dname, *_ = doc_header(read_root(data))
                    info.update(doc_code=code, doc_name=dname)
                except Exception:
                    pass
            files.append(info)
    main = choose_main_file(rcept_no, files)
    header = (None,) * 5
    if main:
        header = doc_header(read_root(out_dir / main))
    return DocumentInfo(rcept_no, main, files, header[0], header[1], header[2], header[3])


def choose_main_file(rcept_no: str, files: list[dict]) -> str | None:
    """본문: {rcept_no}.xml이 있으면 그것, 없으면 '주요사항보고서'가 들어간 첫 XML, 없으면 첫 XML."""
    xmls = [f for f in files if f["name"].lower().endswith(".xml")]
    for f in xmls:
        if f["name"] == f"{rcept_no}.xml":
            return f["name"]
    for f in xmls:
        if "주요사항보고서" in (f["doc_name"] or ""):
            return f["name"]
    return xmls[0]["name"] if xmls else None


@dataclass
class FetchSummary:
    calls: int = 0
    skipped: int = 0
    saved: int = 0
    failed: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    stopped_on_limit: bool = False


def fetch_documents(
    client: DartClient,
    rcept_nos: Iterable[str],
    raw_dir: Path,
    done: set[str],
    save_info: Callable[[DocumentInfo], None],
    log_call: Callable[..., None],
    run_id: str,
    pause: float = 0.2,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchSummary:
    s = FetchSummary()
    for rcept_no in rcept_nos:
        if rcept_no in done:
            s.skipped += 1
            continue
        call = client.get_zip("document.xml", {"rcept_no": rcept_no})
        s.calls += 1
        ok = call.ok and call.content is not None  # 013(문서 없음)은 성공이 아님
        status, message = call.status, call.message
        info = None
        if ok:
            try:
                info = extract_zip(rcept_no, call.content, raw_dir / rcept_no)
            except (zipfile.BadZipFile, OSError) as e:
                ok, status, message = False, "BAD_ZIP", str(e)
        log_call(run_id=run_id, endpoint="document.xml", rcept_no=rcept_no, ok=ok, status=status,
                 message=message, attempts=call.attempts,
                 raw_path=str(raw_dir / rcept_no) if ok else None)
        if call.status == LIMIT_EXCEEDED:
            s.failed.append((rcept_no, status, message))
            s.stopped_on_limit = True
            return s
        if not ok:
            s.failed.append((rcept_no, status, message))
        else:
            save_info(info)
            s.saved += 1
        sleep(pause)
    return s
