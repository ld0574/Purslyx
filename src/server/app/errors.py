"""稳定的业务错误。"""

from __future__ import annotations


class DomainError(Exception):
    """可安全返回给客户端的业务错误。"""

    def __init__(self, code: str, message: str, status_code: int = 422, action: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.action = action


class NotFoundError(DomainError):
    """资源不存在或不属于当前账号时统一使用 404。"""

    def __init__(self, message: str = "资源不存在"):
        super().__init__("RESOURCE_NOT_FOUND", message, 404)
