import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    from app import config, db

    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init_db(path)
    return path
