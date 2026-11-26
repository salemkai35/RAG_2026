"""Moteurs OCR interchangeables : Tesseract (local) ou modèle Vision interne."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Protocol

from ..config import Settings, get_settings
from .llm import LLMClient

VISION_PROMPT = (
    "Tu es un moteur OCR. Transcris intégralement et fidèlement le texte de cette page "
    "de document contractuel, dans l'ordre de lecture. Conserve les numéros d'articles, "
    "les montants, les dates et les références exactement comme écrits. Restitue les "
    "tableaux en Markdown. N'ajoute aucun commentaire, n'invente rien ; écris [illisible] "
    "pour toute partie que tu ne peux pas lire."
)


@dataclass
class OcrResult:
    text: str
    confidence: float | None
    engine: str


class OcrEngine(Protocol):
    name: str

    def __call__(self, image) -> OcrResult: ...


class TesseractOcr:
    name = "tesseract"

    def __init__(self, languages: str = "fra+eng"):
        self.languages = languages

    def __call__(self, image) -> OcrResult:
        import pytesseract

        data = pytesseract.image_to_data(
            image, lang=self.languages, output_type=pytesseract.Output.DICT, config="--psm 3"
        )
        lines: dict[tuple[int, int, int], list[str]] = {}
        confidences: list[float] = []
        for i, word in enumerate(data["text"]):
            word = (word or "").strip()
            if not word:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(word)
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            if conf >= 0:
                confidences.append(conf)
        text_lines = []
        previous_par = None
        for key in sorted(lines):
            par = key[:2]
            if previous_par is not None and par != previous_par:
                text_lines.append("")
            text_lines.append(" ".join(lines[key]))
            previous_par = par
        confidence = round(sum(confidences) / len(confidences) / 100, 3) if confidences else None
        return OcrResult(text="\n".join(text_lines), confidence=confidence, engine=self.name)


class VisionOcr:
    name = "vision"

    def __init__(self, settings: Settings):
        self.llm = LLMClient(settings, endpoint=settings.vision)
        self.max_side = settings.vision_max_side

    def __call__(self, image) -> OcrResult:
        # Une page A4 rendue à 300 dpi fait ~2500×3500 px : trop de jetons image pour un
        # petit modèle Vision (OCRia, Qwen-VL…). On réduit en gardant les proportions.
        if self.max_side and max(image.size) > self.max_side:
            image = image.copy()
            image.thumbnail((self.max_side, self.max_side))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        text = self.llm.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            temperature=0.0,
        )
        return OcrResult(text=text, confidence=None, engine=self.name)


def get_ocr_engine(settings: Settings | None = None) -> OcrEngine | None:
    settings = settings or get_settings()
    if settings.ocr_engine == "tesseract":
        return TesseractOcr(settings.ocr_languages)
    if settings.ocr_engine == "vision":
        return VisionOcr(settings)
    return None
