"""稳定的业务错误。"""

from __future__ import annotations


class DomainError(Exception):
    """可安全返回给客户端的业务错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 422,
        action: str | None = None,
        *,
        retryable: bool | None = None,
        fields: list[dict[str, str]] | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.action = action
        # 错误是否可以重试是接口契约的一部分，不能让前端根据 HTTP 状态自行猜测。
        self.retryable = retryable if retryable is not None else status_code in {429, 503}
        self.fields = fields or []
        self.retry_after = retry_after


class NotFoundError(DomainError):
    """资源不存在或不属于当前账号时统一使用 404。"""

    def __init__(self, message: str = "资源不存在"):
        super().__init__("RESOURCE_NOT_FOUND", message, 404)
