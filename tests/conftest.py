import pytest

from contract_rag.config import reset_settings


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("CRAG_"):
            monkeypatch.delenv(key, raising=False)
    reset_settings()
    yield
    reset_settings()
