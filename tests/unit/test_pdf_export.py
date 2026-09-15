"""岗位版简历 PDF 的分页、混排与输入安全测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader

from server.app.pdf_export import render_resume_pdf


def _content(segment_count: int = 2) -> dict:
    return {
        "sections": [
            {
                "section_key": "projects",
                "title": "项目经历 Projects",
                "position": 2,
                "segments": [
                    {
                        "segment_key": f"project-{index}",
                        "text": f"第 {index} 段：负责 Purslyx API & Web <integration>，完成测试与发布。",
                    }
                    for index in range(segment_count)
                ],
            },
            {
                "section_key": "skills",
                "title": "技能 Skills",
                "position": 1,
                "segments": [
                    {"segment_key": "skill-1", "text": "Python / PostgreSQL / 中文排版"}
                ],
            },
        ]
    }


def test_pdf_renders_chinese_english_and_escaped_content(tmp_path: Path) -> None:
    output = tmp_path / "resume.pdf"
    page_count = render_resume_pdf(
        _content(),
        {
            "section_order": ["skills", "projects"],
            "font_size_pt": 10.5,
            "line_height": 1.4,
            "section_spacing_pt": 8,
            "bold_segment_keys": ["skill-1"],
        },
        output,
        "张三 Zhang San",
    )
    reader = PdfReader(str(output))
    assert output.read_bytes().startswith(b"%PDF")
    assert len(reader.pages) == 1
    assert page_count == len(reader.pages)
    assert reader.metadata.title == "张三 Zhang San"
    assert "Purslyx" in (reader.pages[0].extract_text() or "")


def test_long_pdf_flows_to_multiple_pages_without_truncation(tmp_path: Path) -> None:
    output = tmp_path / "long-resume.pdf"
    page_count = render_resume_pdf(_content(140), {}, output, "Long Resume")
    reader = PdfReader(str(output))
    assert len(reader.pages) >= 3
    assert page_count == len(reader.pages)
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Long Resume" in extracted
    assert "Purslyx" in extracted
    assert all(f"{page_no}" in extracted for page_no in range(1, len(reader.pages) + 1))


def test_pdf_supports_serif_font_option(tmp_path: Path) -> None:
    output = tmp_path / "serif-resume.pdf"

    page_count = render_resume_pdf(
        _content(), {"font_family": "source_han_serif"}, output, "宋体岗位版简历"
    )

    assert page_count == 1
    assert output.read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize(
    "layout",
    [
        {"font_size_pt": "not-a-number"},
        {"line_height": float("nan")},
        {"section_spacing_pt": float("inf")},
    ],
)
def test_pdf_rejects_invalid_layout_numbers(tmp_path: Path, layout: dict) -> None:
    with pytest.raises(ValueError):
        render_resume_pdf(_content(), layout, tmp_path / "invalid.pdf", "Invalid")
