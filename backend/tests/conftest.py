import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient against a freshly-seeded, throwaway database."""
    working_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "WORKING_DB", working_db)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    shutil.rmtree(tmp_path, ignore_errors=True)
