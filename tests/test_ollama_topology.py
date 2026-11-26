"""Adaptations « serveur Ollama distant » : embeddings Matryoshka, Vision, passerelle OCR."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from contract_rag.config import get_settings
from contract_rag.gateways import embeddings as emb
from contract_rag.gateways.embeddings import EmbeddingClient, truncate_normalize


def test_truncate_normalize_keeps_unit_norm():
    vec = [3.0, 4.0, 12.0, 84.0]
    out = truncate_normalize(vec, 2)
    assert out == pytest.approx([0.6, 0.8])
    assert truncate_normalize([1.0, 0.0], 4) == [1.0, 0.0]  # déjà assez court : inchangé


def test_prefix_newline_escape(monkeypatch):
    monkeypatch.setenv("CRAG_EMBEDDING_QUERY_PREFIX", r"Instruct: retrouver les passages\nQuery: ")
    assert get_settings().embedding_query_prefix == "Instruct: retrouver les passages\nQuery: "


def _client(monkeypatch, **env):
    base = {"CRAG_EMBEDDING_BASE_URL": "http://172.21.36.55:11434/v1", "CRAG_EMBEDDING_MODEL": "qwen3-embedding:8b",
            "CRAG_EMBEDDING_DIM": "4"}
    for k, v in {**base, **env}.items():
        monkeypatch.setenv(k, v)
    captured = {}

    def fake_post(client, endpoint, path, payload, retries):
        captured["payload"] = payload
        size = payload.get("dimensions", 8)
        return {"data": [{"index": i, "embedding": [1.0] * size} for i, _ in enumerate(payload["input"])]}

    monkeypatch.setattr(emb, "post_json", fake_post)
    monkeypatch.setattr(emb, "build_client", lambda endpoint, settings: object())
    return EmbeddingClient(get_settings(), retries=1), captured


def test_dimensions_requested_from_server(monkeypatch):
    client, captured = _client(monkeypatch, CRAG_EMBEDDING_REQUEST_DIMENSIONS="true")
    assert len(client.embed_query("pénalités")) == 4
    assert captured["payload"]["dimensions"] == 4


def test_client_side_truncation_when_server_ignores_dimensions(monkeypatch):
    client, captured = _client(monkeypatch, CRAG_EMBEDDING_TRUNCATE="true")
    vec = client.embed_query("pénalités")
    assert "dimensions" not in captured["payload"]
    assert len(vec) == 4 and sum(x * x for x in vec) == pytest.approx(1.0)


def test_dimension_mismatch_still_detected(monkeypatch):
    client, _ = _client(monkeypatch)
    with pytest.raises(Exception, match="Dimension d'embedding 8"):
        client.embed_query("pénalités")


def test_vision_ocr_downscales_page(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    from contract_rag.gateways import ocr

    monkeypatch.setenv("CRAG_VISION_BASE_URL", "http://172.21.36.55:11434/v1")
    monkeypatch.setenv("CRAG_VISION_MODEL", "ocria")
    monkeypatch.setenv("CRAG_VISION_MAX_SIDE", "1000")
    seen = {}

    def fake_chat(self, messages, **kwargs):
        import base64
        import io

        uri = messages[0]["content"][1]["image_url"]["url"]
        img = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
        seen["size"] = img.size
        return "ARTICLE 1"

    monkeypatch.setattr(ocr.LLMClient, "chat", fake_chat)
    result = ocr.VisionOcr(get_settings())(Image.new("RGBA", (2480, 3508), "white"))
    assert result.text == "ARTICLE 1"
    assert max(seen["size"]) == 1000 and seen["size"][0] < seen["size"][1]


# --- Passerelle d'extraction (contrat Open WebUI « external ») --------------------------

@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv("CRAG_BRIDGE_API_KEY", "cle-de-test")
    monkeypatch.setenv("CRAG_OCR_ENGINE", "none")
    from contract_rag.bridge import make_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(get_settings()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _put(url, data, key="cle-de-test", filename="note%20de%20service.txt", ctype="text/plain"):
    req = urllib.request.Request(f"{url}/process", data=data, method="PUT", headers={
        "Authorization": f"Bearer {key}", "X-Filename": filename, "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def test_bridge_text_document(bridge):
    status, docs = _put(bridge, "Délai d'exécution : 240 jours".encode())
    assert status == 200
    assert docs[0]["page_content"] == "Délai d'exécution : 240 jours"
    assert docs[0]["metadata"]["source"] == "note de service.txt"


def test_bridge_rejects_bad_key(bridge):
    with pytest.raises(urllib.error.HTTPError) as err:
        _put(bridge, b"x", key="mauvaise")
    assert err.value.code == 401


def test_bridge_rejects_unknown_format(bridge):
    with pytest.raises(urllib.error.HTTPError) as err:
        _put(bridge, b"PK..", filename="presentation.pptx", ctype="application/octet-stream")
    assert err.value.code == 415


def test_bridge_health(bridge):
    with urllib.request.urlopen(f"{bridge}/health", timeout=5) as resp:
        assert json.loads(resp.read())["status"] == "ok"


def test_bridge_pdf_pages(bridge):
    pytest.importorskip("pdfplumber")
    pytest.importorskip("reportlab")
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 800, "ARTICLE 7 : PENALITES DE RETARD")
    c.drawString(72, 780, "Un millieme du montant du marche par jour de retard, plafonne a 10 %.")
    c.showPage()
    c.drawString(72, 800, "ARTICLE 8 : RECEPTION PROVISOIRE ET DEFINITIVE DES OUVRAGES")
    c.showPage()
    c.save()
    status, docs = _put(bridge, buf.getvalue(), filename="contrat.pdf", ctype="application/pdf")
    assert status == 200 and len(docs) == 2
    assert docs[0]["metadata"]["page"] == 0 and docs[1]["metadata"]["page_label"] == "2"
    assert "PENALITES" in docs[0]["page_content"]
