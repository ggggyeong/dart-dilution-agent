"""정답 데이터(fixtures) 자체가 쓸 수 있는 상태인지 확인한다."""

import pytest
from lxml import etree

from conftest import fixture_xmls


def test_fixture_xmls_present():
    xmls = fixture_xmls()
    if not xmls:
        pytest.skip("tests/fixtures/*.xml 없음 (사용자가 추가 예정)")
    assert len(xmls) >= 3


def test_expected_values_not_empty(expected_values):
    assert expected_values


@pytest.mark.parametrize("xml_path", fixture_xmls(), ids=lambda p: p.name)
def test_fixture_xml_parses(xml_path):
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.parse(str(xml_path), parser).getroot()
    assert root is not None
    assert root.find(".//DOCUMENT-NAME") is not None
