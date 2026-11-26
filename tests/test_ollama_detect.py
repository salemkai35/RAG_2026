"""Détection automatique des modèles Ollama (scripts/ollama-detect.py)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ollama_detect", Path(__file__).resolve().parents[1] / "scripts" / "ollama-detect.py")
detect = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(detect)


def test_site_topology_qwen_ocria():
    models = [
        {"name": "qwen3-embedding:8b", "size": 4_700_000_000},
        {"name": "ocria-1.5:4b", "size": 3_300_000_000},
        {"name": "qwen3.6:27b-q4_K_M", "size": 17_000_000_000},
        {"name": "llama3.2:3b", "size": 2_000_000_000},
    ]
    assert detect.choose_models(models) == {
        "LLM_MODEL": "qwen3.6:27b-q4_K_M",
        "EMBEDDING_MODEL": "qwen3-embedding:8b",
        "VISION_MODEL": "ocria-1.5:4b",
    }


def test_wsl_test_server_without_ocr():
    models = [{"name": "nomic-embed-text:latest", "size": 274_000_000},
              {"name": "mistral:7b-instruct", "size": 4_100_000_000}]
    chosen = detect.choose_models(models)
    assert chosen == {"LLM_MODEL": "mistral:7b-instruct", "EMBEDDING_MODEL": "nomic-embed-text:latest",
                      "VISION_MODEL": ""}


def test_vision_model_fallback_on_vl_name():
    chosen = detect.choose_models([{"name": "qwen2.5vl:7b"}, {"name": "qwen2.5-vl:7b"},
                                   {"name": "bge-m3-embed"}, {"name": "qwen3:14b", "size": 9}])
    assert chosen["VISION_MODEL"].startswith("qwen2.5") and chosen["LLM_MODEL"] == "qwen3:14b"


def test_large_embedding_reduced_to_1024():
    out = detect.embedding_settings("qwen3-embedding:8b", 4096)
    assert out["EMBEDDING_DIM"] == "1024"
    assert out["EMBEDDING_REQUEST_DIMENSIONS"] == "true" and out["EMBEDDING_TRUNCATE"] == "true"
    assert out["OWUI_BYPASS_EMBEDDING"] == "true"
    assert out["EMBEDDING_QUERY_PREFIX"].startswith('"Instruct: ') and r"\nQuery: " in out["EMBEDDING_QUERY_PREFIX"]
    assert out["EMBEDDING_DOCUMENT_PREFIX"] == ""


def test_small_embedding_kept_native():
    out = detect.embedding_settings("nomic-embed-text:latest", 768)
    assert out["EMBEDDING_DIM"] == "768" and out["EMBEDDING_REQUEST_DIMENSIONS"] == "false"
    assert out["EMBEDDING_QUERY_PREFIX"] == '"search_query: "'
    assert "OWUI_BYPASS_EMBEDDING" not in out
