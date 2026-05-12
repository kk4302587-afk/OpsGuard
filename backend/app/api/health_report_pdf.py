"""Health report PDF export.

Generates a professional PDF report from the health check data.
Uses reportlab with Chinese font support.
"""

import io
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

router = APIRouter()

# Font setup
_font_registered = False
_font_name = "Helvetica"
_font_bold = "Helvetica-Bold"


def _register_font():
    """Try to find and register a Chinese-capable font."""
    global _font_registered, _font_name, _font_bold

    if _font_registered:
        return

    font_paths = [
        # Linux
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        # Windows
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]

    for font_path in font_paths:
        if Path(font_path).exists():
            try:
                pdfmetrics.registerFont(TTFont("CJK", font_path))
                _font_name = "CJK"
                _font_bold = "CJK"
                _font_registered = True
                return
            except Exception:
                continue

    _font_registered = True


# Colors
C_GREEN = colors.HexColor('#00d4aa')
C_YELLOW = colors.HexColor('#e5c07b')
C_RED = colors.HexColor('#e06c75')
C_BLUE = colors.HexColor('#61afef')
C_DARK = colors.HexColor('#1a1d23')
C_GRAY = colors.HexColor('#5c6370')
C_LIGHT_GRAY = colors.HexColor('#e4e7eb')
C_BG = colors.HexColor('#f8f9fa')

STATUS_COLORS = {"healthy": C_GREEN, "warning": C_YELLOW, "critical": C_RED}
STATUS_LABELS = {"healthy": "正常", "warning": "警告", "critical": "严重"}


@router.get("/export-pdf")
async def export_health_report_pdf():
    """Generate and download a PDF health report."""
    from app.api.health_report import generate_health_report
    report = await generate_health_report()

    _register_font()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18*mm,
        leftMargin=18*mm,
        topMargin=18*mm,
        bottomMargin=18*mm,
    )

    # Styles
    s_title = ParagraphStyle('Title', fontName=_font_bold, fontSize=20, leading=28, textColor=C_DARK, spaceAfter=4*mm)
    s_subtitle = ParagraphStyle('Subtitle', fontName=_font_name, fontSize=9, leading=12, textColor=C_GRAY, spaceAfter=2*mm)
    s_heading = ParagraphStyle('Heading', fontName=_font_bold, fontSize=13, leading=18, spaceBefore=6*mm, spaceAfter=3*mm, textColor=C_DARK)
    s_body = ParagraphStyle('Body', fontName=_font_name, fontSize=10, leading=14, spaceAfter=2*mm)
    s_issue = ParagraphStyle('Issue', fontName=_font_name, fontSize=9, leading=13, leftIndent=12, textColor=colors.HexColor('#b8860b'))
    s_rec = ParagraphStyle('Rec', fontName=_font_name, fontSize=9, leading=13, leftIndent=12, textColor=colors.HexColor('#4682b4'))
    s_footer = ParagraphStyle('Footer', fontName=_font_name, fontSize=8, leading=11, textColor=C_GRAY, alignment=1)

    elements = []

    # === Header ===
    elements.append(Paragraph("OpsGuard 系统健康巡检报告", s_title))
    elements.append(Paragraph(
        f"生成时间: {report['generated_at'][:19].replace('T', ' ')}  |  "
        f"主机: {report['hostname']}  |  系统: {report['os']}  |  架构: {report['arch']}",
        s_subtitle,
    ))
    elements.append(Spacer(1, 3*mm))

    # === Overall Status Banner ===
    status_color = STATUS_COLORS.get(report['overall_status'], C_GRAY)
    status_label = STATUS_LABELS.get(report['overall_status'], '未知')

    banner_data = [[f"整体评估:  {status_label}"]]
    banner = Table(banner_data, colWidths=[174*mm], rowHeights=[12*mm])
    banner.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), status_color),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), _font_bold),
        ('FONTSIZE', (0, 0), (-1, -1), 13),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(banner)
    elements.append(Spacer(1, 6*mm))

    # === Sections ===
    for section in report['sections']:
        sec_elements = []
        sec_color = STATUS_COLORS.get(section['status'], C_GRAY)
        sec_label = STATUS_LABELS.get(section['status'], '')

        # Section heading with colored indicator
        sec_elements.append(Paragraph(
            f'<font color="{sec_color.hexval()}">\u25cf</font>  {section["title"]}  '
            f'<font size="9" color="{sec_color.hexval()}">[{sec_label}]</font>',
            s_heading,
        ))

        # Metrics table (2 columns: key | value)
        metrics_data = []
        for key, value in section['metrics'].items():
            if isinstance(value, dict):
                val_str = "  |  ".join(f"{k}: {v}" for k, v in value.items())
            elif isinstance(value, list):
                val_str = ", ".join(str(v) for v in value[:10])
                if len(value) > 10:
                    val_str += " ..."
            else:
                val_str = str(value)
            metrics_data.append([key, val_str])

        if metrics_data:
            col_widths = [45*mm, 129*mm]
            metrics_table = Table(metrics_data, colWidths=col_widths)
            metrics_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), _font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TEXTCOLOR', (0, 0), (0, -1), C_GRAY),
                ('TEXTCOLOR', (1, 0), (1, -1), C_DARK),
                ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                ('ALIGN', (1, 0), (1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (0, -1), 6),
                ('LEFTPADDING', (1, 0), (1, -1), 12),
                ('BACKGROUND', (0, 0), (-1, -1), C_BG),
                ('LINEBELOW', (0, 0), (-1, -2), 0.3, C_LIGHT_GRAY),
                ('ROUNDEDCORNERS', [3, 3, 3, 3]),
            ]))
            sec_elements.append(metrics_table)
            sec_elements.append(Spacer(1, 3*mm))

        # Issues
        if section['issues']:
            sec_elements.append(Paragraph(
                '<font color="#b8860b">\u26a0 发现问题:</font>', s_body,
            ))
            for issue in section['issues']:
                sec_elements.append(Paragraph(f"\u2022  {issue}", s_issue))
            sec_elements.append(Spacer(1, 2*mm))

        # Recommendations
        if section['recommendations']:
            sec_elements.append(Paragraph(
                '<font color="#4682b4">\u2794 优化建议:</font>', s_body,
            ))
            for rec in section['recommendations']:
                sec_elements.append(Paragraph(f"\u2022  {rec}", s_rec))
            sec_elements.append(Spacer(1, 2*mm))

        # Separator
        sec_elements.append(HRFlowable(width="100%", thickness=0.5, color=C_LIGHT_GRAY, spaceBefore=2*mm, spaceAfter=2*mm))

        elements.append(KeepTogether(sec_elements))

    # === Footer ===
    elements.append(Spacer(1, 10*mm))
    elements.append(Paragraph(
        "— 由 OpsGuard 智能运维 Agent 自动生成 —",
        s_footer,
    ))

    # Build
    doc.build(elements)
    buffer.seek(0)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"opsguard_health_report_{timestamp}.pdf"

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
