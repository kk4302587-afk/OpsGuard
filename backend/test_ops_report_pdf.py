"""Regression checks for operations report PDF export."""

import asyncio

from app.api import ops_report_pdf
from reportlab.pdfbase import pdfmetrics


def test_ops_report_pdf_registers_chinese_capable_font() -> None:
    ops_report_pdf._font_registered = False
    ops_report_pdf._font_name = "Helvetica"
    ops_report_pdf._font_bold = "Helvetica-Bold"

    ops_report_pdf._register_font()

    assert ops_report_pdf._font_name != "Helvetica"
    assert ops_report_pdf._font_bold != "Helvetica-Bold"
    assert _font_has_text(ops_report_pdf._font_name, "OpsGuard 运维报告 123")
    assert _font_has_text(ops_report_pdf._font_bold, "OpsGuard 运维报告 123")


def test_ops_report_pdf_endpoint_returns_pdf() -> None:
    response = asyncio.run(ops_report_pdf.export_ops_report_pdf(hours=24))

    body = response.body_iterator
    first_chunk = asyncio.run(body.__anext__())

    assert response.media_type == "application/pdf"
    assert first_chunk.startswith(b"%PDF")


def _font_has_text(font_name: str, text: str) -> bool:
    font = pdfmetrics.getFont(font_name)
    char_to_glyph = font.face.charToGlyph
    return all(ord(char) in char_to_glyph for char in text if not char.isspace())
