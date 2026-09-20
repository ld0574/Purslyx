"""资料文件读取和本地结构化提取。

本地解析器用于无外部密钥时的完整开发流程，也作为真实模型解析失败时的可恢复兜底。
它只整理用户已经提供的文字，不生成经历、数字或岗位条件。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import DomainError

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
MAX_PDF_PAGES = 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_text(text: str) -> str:
    """统一换行和空白，但不删除用户可见正文。"""

    value = text.replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    return value.strip()


def extract_file_text(path: Path, source_type: str) -> str:
    """按已确认的文件类型读取文字型 PDF、DOC 和 DOCX。"""

    if path.stat().st_size > MAX_FILE_BYTES:
        raise DomainError("DOCUMENT_TOO_LARGE", "文件不能超过 20 MiB", 413)

    try:
        if source_type == "pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            if len(reader.pages) > MAX_PDF_PAGES:
                raise DomainError("DOCUMENT_TOO_LARGE", "PDF 不能超过 20 页", 413)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif source_type == "docx":
            from docx import Document as DocxDocument

            document = DocxDocument(str(path))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            for table in document.tables:
                text += "\n" + "\n".join(" | ".join(cell.text for cell in row.cells) for row in table.rows)
        elif source_type == "doc":
            text = _extract_legacy_doc(path)
        else:
            text = path.read_text(encoding="utf-8")
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(
            "DOCUMENT_CONTENT_UNREADABLE",
            "文件无法读取，请转换为文字型 PDF、DOCX 或粘贴文字",
            422,
            "paste_text",
        ) from exc

    text = normalize_text(text)
    if not text or len(text) > MAX_TEXT_CHARS:
        if len(text) > MAX_TEXT_CHARS:
            raise DomainError("DOCUMENT_TOO_LARGE", "提取后的正文不能超过 100,000 个字符", 413)
        raise DomainError("DOCUMENT_CONTENT_UNREADABLE", "文件没有可读取的文字，请粘贴文字", 422, "paste_text")
    return text


def _extract_legacy_doc(path: Path) -> str:
    """在 macOS 或安装 antiword 的环境读取旧 DOC；不执行文件内容。"""

    textutil = shutil.which("textutil")
    antiword = shutil.which("antiword")
    if textutil:
        result = subprocess.run(
            [textutil, "-convert", "txt", "-stdout", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
    if antiword:
        result = subprocess.run(
            [antiword, str(path)], capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode == 0:
            return result.stdout
    raise DomainError("DOCUMENT_FORMAT_UNSUPPORTED", "当前环境缺少 DOC 读取工具，请转换为 DOCX 或粘贴文字", 415)


def detect_source_type(filename: str | None, content_type: str | None) -> str:
    """根据扩展名和 MIME 推断受支持的来源类型。"""

    suffix = Path(filename or "").suffix.lower()
    mapping = {".pdf": "pdf", ".doc": "doc", ".docx": "docx", ".txt": "text"}
    if suffix in mapping:
        return mapping[suffix]
    if content_type == "application/pdf":
        return "pdf"
    if content_type in {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}:
        return "docx"
    if content_type == "text/plain":
        return "text"
    raise DomainError("DOCUMENT_FORMAT_UNSUPPORTED", "仅支持文本、PDF、DOC 和 DOCX", 415)


def _slug(value: str, fallback: str) -> str:
    ascii_value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return (ascii_value or fallback)[:60]


def _lines(text: str) -> list[str]:
    return [line.strip(" •·\t") for line in normalize_text(text).split("\n") if line.strip()]


def parse_resume_text(text: str) -> dict[str, Any]:
    """把简历正文拆成稳定段落，段落内容完全来自输入。"""

    lines = _lines(text)
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    headings = {"教育经历", "工作经历", "项目经历", "实习经历", "技能", "校园经历", "个人简介", "经历"}
    for index, line in enumerate(lines, start=1):
        is_heading = line in headings or (len(line) <= 12 and line.endswith((":", "：")))
        if is_heading:
            key = _slug(line.rstrip(":："), f"section-{len(sections) + 1}")
            current = {"section_key": key, "section_type": key, "title": line.rstrip(":："), "position": len(sections) + 1, "segments": []}
            sections.append(current)
            continue
        if current is None:
            current = {"section_key": "profile", "section_type": "profile", "title": "个人资料", "position": 1, "segments": []}
            sections.append(current)
        section_key = current["section_key"]
        current["segments"].append(
            {
                "segment_key": f"{section_key}-{len(current['segments']) + 1}",
                "text": line,
                "source": "user_confirmed",
                "source_line": index,
            }
        )
    return {"schema_version": "document-content-v1", "sections": sections, "job_fields": None}


def parse_job_text(text: str) -> dict[str, Any]:
    """从 JD 文字中提取可检查字段；不为缺失条件补默认值。"""

    lines = _lines(text)
    title = lines[0] if lines else None
    company = None
    location_text = None
    salary_text = None
    experience_text = None
    education_text = None
    requirements: list[str] = []
    responsibilities: list[str] = []
    work_mode = None
    for line in lines[1:]:
        if re.search(r"公司|企业", line) and company is None:
            company = re.sub(r"^(公司|企业)\s*[:：]?\s*", "", line)
        if re.search(r"薪资|薪酬|K|k/月|月薪", line) and salary_text is None:
            salary_text = line
        if experience_text is None and re.search(r"不限经验|无经验|应届|在校生|\d+\s*[-~至到]\s*\d+\s*年|\d+\+?\s*年", line, re.IGNORECASE):
            experience_text = line
        if education_text is None and re.search(r"学历不限|不限学历|博士|硕士|本科|大专|中专|高中", line):
            education_text = line
        if re.search(r"地点|工作地|办公地|城市", line) and location_text is None:
            location_text = re.sub(r"^(地点|工作地|办公地|城市)\s*[:：]?\s*", "", line)
        if work_mode is None:
            if re.search(r"远程|全远程|remote", line, re.IGNORECASE):
                work_mode = "remote"
            elif re.search(r"混合|灵活办公|hybrid", line, re.IGNORECASE):
                work_mode = "hybrid"
            elif re.search(r"现场|坐班|到岗|onsite", line, re.IGNORECASE):
                work_mode = "onsite"
        if re.match(r"^(要求|任职要求|资格|技能)\s*[:：]?", line):
            continue
        if re.match(r"^(职责|工作内容|岗位职责)\s*[:：]?", line):
            continue
        if re.match(r"^[一二三四五六七八九十0-9][、.)）]", line) or line.startswith(("-", "•")):
            requirements.append(line.lstrip("-• "))
        elif any(keyword in line for keyword in ("负责", "参与", "推动", "设计", "开发", "运营")):
            responsibilities.append(line)
    category = infer_job_category(" ".join(lines))
    job_fields = {
        "title": title,
        "company_name": company,
        "location_text": location_text,
        "work_mode": work_mode,
        "salary_text": salary_text,
        "experience_text": experience_text,
        "education_text": education_text,
        "locations": (
            [item.strip() for item in re.split(r"[/／、,，|]", location_text) if item.strip()]
            if location_text
            else []
        ),
        "salary": parse_salary_text(salary_text) if salary_text else None,
        "requirements": requirements or responsibilities,
        "responsibilities": responsibilities,
        "category": category,
        "source_text": normalize_text(text),
    }
    return {"schema_version": "document-content-v1", "sections": [], "job_fields": job_fields}


def infer_job_category(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in ("react", "vue", "python", "java", "前端", "后端", "研发", "开发")):
        return "engineering"
    if any(word in text for word in ("产品", "需求", "用户研究", "原型", "prd")):
        return "product"
    if any(word in text for word in ("运营", "内容", "渠道", "增长")):
        return "operations"
    return "general"


def parse_salary_text(value: str | None) -> dict[str, Any] | None:
    """解析数字，不补猜币种、周期或税制。"""

    if not value:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*([Kk千]?)\s*(?:[-~至到]\s*)(\d+(?:\.\d+)?)\s*([Kk千]?)", value)
    if not match:
        if re.search(r"面议|面谈|可议", value):
            return {"status": "negotiable", "min": None, "max": None, "currency": None, "period": None, "tax_basis": None}
        return {"status": "unknown", "min": None, "max": None, "currency": None, "period": None, "tax_basis": None}
    left, left_unit, right, right_unit = match.groups()
    multiplier = 1000 if left_unit or right_unit else 1
    return {
        "status": "specified",
        "min": str(float(left) * multiplier),
        "max": str(float(right) * multiplier),
        "currency": None,
        "period": "monthly" if ("月" in value or "K" in value or "k" in value or "千" in value) else None,
        "tax_basis": None,
        "salary_months": None,
    }
