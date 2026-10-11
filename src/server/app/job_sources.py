"""来源身份 v1：不改变用于原平台访问的 URL，也不按标题/公司合并。"""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse


def source_identity_url(platform: str, value: str) -> str | None:
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        domain = {"boss": "zhipin.com", "liepin": "liepin.com"}.get(platform)
        if parsed.scheme != "https" or not domain or host not in {domain, f"www.{domain}"}:
            return None
        path = parsed.path.rstrip("/") or "/"
        # 受支持详情页的岗位 ID 已经在路径中，查询参数仅影响访问上下文。
        stable_path = (platform == "boss" and re.fullmatch(r"/job_detail/[A-Za-z0-9_-]+\.html", path)) or (
            platform == "liepin" and re.fullmatch(r"/job/[A-Za-z0-9_-]+\.(?:shtml|html)", path)
        )
        query = [] if stable_path else sorted((key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
                                               if not key.lower().startswith("utm_") and key.lower() not in {"spm", "ka", "tracking", "trackingid"})
        return f"https://www.{domain}{path}" + ("?" + urlencode(query) if query else "")
    except (ValueError, TypeError):
        return None


def job_source_key(source_type: str, platform: str | None, source_hash: str | None, item_id: str,
                   source_url: str | None = None) -> str:
    if source_type == "browser_capture" and platform:
        normalized = source_identity_url(platform, source_url) if source_url else None
        digest = hashlib.sha256(normalized.encode()).hexdigest() if normalized else source_hash
        if digest:
            return f"browser:{platform}:{digest}"
    return f"manual:{item_id}"
