"""LLM 정답표 만들기: 이미 있는 제목은 다시 부르지 않고, 기존 줄은 덮어쓰지 않는다. 가짜 클라이언트 사용."""

from types import SimpleNamespace

from filingscope.classify.answer_sheet import load_answers
from filingscope.llm.title_labeler import (
    TitleLabel, build_answer_sheet, label_to_row, load_answer_sheet, write_answer_sheet,
)


class FakeClient:
    def __init__(self, labels: dict[str, TitleLabel], fail: set[str] = frozenset()):
        self.calls = []
        self._labels, self._fail = labels, fail
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        title = kw["messages"][0]["content"].removeprefix("제목: ")
        self.calls.append(title)
        assert "classify" not in kw["system"]  # 코드 판정은 보내지 않는다
        if title in self._fail:
            raise RuntimeError("boom")
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self._labels[title],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10))


def lbl(event_type, is_corr=False, tag="", mixed=False):
    return TitleLabel(event_type=event_type, is_correction=is_corr, report_tag=tag, is_mixed=mixed, reason="r")


def test_only_new_titles_are_labeled_and_existing_rows_kept(tmp_path):
    path = tmp_path / "title_cases.csv"
    user_row = label_to_row("A제목", lbl("cb")) | {"reason": "사용자 결정", "source": "user"}
    write_answer_sheet(path, {"A제목": user_row})

    client = FakeClient({"B제목": lbl("rights_issue", True, "기재정정"), "C제목": lbl("unsure")},
                        fail={"D제목"})
    run = build_answer_sheet(client, ["A제목", "B제목", "C제목", "D제목"], path, workers=2)

    assert sorted(client.calls) == ["B제목", "C제목", "D제목"]  # A는 다시 부르지 않음
    assert set(run.labeled) == {"B제목", "C제목"} and set(run.failed) == {"D제목"}
    assert run.input_tokens == 200

    rows = load_answer_sheet(path)
    assert rows["A제목"]["source"] == "user"  # 덮어쓰지 않음
    assert "D제목" not in rows  # 실패는 저장하지 않음 → 다음 실행에 다시 시도

    answers = {a.report_nm: a for a in load_answers(path)}
    assert answers["B제목"].as_tuple() == ("rights_issue", True, "기재정정", False)
    assert answers["C제목"].is_unsure


def test_none_label_reads_back_as_none(tmp_path):
    path = tmp_path / "t.csv"
    write_answer_sheet(path, {"X": label_to_row("X", lbl("none"))})
    assert load_answers(path)[0].as_tuple() == (None, False, None, False)
