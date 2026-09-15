"""岗位版简历 PDF 渲染器。

渲染器只读取已确认的版本内容，不把岗位期望或模型提示词自动写入简历；页面采用
ReportLab 的可换行段落，长文本和中英文混排会自然分页。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

SERIF_FONT_NAME = "STSong-Light"
SANS_REGULAR_NAME = "PurslyxSansRegular"
SANS_BOLD_NAME = "PurslyxSansBold"

# 本地开发机与常见 Linux 镜像中的中文无衬线字体。候选项都只从可信的系统字体
# 目录读取；若部署机没有这些字体，则稳定回退到 ReportLab 内置中文字体。
SANS_FONT_CANDIDATES = (
    (
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        1,
    ),
    (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        2,
    ),
    (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf"),
        0,
    ),
)


def _register_serif_font() -> None:
    """注册始终可用的中文宋体。"""

    if SERIF_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(SERIF_FONT_NAME))


def _font_names(font_family: str) -> tuple[str, str]:
    """返回普通与粗体字体；中文无衬线缺失时使用可用的中文回退字体。"""

    _register_serif_font()
    if font_family == "source_han_serif":
        return SERIF_FONT_NAME, SERIF_FONT_NAME
    if SANS_REGULAR_NAME in pdfmetrics.getRegisteredFontNames():
        return SANS_REGULAR_NAME, SANS_BOLD_NAME
    for regular_path, bold_path, subfont_index in SANS_FONT_CANDIDATES:
        if not regular_path.is_file() or not bold_path.is_file():
            continue
        try:
            pdfmetrics.registerFont(
                TTFont(SANS_REGULAR_NAME, str(regular_path), subfontIndex=subfont_index)
            )
            pdfmetrics.registerFont(
                TTFont(SANS_BOLD_NAME, str(bold_path), subfontIndex=subfont_index)
            )
            return SANS_REGULAR_NAME, SANS_BOLD_NAME
        except Exception:
            # 某些发行版的 TTC 子字体顺序不同；继续尝试下一组，不影响 PDF 导出。
            continue
    return SERIF_FONT_NAME, SERIF_FONT_NAME


def _clean_text(value: Any) -> str:
    return escape(str(value or "").replace("\u2028", " ").strip())


def render_resume_pdf(
    content: dict[str, Any], layout: dict[str, Any], output_path: Path, title: str
) -> int:
    """生成带页眉、页脚和稳定层级的 PDF，并返回最终页数。"""

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
    regular_font, bold_font = _font_names(str(layout.get("font_family", "noto_sans_sc")))
    bold_segment_keys = {str(value) for value in (layout.get("bold_segment_keys") or [])}
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("PurslyxTitle", parent=styles["Title"], fontName=bold_font, fontSize=20, leading=25, alignment=TA_CENTER, textColor=colors.HexColor("#16324f"), spaceAfter=5 * mm)
    section_style = ParagraphStyle("PurslyxSection", parent=styles["Heading2"], fontName=bold_font, fontSize=12.5, leading=16, textColor=accent, spaceBefore=section_spacing, spaceAfter=section_spacing / 3, keepWithNext=True)
    body_style = ParagraphStyle("PurslyxBody", parent=styles["BodyText"], fontName=regular_font, fontSize=font_size, leading=font_size * line_spacing, textColor=colors.HexColor("#253b53"), alignment=TA_LEFT, spaceAfter=max(1.6, section_spacing / 4), wordWrap="CJK")
    bold_body_style = ParagraphStyle("PurslyxBoldBody", parent=body_style, fontName=bold_font)
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
                style = bold_body_style if str(segment.get("segment_key")) in bold_segment_keys else body_style
                story.append(Paragraph(f"- {text}", style))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Purslyx - 内容来自用户已确认资料，未自动添加未经核验的经历。", small_style))

    def draw_page(canvas: Any, document: Any) -> None:
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#dce9f4"))
        canvas.line(document.leftMargin, 15 * mm, width - document.rightMargin, 15 * mm)
        canvas.setFont(regular_font, 8)
        canvas.setFillColor(colors.HexColor("#6c8296"))
        canvas.drawString(document.leftMargin, 10 * mm, "Purslyx")
        canvas.drawRightString(width - document.rightMargin, 10 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    document = SimpleDocTemplate(str(output_path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm, title=title, author="Purslyx")
    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return int(document.page)
