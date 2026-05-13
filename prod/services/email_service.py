"""OAuth 案内などのメール送信（SMTP / 環境変数設定）."""

from __future__ import annotations

import os
from pathlib import Path

from utils.env_util import load_env_file

load_env_file(Path(__file__).resolve().parent.parent / ".env")
import html as html_module
import smtplib
import ssl
from email import policy as email_policy
from email.message import EmailMessage
from typing import Optional, Tuple

# OAuth の認可 URL は非常に長い。SMTP の既定（行長制限）で折り返すと URL が壊れ、
# Gmail 等でリンクがクリックできなくなる。送信時は行折り返しを無効にする。
_SMTP_SEND_POLICY = email_policy.SMTP.clone(max_line_length=0)


def _send_message_compat(server: smtplib.SMTP, msg: EmailMessage) -> None:
    """Python 3.9+ の send_message(..., policy=) を使う。3.8 以前は policy 未対応のため従来どおり。"""
    try:
        server.send_message(msg, policy=_SMTP_SEND_POLICY)
    except TypeError:
        server.send_message(msg)


def _smtp_password() -> str:
    """Gmail アプリ パスワードは表示時にスペース区切りがあるため除去する."""
    return os.environ.get("SMTP_PASSWORD", "").strip().replace(" ", "")


def smtp_configured() -> bool:
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = _smtp_password()
    return bool(host and user and password)


def _from_address() -> str:
    return os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USER", "").strip()


def send_plain_email(
    to_addr: str,
    subject: str,
    body: str,
    *,
    html_body: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    テキストメールを 1 通送信する。html_body を渡すと multipart/alternative（HTML+テキスト）になり、
    Gmail 等でリンクがクリック可能になりやすい。
    Returns:
        (成功, エラーメッセージ) — 成功時はエラー文字列は空。
    """
    to_addr = to_addr.strip()
    if not to_addr:
        return False, "送信先メールアドレスが空です。"

    host = os.environ.get("SMTP_HOST", "").strip()
    port_str = os.environ.get("SMTP_PORT", "587").strip() or "587"
    user = os.environ.get("SMTP_USER", "").strip()
    password = _smtp_password()
    from_addr = _from_address()

    if not host or not user or not password:
        return False, "SMTP の設定が不足しています（SMTP_HOST, SMTP_USER, SMTP_PASSWORD）。"
    if not from_addr:
        return False, "送信元（SMTP_FROM または SMTP_USER）を設定してください。"

    try:
        port = int(port_str)
    except ValueError:
        return False, f"SMTP_PORT が数値ではありません: {port_str!r}"

    use_tls = os.environ.get("SMTP_USE_TLS", "1").strip().lower() not in ("0", "false", "no", "off")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body, charset="utf-8")
    if html_body:
        msg.add_alternative(html_body, subtype="html", charset="utf-8")

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
                server.login(user, password)
                _send_message_compat(server, msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.ehlo()
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(user, password)
                _send_message_compat(server, msg)
        return True, ""
    except OSError as e:
        return False, f"接続エラー: {e}"
    except smtplib.SMTPException as e:
        return False, f"SMTP エラー: {e}"
    except Exception as e:
        return False, str(e)


def build_worker_oauth_email_body(worker_display_name: str, auth_url: str) -> str:
    name = worker_display_name.strip() or "ご担当者"
    return (
        f"{name} 様\n\n"
        "日程調整システムで、あなたの Google カレンダーを読み取れるようにするため、"
        "次のリンクをブラウザで開き、表示された Google アカウントで許可してください。\n\n"
        f"{auth_url}\n\n"
        "（上記が途中で改行されている場合は、1行に繋げてから開いてください。）\n\n"
        "許可後、ブラウザはアプリに戻ります。心当たりがない場合はこのメールを破棄してください。\n"
    )


def build_vehicle_fleet_oauth_email_body(auth_url: str) -> str:
    return (
        "日程調整システムの「車両用 Google カレンダー」連携のため、"
        "次のリンクをブラウザで開き、車両カレンダーをまとめている Google アカウントで許可してください。\n\n"
        f"{auth_url}\n\n"
        "許可後、ブラウザはアプリに戻ります。心当たりがない場合はこのメールを破棄してください。\n"
    )


def build_worker_oauth_email_html(worker_display_name: str, auth_url: str) -> str:
    """OAuth 案内の HTML 本文（クリック可能な a タグ付き）."""
    name = html_module.escape(worker_display_name.strip() or "ご担当者")
    href = html_module.escape(auth_url, quote=True)
    return (
        "<!DOCTYPE html><html><body>"
        f"<p>{name} 様</p>"
        "<p>日程調整システムで、あなたの Google カレンダーを読み取れるようにするため、"
        "次のリンクから許可してください。</p>"
        f'<p><a href="{href}">Google でカレンダー連携を許可する</a></p>'
        "<p>リンクが開けない場合は、次の URL をコピーしてブラウザのアドレス欄に貼り付けてください。</p>"
        f'<p style="word-break:break-all;font-size:12px;">{href}</p>'
        "<p>許可後、ブラウザはアプリに戻ります。心当たりがない場合はこのメールを破棄してください。</p>"
        "</body></html>"
    )


def build_vehicle_fleet_oauth_email_html(auth_url: str) -> str:
    href = html_module.escape(auth_url, quote=True)
    return (
        "<!DOCTYPE html><html><body>"
        "<p>日程調整システムの「車両用 Google カレンダー」連携のため、"
        "次のリンクから、車両カレンダーをまとめている Google アカウントで許可してください。</p>"
        f'<p><a href="{href}">Google で車両カレンダー連携を許可する</a></p>'
        "<p>リンクが開けない場合は、次の URL をコピーしてブラウザに貼り付けてください。</p>"
        f'<p style="word-break:break-all;font-size:12px;">{href}</p>'
        "<p>許可後、ブラウザはアプリに戻ります。心当たりがない場合はこのメールを破棄してください。</p>"
        "</body></html>"
    )


def build_vehicle_item_oauth_email_body(vehicle_display_name: str, auth_url: str) -> str:
    """車両1台あたりの OAuth 案内（職人向けメールと同様の運用）."""
    name = vehicle_display_name.strip() or "ご担当"
    return (
        f"{name}（車両）のカレンダー連携\n\n"
        "日程調整システムで、この車両に紐づく Google カレンダーを読み取れるようにするため、"
        "次のリンクをブラウザで開き、表示された Google アカウントで許可してください。\n\n"
        f"{auth_url}\n\n"
        "許可後、ブラウザはアプリに戻ります。心当たりがない場合はこのメールを破棄してください。\n"
    )


def build_vehicle_item_oauth_email_html(vehicle_display_name: str, auth_url: str) -> str:
    name = html_module.escape(vehicle_display_name.strip() or "ご担当")
    href = html_module.escape(auth_url, quote=True)
    return (
        "<!DOCTYPE html><html><body>"
        f"<p>{name}（車両）のカレンダー連携</p>"
        "<p>日程調整システムで、この車両に紐づく Google カレンダーを読み取れるようにするため、"
        "次のリンクから許可してください。</p>"
        f'<p><a href="{href}">Google で車両カレンダー連携を許可する</a></p>'
        "<p>リンクが開けない場合は、次の URL をコピーしてブラウザのアドレス欄に貼り付けてください。</p>"
        f'<p style="word-break:break-all;font-size:12px;">{href}</p>'
        "<p>許可後、ブラウザはアプリに戻ります。心当たりがない場合はこのメールを破棄してください。</p>"
        "</body></html>"
    )
