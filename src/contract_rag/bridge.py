"""Passerelle d'extraction documentaire pour Open WebUI (moteur « External »).

Pourquoi : Ollama (et donc OCRia) n'accepte que des *images*. Quand un utilisateur dépose
un PDF scanné ou une photo dans le chat, Open WebUI ne sait pas, seul, rendre les pages
en images ni appeler un modèle Vision. Cette passerelle le fait, en réutilisant
exactement les lecteurs de l'ingestion des contrats (pdfplumber + rendu pypdfium2 + OCR).

Contrat Open WebUI v0.13 (retrieval/loaders/external_document.py) :
    PUT {url}/process   corps = octets du fichier
    en-têtes : Content-Type, X-Filename (encodé URL), Authorization: Bearer <clé>
    réponse  : [{"page_content": "...", "metadata": {...}}, ...]

Volontairement sans framework (bibliothèque standard uniquement) : un seul point d'entrée,
pas de dépendance nouvelle, ~10 Mo de RAM en plus du processus Python.
Le service n'est publié sur aucun port de l'hôte : seul Open WebUI le joint, par le réseau
Docker interne. Lancement : ``contract-rag ocr-bridge`` (service ``ocr-bridge`` du compose).
"""

from __future__ import annotations

import hmac
import json
import logging
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from .config import Settings, get_settings
from .gateways.ocr import get_ocr_engine
from .ingestion.loaders import UnsupportedFormat, parse_file
from .ingestion.types import Block, ParsedDocument

log = logging.getLogger("contract_rag.bridge")

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xlsm", ".doc", ".xls"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".log", ".yaml", ".yml"}
MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tif",
    "image/webp": ".webp",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}


class BridgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _block_text(block: Block) -> str:
    if block.kind == "table" and block.rows:
        lines = [" | ".join(cell.replace("\n", " ") for cell in row) for row in block.rows]
        return "\n".join(lines)
    if block.kind == "heading":
        return f"## {block.text.strip()}"
    return block.text or ""


def documents_from_parsed(parsed: ParsedDocument, filename: str) -> list[dict]:
    """Un document Open WebUI par page (PDF), ou un seul document (Word, Excel)."""
    methods = {p.number: p.method for p in parsed.pages}
    groups: dict[int | None, list[str]] = {}
    for block in parsed.blocks:
        text = _block_text(block).strip()
        if text:
            groups.setdefault(block.page, []).append(text)
    documents = []
    for page in sorted(groups, key=lambda p: (p is None, p or 0)):
        metadata: dict = {"source": filename, "extraction": "contract_rag"}
        if page is not None:
            # Open WebUI numérote les pages à partir de 0 (comme son lecteur PDF natif).
            metadata.update(page=page - 1, page_label=str(page), method=methods.get(page, "texte"))
        documents.append({"page_content": "\n\n".join(groups[page]), "metadata": metadata})
    if parsed.warnings and documents:
        documents[0]["metadata"]["warnings"] = " | ".join(parsed.warnings)[:1000]
    return documents


def ocr_image_file(path: Path, settings: Settings, filename: str) -> list[dict]:
    from PIL import Image, ImageSequence

    engine = get_ocr_engine(settings)
    if engine is None:
        raise BridgeError(422, "Image reçue mais OCR désactivé (OCR_ENGINE=none).")
    documents = []
    with Image.open(path) as image:
        for index, frame in enumerate(ImageSequence.Iterator(image), start=1):
            result = engine(frame.convert("RGB"))
            if result.text.strip():
                documents.append({
                    "page_content": result.text,
                    "metadata": {"source": filename, "page": index - 1, "page_label": str(index),
                                 "method": f"ocr:{result.engine}", "extraction": "contract_rag"},
                })
    return documents


def extract(data: bytes, filename: str, content_type: str | None, settings: Settings) -> list[dict]:
    ext = Path(filename).suffix.lower()
    if not ext and content_type:
        ext = MIME_EXTENSIONS.get(content_type.split(";")[0].strip().lower(), "")
    if ext in TEXT_EXTENSIONS or (not ext and (content_type or "").startswith("text/")):
        text = data.decode("utf-8", errors="replace")
        return [{"page_content": text, "metadata": {"source": filename, "extraction": "texte"}}]
    if ext not in DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS:
        raise BridgeError(415, f"Format non pris en charge par la passerelle : {ext or content_type}.")
    with tempfile.TemporaryDirectory(prefix="crag-bridge-") as tmp:
        path = Path(tmp) / f"document{ext}"
        path.write_bytes(data)
        if ext in IMAGE_EXTENSIONS:
            return ocr_image_file(path, settings, filename)
        try:
            parsed = parse_file(path, settings, get_ocr_engine(settings))
        except UnsupportedFormat as exc:
            raise BridgeError(415, str(exc)) from exc
        return documents_from_parsed(parsed, filename)


def make_handler(settings: Settings):
    expected = (settings.bridge_api_key or "").encode()
    max_bytes = settings.bridge_max_mb * 1024 * 1024

    class Handler(BaseHTTPRequestHandler):
        server_version = "contract-rag-bridge"
        protocol_version = "HTTP/1.1"

        def _reply(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # journal standard de http.server → logging
            log.debug("%s - %s", self.address_string(), fmt % args)

        def do_GET(self):
            if self.path.rstrip("/") in ("/health", ""):
                self._reply(200, {"status": "ok", "ocr": settings.ocr_engine,
                                  "vision_model": settings.vision.model})
            else:
                self._reply(404, {"detail": "introuvable"})

        def do_PUT(self):
            started = time.monotonic()
            if self.path.rstrip("/") != "/process":
                return self._reply(404, {"detail": "introuvable"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip().encode()
            if not expected or not hmac.compare_digest(token, expected):
                # Consommer le corps (borné) avant de répondre : sinon le client, encore en train
                # d'envoyer, reçoit une remise à zéro de la connexion au lieu du 401.
                if 0 < length <= max_bytes:
                    self.rfile.read(length)
                self.close_connection = True
                return self._reply(401, {"detail": "clé de la passerelle invalide"})
            if length <= 0:
                return self._reply(400, {"detail": "corps vide"})
            if length > max_bytes:
                return self._reply(413, {"detail": f"fichier > {settings.bridge_max_mb} Mo"})
            data = self.rfile.read(length)
            filename = unquote(self.headers.get("X-Filename") or "document")
            try:
                documents = extract(data, filename, self.headers.get("Content-Type"), settings)
            except BridgeError as exc:
                log.warning("Refus (%s) : %s", exc.status, exc)
                return self._reply(exc.status, {"detail": str(exc)})
            except Exception as exc:  # lecteur ou OCR en échec : message lisible pour l'utilisateur
                log.exception("Extraction impossible")
                return self._reply(500, {"detail": f"Extraction impossible : {exc}"})
            if not documents:
                return self._reply(422, {"detail": "aucun texte extrait"})
            log.info("%d page(s) / %d car. extraites en %.1f s (%s)", len(documents),
                     sum(len(d["page_content"]) for d in documents), time.monotonic() - started,
                     Path(filename).suffix.lower() or "?")
            self._reply(200, documents)

    return Handler


def serve(host: str = "0.0.0.0", port: int = 8090, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.bridge_api_key:
        raise SystemExit("CRAG_BRIDGE_API_KEY doit être défini (clé partagée avec Open WebUI).")
    server = ThreadingHTTPServer((host, port), make_handler(settings))
    log.info("Passerelle d'extraction à l'écoute sur %s:%s (OCR : %s %s)", host, port,
             settings.ocr_engine, settings.vision.model or "")
    try:
        server.serve_forever()
    finally:
        server.server_close()
