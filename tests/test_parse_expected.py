"""파서 결과가 정답(expected_values.json)과 일치하는지 — 파서 구현 전까지 skip."""

import pytest

from conftest import fixture_xmls


@pytest.mark.skip(reason="parse 모듈 구현 전")
@pytest.mark.parametrize("xml_path", fixture_xmls(), ids=lambda p: p.name)
def test_extracted_values_match_expected(xml_path, expected_values):
    # TODO: filingscope.parse 구현 후
    #   extracted = parse_filing(xml_path)
    #   assert extracted == expected_values[<이 XML의 키>]
    ...
