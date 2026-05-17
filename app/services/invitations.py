"""
Workspace invitation delivery helpers.

Marge stores only token hashes, so the raw invite token must be delivered once.
In production this should go through SMTP or a transactional email provider; in
local smoke tests an outbox file gives us deterministic coverage without
contacting a real mail server.
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from app.models import AccountUser, ChurchAccount


@dataclass
class InviteDeliveryResult:
    status: str
    channel: str
    detail: str
    sent_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "channel": self.channel,
            "detail": self.detail,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }


def send_workspace_invite(account: ChurchAccount, user: AccountUser, token: str, inviter_name: str | None = None) -> InviteDeliveryResult:
    if not user.email:
        return InviteDeliveryResult(status="skipped", channel="none", detail="Invite user has no email address.")

    message = _invite_message(account, user, token, inviter_name)
    return _deliver_message(message, user.email)


def send_login_link(account: ChurchAccount, user: AccountUser, token: str) -> InviteDeliveryResult:
    if not user.email:
        return InviteDeliveryResult(status="skipped", channel="none", detail="Login user has no email address.")

    message = _login_message(account, user, token)
    return _deliver_message(message, user.email)


def _deliver_message(message: EmailMessage, recipient: str) -> InviteDeliveryResult:
    outbox_path = os.getenv("MARGE_INVITE_EMAIL_OUTBOX", "").strip()
    if outbox_path:
        Path(outbox_path).parent.mkdir(parents=True, exist_ok=True)
        with open(outbox_path, "a", encoding="utf-8") as outbox:
            outbox.write(message.as_string())
            outbox.write("\n\n---\n\n")
        return InviteDeliveryResult(status="sent", channel="file", detail=f"Invite written to {outbox_path}.", sent_at=datetime.utcnow())

    host = os.getenv("SMTP_HOST", "").strip()
    from_address = _from_address()
    if not host or not from_address:
        return InviteDeliveryResult(status="not_configured", channel="none", detail="Set SMTP_HOST and MARGE_INVITE_EMAIL_FROM to email workspace invites.")

    port = _int_env("SMTP_PORT", 587)
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    use_starttls = os.getenv("SMTP_STARTTLS", "true").strip().lower() not in {"0", "false", "no"}

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_starttls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
    except OSError as exc:
        return InviteDeliveryResult(status="failed", channel="smtp", detail=f"SMTP email delivery failed: {exc}")

    return InviteDeliveryResult(status="sent", channel="smtp", detail=f"Email sent to {recipient}.", sent_at=datetime.utcnow())


def _invite_message(account: ChurchAccount, user: AccountUser, token: str, inviter_name: str | None) -> EmailMessage:
    app_url = os.getenv("MARGE_APP_URL", "http://127.0.0.1:8000/app").strip().rstrip("/") or "http://127.0.0.1:8000/app"
    invite_url = f"{app_url}?invite_token={quote(token)}"
    display_name = user.name or user.email or "there"
    inviter = inviter_name or account.pastor_name or "your church"
    body = (
        f"Hi {display_name},\n\n"
        f"{inviter} invited you to Marge for {account.church_name} as {user.role}.\n\n"
        f"Open this link to join the workspace:\n{invite_url}\n\n"
        "Marge will exchange this one-time workspace token for a browser session and remove it from the address bar.\n"
        "Do not forward this invitation unless you intend to share access to the workspace.\n"
    )

    message = EmailMessage()
    message["To"] = user.email
    message["From"] = _from_address() or f"Marge <no-reply@{account.slug}.local>"
    message["Subject"] = f"Join {account.church_name} in Marge"
    message.set_content(body)
    return message


def _login_message(account: ChurchAccount, user: AccountUser, token: str) -> EmailMessage:
    app_url = os.getenv("MARGE_APP_URL", "http://127.0.0.1:8000/app").strip().rstrip("/") or "http://127.0.0.1:8000/app"
    login_url = f"{app_url}?login_token={quote(token)}"
    display_name = user.name or user.email or "there"
    body = (
        f"Hi {display_name},\n\n"
        f"Use this one-time link to open Marge for {account.church_name}:\n{login_url}\n\n"
        "This link expires soon and can only be used once. Marge will exchange it for a browser session and remove it from the address bar.\n"
        "If you did not ask for this link, you can ignore this email.\n"
    )

    message = EmailMessage()
    message["To"] = user.email
    message["From"] = _from_address() or f"Marge <no-reply@{account.slug}.local>"
    message["Subject"] = f"Your Marge sign-in link for {account.church_name}"
    message.set_content(body)
    return message


def _from_address() -> str:
    return os.getenv("MARGE_INVITE_EMAIL_FROM", "").strip()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
