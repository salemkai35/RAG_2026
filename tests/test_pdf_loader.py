from pathlib import Path

import pytest

pytest.importorskip("pdfplumber")
pytest.importorskip("pypdfium2")
reportlab = pytest.importorskip("reportlab")

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from contract_rag.config import get_settings  # noqa: E402
from contract_rag.gateways.ocr import OcrResult  # noqa: E402
from contract_rag.ingestion.loaders.pdf import parse_pdf  # noqa: E402
from contract_rag.ingestion.structure import build_chunks  # noqa: E402


class FakeOcr:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        assert image.size[0] > 1000  # rendu à 300 dpi
        return OcrResult("ARTICLE 9 : GARANTIES\nCaution de bonne exécution de 5 %.", 0.42, "fake")


def _make_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    for line in ["CONTRAT DE TRAVAUX", "ARTICLE 1 : OBJET", "Le présent contrat a pour objet la réhabilitation",
                 "du bloc pédagogique B de l'institut."]:
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    # Page 2 : simulée « numérisée » (aucun texte)
    c.rect(72, 600, 300, 100)
    c.showPage()
    c.drawString(72, 800, "ARTICLE 12 : PENALITES DE RETARD")
    c.drawString(72, 782, "Une penalite de 1/1000 du montant par jour de retard.")
    c.showPage()
    c.save()


def test_pdf_text_ocr_and_traceability(tmp_path: Path):
    pdf = tmp_path / "contrat.pdf"
    _make_pdf(pdf)
    ocr = FakeOcr()
    parsed = parse_pdf(pdf, get_settings(), ocr)

    assert parsed.page_count == 3
    assert [p.method for p in parsed.pages] == ["texte", "ocr:fake", "texte"]
    assert ocr.calls == 1
    assert any("faible confiance" in w for w in parsed.warnings)

    chunks = build_chunks(parsed.blocks)
    by_title = {c.section_titre: c for c in chunks}
    assert by_title["ARTICLE 1 : OBJET"].page_debut == 1
    assert by_title["ARTICLE 9 : GARANTIES"].page_debut == 2
    assert by_title["ARTICLE 12 : PENALITES DE RETARD"].page_debut == 3
    assert "1/1000" in by_title["ARTICLE 12 : PENALITES DE RETARD"].contenu


def test_pdf_without_ocr_warns(tmp_path: Path):
    pdf = tmp_path / "scan.pdf"
    _make_pdf(pdf)
    parsed = parse_pdf(pdf, get_settings(), None)
    assert parsed.pages[1].method == "vide"
    assert any("sans texte" in w for w in parsed.warnings)
