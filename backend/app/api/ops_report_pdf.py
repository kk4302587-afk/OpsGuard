"""Operations report PDF export."""

import io
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query
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

_font_registered = False
_font_name = "Helvetica"
_font_bold = "Helvetica-Bold"


def _register_font():
    global _font_registered, _font_name, _font_bold
    if _font_registered:
        return
    font_paths = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
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


C_GREEN = colors.HexColor('#00d4aa')
C_YELLOW = colors.HexColor('#e5c07b')
C_RED = colors.HexColor('#e06c75')
C_BLUE = colors.HexColor('#61afef')
C_DARK = colors.HexColor('#1a1d23')
C_GRAY = colors.HexColor('#5c6370')
C_LIGHT = colors.HexColor('#e4e7eb')
C_BG = colors.HexColor('#f8f9fa')


@router.get("/export-pdf")
async def export_ops_report_pdf(hours: int = Query(default=24)):
    """Generate and download an operations report as PDF."""
    from app.api.ops_report import generate_ops_report
    report = await generate_ops_report(hours)

    _register_font()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)

    s_title = ParagraphStyle('Title', fontName=_font_bold, fontSize=18, leading=24, textColor=C_DARK, spaceAfter=4*mm)
    s_subtitle = ParagraphStyle('Sub', fontName=_font_name, fontSize=9, leading=12, textColor=C_GRAY, spaceAfter=4*mm)
    s_heading = ParagraphStyle('H', fontName=_font_bold, fontSize=12, leading=16, spaceBefore=5*mm, spaceAfter=3*mm, textColor=C_DARK)
    s_body = ParagraphStyle('B', fontName=_font_name, fontSize=10, leading=14, spaceAfter=2*mm)
    s_small = ParagraphStyle('S', fontName=_font_name, fontSize=9, leading=12, textColor=C_GRAY)
    s_footer = ParagraphStyle('F', fontName=_font_name, fontSize=8, leading=11, textColor=C_GRAY, alignment=1)

    elements = []

    # Title
    elements.append(Paragraph("OpsGuard 运维报告", s_title))
    elements.append(Paragraph(
        f"时间范围: {report['time_range']}  |  生成时间: {report['generated_at'][:19].replace('T', ' ')}",
        s_subtitle,
    ))

    # Stats table
    sections = report["sections"]
    stats_data = [[
        f"会话: {sections.get('sessions', {}).get('count', 0)}",
        f"工具调用: {sections.get('tool_calls', {}).get('total', 0)}",
        f"安全拦截: {sections.get('security', {}).get('blocks', 0)}",
        f"审批通过: {sections.get('approvals', {}).get('approved', 0)}",
        f"新增知识: {sections.get('knowledge', {}).get('count', 0)}",
        f"新增 Runbook: {sections.get('runbooks', {}).get('count', 0)}",
    ]]
    stats_table = Table(stats_data, colWidths=[29*mm]*6)
    stats_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_BG),
        ('FONTNAME', (0, 0), (-1, -1), _font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, C_LIGHT),
    ]))
    elements.append(stats_table)
    elements.append(Spacer(1, 5*mm))

    # Tool calls breakdown
    tool_calls = sections.get("tool_calls", {})
    if tool_calls.get("by_tool"):
        elements.append(Paragraph("工具调用分布", s_heading))
        tool_data = [[name, str(count)] for name, count in tool_calls["by_tool"].items()]
        if tool_data:
            t = Table(tool_data, colWidths=[100*mm, 30*mm])
            t.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), _font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TEXTCOLOR', (0, 0), (0, -1), C_DARK),
                ('TEXTCOLOR', (1, 0), (1, -1), C_GREEN),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LINEBELOW', (0, 0), (-1, -2), 0.3, C_LIGHT),
                ('BACKGROUND', (0, 0), (-1, -1), C_BG),
            ]))
            elements.append(t)

    # Security events
    security = sections.get("security", {})
    if security.get("blocks", 0) > 0:
        elements.append(Paragraph("安全拦截事件", s_heading))
        for detail in security.get("details", [])[:10]:
            elements.append(Paragraph(f"\u2022  {detail}", s_small))

    # Approvals
    approvals = sections.get("approvals", {})
    if approvals.get("total_requests", 0) > 0:
        elements.append(Paragraph("审批记录", s_heading))
        elements.append(Paragraph(
            f"请求: {approvals['total_requests']}  |  批准: {approvals['approved']}  |  拒绝: {approvals['rejected']}",
            s_body,
        ))

    # Incidents
    incidents = sections.get("incidents", {})
    if incidents.get("items"):
        elements.append(Paragraph("事件草稿", s_heading))
        for item in incidents["items"][:10]:
            status = _status_label(item.get("status", ""))
            title = item.get("problem_statement") or item.get("id", "")
            elements.append(Paragraph(f"\u2022  [{status}] {title}", s_small))

    # Multimodal evidence
    multimodal = sections.get("multimodal_evidence", {})
    if multimodal.get("items"):
        elements.append(Paragraph("多模态证据", s_heading))
        for item in multimodal["items"][:10]:
            input_label = "语音识别" if item.get("input_type") == "audio" else "图片识别"
            summary = item.get("summary") or item.get("recognized_text") or "已记录识别结果"
            verification = item.get("verification") or []
            verify_text = "；真实工具验证: " + "、".join(
                str(v.get("source") or v.get("title") or "") for v in verification[:3]
            ) if verification else "；尚未找到真实工具验证"
            elements.append(Paragraph(f"\u2022  [{input_label}] {summary[:120]}{verify_text}", s_small))

    # Sessions
    sess = sections.get("sessions", {})
    if sess.get("items"):
        elements.append(Paragraph("会话记录", s_heading))
        for item in sess["items"][:15]:
            title = item.get("title", "未命名")
            time = item.get("created_at", "")[:16].replace("T", " ")
            elements.append(Paragraph(f"\u2022  {title}  <font color='#5c6370'>({time})</font>", s_small))

    # Knowledge
    knowledge = sections.get("knowledge", {})
    if knowledge.get("items"):
        elements.append(Paragraph("新增知识条目", s_heading))
        for item in knowledge["items"][:10]:
            elements.append(Paragraph(f"\u2022  {item.get('problem_signature', '')}", s_small))

    # Footer
    elements.append(Spacer(1, 10*mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=C_LIGHT))
    elements.append(Spacer(1, 3*mm))
    elements.append(Paragraph("— 由 OpsGuard 智能运维 Agent 自动生成 —", s_footer))

    doc.build(elements)
    buffer.seek(0)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"opsguard_ops_report_{timestamp}.pdf"

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _status_label(status: str) -> str:
    return {
        "resolved": "已解决",
        "failed": "失败",
        "open": "处理中",
        "active": "处理中",
    }.get(status or "", status or "未知")
