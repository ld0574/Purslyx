"""账号一次性链接邮件。

邮件只在账号事务提交后发送。调用方无需持有数据库会话；投递失败只记录不含令牌的
告警，由用户重新申请新令牌，不回滚已经完成的注册或账号状态变更。
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote

from .config import settings

logger = logging.getLogger(__name__)

ACTION_CONTENT = {
    "verify_email": (
        "验证 Purslyx 邮箱",
        "/app/verify-email",
        "完成邮箱验证后即可登录并领取适用的试用次数。",
    ),
    "reset_password": (
        "重置 Purslyx 密码",
        "/app/reset-password",
        "如果不是你发起的请求，可以忽略这封邮件。",
    ),
    "recover_account": (
        "恢复 Purslyx 账号",
        "/app/recover-account",
        "确认后会恢复原账号及其资料，仍需使用原密码登录。",
    ),
}


def _message(recipient: str, action: str, token: str) -> EmailMessage:
    """构造只包含产品链接的纯文本邮件，不把令牌写入主题或日志。"""

    try:
        subject, path, explanation = ACTION_CONTENT[action]
    except KeyError as exc:
        raise ValueError("不支持的账号邮件类型") from exc
    if not settings.smtp_from:
        raise RuntimeError("未配置 SMTP_FROM")
    url = f"{settings.product_origin.rstrip('/')}{path}?token={quote(token, safe='')}"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message.set_content(
        f"你好：\n\n{explanation}\n\n请打开以下一次性链接：\n{url}\n\n"
        "如果不是你发起的操作，可以忽略这封邮件。"
    )
    return message


def send_account_action_email(recipient: str, action: str, token: str) -> None:
    """通过 SMTP 发送账号邮件；配置或网络错误交给调用方处理。"""

    if not settings.smtp_host:
        raise RuntimeError("未配置 SMTP_HOST")
    if bool(settings.smtp_username) != bool(settings.smtp_password):
        raise RuntimeError("SMTP_USERNAME 与 SMTP_PASSWORD 必须同时配置")
    message = _message(recipient, action, token)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
        client.ehlo()
        if settings.smtp_starttls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.smtp_username and settings.smtp_password:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)


def deliver_account_action_email(recipient: str, action: str, token: str) -> bool:
    """安全投递账号邮件；失败时不传播异常，也绝不记录原始令牌。"""

    try:
        send_account_action_email(recipient, action, token)
    except Exception as exc:
        logger.warning(
            "账号邮件投递失败 action=%s recipient_domain=%s error=%s",
            action,
            recipient.rpartition("@")[2] or "unknown",
            type(exc).__name__,
        )
        return False
    return True
