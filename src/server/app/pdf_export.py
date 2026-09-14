"""岗位版简历 PDF 渲染器。

渲染器只读取已确认的版本内容，不把岗位期望或模型提示词自动写入简历；页面采用
ReportLab 的可换行段落，长文本和中英文混排会自然分页。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape


FONT_NAME = "STSong-Light"


def _register_font() -> None:
    """使用 ReportLab 内置中文 CID 字体，避免依赖本机字体文件。"""

    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))


def _clean_text(value: Any) -> str:
    return escape(str(value or "").replace("\u2028", " ").strip())


def render_resume_pdf(content: dict[str, Any], layout: dict[str, Any], output_path: Path, title: str) -> None:
    """生成带页眉、页脚和稳定层级的 PDF。"""

    _register_font()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # API 已经做过范围校验；这里仍保留旧字段兼容，避免历史岗位版无法重新导出。
    try:
        font_size = float(layout.get("font_size_pt", layout.get("font_size", 10.5)))
        line_spacing = float(layout.get("line_height", layout.get("line_spacing", 1.4)))
        section_spacing = float(layout.get("section_spacing_pt", 8.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("岗位版排版参数不是数字") from exc
    if not all(math.isfinite(value) for value in (font_size, line_spacing, section_spacing)):
        raise ValueError("岗位版排版参数不是有限数字")
    # 成品只使用品牌蓝；客户端不能通过 layout 注入任意颜色或外部资源。
    accent = colors.HexColor("#1264e8")
    bold_segment_keys = {str(value) for value in (layout.get("bold_segment_keys") or [])}
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("PurslyxTitle", parent=styles["Title"], fontName=FONT_NAME, fontSize=20, leading=25, alignment=TA_CENTER, textColor=colors.HexColor("#16324f"), spaceAfter=5 * mm)
    section_style = ParagraphStyle("PurslyxSection", parent=styles["Heading2"], fontName=FONT_NAME, fontSize=12.5, leading=16, textColor=accent, spaceBefore=section_spacing, spaceAfter=section_spacing / 3, keepWithNext=True)
    body_style = ParagraphStyle("PurslyxBody", parent=styles["BodyText"], fontName=FONT_NAME, fontSize=font_size, leading=font_size * line_spacing, textColor=colors.HexColor("#253b53"), alignment=TA_LEFT, spaceAfter=max(1.6, section_spacing / 4), wordWrap="CJK")
    small_style = ParagraphStyle("PurslyxSmall", parent=body_style, fontSize=8.5, leading=11, textColor=colors.HexColor("#6c8296"), spaceAfter=0)

    story: list[Any] = [Paragraph(_clean_text(title), title_style), HRFlowable(width="100%", thickness=1.2, color=accent, spaceAfter=4 * mm)]
    sections = content.get("sections") or []
    if not sections:
        story.append(Paragraph("暂无可展示的已确认内容。", body_style))
    by_key = {str(row.get("section_key")): row for row in sections}
    order = [str(value) for value in (layout.get("section_order") or [])]
    ordered_sections = [by_key[key] for key in order if key in by_key]
    ordered_sections.extend(row for row in sorted(sections, key=lambda row: int(row.get("position", 0))) if row not in ordered_sections)
    for section in ordered_sections:
        heading = section.get("title") or section.get("section_key") or "经历"
        story.append(Paragraph(_clean_text(heading), section_style))
        for segment in section.get("segments") or []:
            text = _clean_text(segment.get("text"))
            if text:
                if str(segment.get("segment_key")) in bold_segment_keys:
                    text = f"<b>{text}</b>"
                story.append(Paragraph(f"- {text}", body_style))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Purslyx - 内容来自用户已确认资料，未自动添加未经核验的经历。", small_style))

    def draw_page(canvas: Any, document: Any) -> None:
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#dce9f4"))
        canvas.line(document.leftMargin, 15 * mm, width - document.rightMargin, 15 * mm)
        canvas.setFont(FONT_NAME, 8)
        canvas.setFillColor(colors.HexColor("#6c8296"))
        canvas.drawString(document.leftMargin, 10 * mm, "Purslyx")
        canvas.drawRightString(width - document.rightMargin, 10 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    document = SimpleDocTemplate(str(output_path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm, title=title, author="Purslyx")
    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
