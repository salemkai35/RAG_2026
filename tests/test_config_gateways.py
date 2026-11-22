import pytest

from contract_rag.config import Settings, get_settings
from contract_rag.gateways.http import ModelEndpointError, ensure_configured, host_is_allowed
from contract_rag.gateways.reranker import build_rerank_request, parse_rerank_response


def test_endpoints_fall_back_to_global_gateway(monkeypatch):
    monkeypatch.setenv("CRAG_AI_BASE_URL", "http://gateway.interne.local/v1/")
    monkeypatch.setenv("CRAG_AI_API_KEY", "k")
    monkeypatch.setenv("CRAG_EMBEDDING_BASE_URL", "http://emb.interne.local/v1")
    s = get_settings()
    assert s.llm.base_url == "http://gateway.interne.local/v1"
    assert s.llm.api_key == "k"
    assert s.embedding.base_url == "http://emb.interne.local/v1"
    assert s.reranker.base_url == "http://gateway.interne.local/v1"


def test_invalid_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("CRAG_OCR_ENGINE", "cloud")
    with pytest.raises(ValueError, match="CRAG_OCR_ENGINE"):
        Settings.from_env()


def test_dimension_limit(monkeypatch):
    monkeypatch.setenv("CRAG_EMBEDDING_DIM", "3072")
    with pytest.raises(ValueError, match="2000"):
        Settings.from_env()


@pytest.mark.parametrize(
    "url,allowed,expected",
    [
        ("http://llm.interne.local:8000/v1", (".interne.local",), True),
        ("http://interne.local/v1", (".interne.local",), True),
        ("https://api.openai.com/v1", (".interne.local",), False),
        ("http://10.20.1.5:8000/v1", ("10.20.1.5",), True),
        ("http://evil-interne.local.example.com", (".interne.local",), False),
        ("https://api.openai.com/v1", (), True),
    ],
)
def test_host_allowlist(url, allowed, expected):
    assert host_is_allowed(url, allowed) is expected


def test_public_endpoint_refused(monkeypatch):
    monkeypatch.setenv("CRAG_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("CRAG_ALLOWED_HOSTS", ".interne.local")
    s = get_settings()
    with pytest.raises(ModelEndpointError, match="refusé"):
        ensure_configured(s.llm, s)


def test_rerank_formats():
    assert build_rerank_request("tei", "m", "q", ["a"], 3) == {
        "query": "q", "texts": ["a"], "raw_scores": False, "truncate": True}
    assert build_rerank_request("cohere", "m", "q", ["a"], 3)["model"] == "m"
    cohere = {"results": [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}]}
    tei = [{"index": 0, "score": 0.2}, {"index": 1, "score": 0.7}]
    assert parse_rerank_response(cohere) == [(1, 0.9), (0, 0.1)]
    assert parse_rerank_response(tei) == [(1, 0.7), (0, 0.2)]
    with pytest.raises(ModelEndpointError):
        parse_rerank_response({"unexpected": True})
