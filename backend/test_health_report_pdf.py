"""Regression checks for health report PDF export."""

import asyncio

from app.api import health_report_pdf
from reportlab.pdfbase import pdfmetrics


def test_health_report_pdf_registers_chinese_capable_font() -> None:
    health_report_pdf._font_registered = False
    health_report_pdf._font_name = "Helvetica"
    health_report_pdf._font_bold = "Helvetica-Bold"

    health_report_pdf._register_font()

    assert health_report_pdf._font_name != "Helvetica"
    assert health_report_pdf._font_bold != "Helvetica-Bold"
    assert _font_has_text(health_report_pdf._font_name, "OpsGuard 系统健康巡检报告 123")
    assert _font_has_text(health_report_pdf._font_bold, "OpsGuard 系统健康巡检报告 123")


def test_health_report_pdf_endpoint_returns_pdf() -> None:
    response = asyncio.run(health_report_pdf.export_health_report_pdf())

    body = response.body_iterator
    first_chunk = asyncio.run(body.__anext__())

    assert response.media_type == "application/pdf"
    assert first_chunk.startswith(b"%PDF")


def _font_has_text(font_name: str, text: str) -> bool:
    font = pdfmetrics.getFont(font_name)
    char_to_glyph = font.face.charToGlyph
    return all(ord(char) in char_to_glyph for char in text if not char.isspace())
