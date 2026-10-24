import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXPECTED_PATH = FIXTURES_DIR / "expected_values.json"


def fixture_xmls() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.xml"))


@pytest.fixture(scope="session")
def expected_values() -> dict:
    if not EXPECTED_PATH.exists():
        pytest.skip("tests/fixtures/expected_values.json 없음 (사용자가 추가 예정)")
    return json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
