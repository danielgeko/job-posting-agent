import json
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent


@pytest.fixture
def load_fixture():
    return lambda name: json.loads((FIXTURES / name).read_text())


@pytest.fixture
def preferences():
    return yaml.safe_load((ROOT / "profile" / "preferences.yaml").read_text())
