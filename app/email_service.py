"""Email service - send onboarding email to new CTV after registration."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Path to HTML template
TEMPLATE_DIR = Path(__file__).parent / "templates"
ONBOARD_TEMPLATE = TEMPLATE_DIR / "AgentOnboard.html"


def _load_onboard_template(fullname: str) -> str:
    """Load AgentOnboard.html and replace placeholders."""
    try:
        html = ONBOARD_TEMPLATE.read_text(encoding="utf-8")
        html = html.replace("{fullName}", fullname)
        html = html.replace("{loginUrl}", settings.FE_LOGIN_URL)
        return html
    except FileNotFoundError:
        logger.warning("AgentOnboard.html template not found, using fallback")
        return _build_fallback_html(fullname)


def _build_fallback_html(fullname: str) -> str:
    """Fallback template in case file is missing."""
    login_url = settings.FE_LOGIN_URL
    return f"""
    <html>
    <body style="font-family: 'Segoe UI', sans-serif; padding: 20px; color: #333;">
        <div style="max-width: 600px; margin: auto; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden;">
            <div style="background: linear-gradient(135deg, #4f46e5, #6366f1); padding: 30px; text-align: center;">
                <h1 style="color: white; margin: 0;">TrustAgent</h1>
                <p style="color: #c7d2fe; margin-top: 8px;">Chào mừng Cộng tác viên mới!</p>
            </div>
            <div style="padding: 30px;">
                <h2 style="color: #1e293b;">Xin chào {fullname},</h2>
                <p>Chúc mừng bạn đã đăng ký thành công trở thành Cộng tác viên (CTV) của TrustAgent!</p>
                <p>Vui lòng truy cập <a href="{login_url}">{login_url}</a> để hoàn tất thông tin.</p>
                <p>Trân trọng,<br><strong>Đội ngũ TrustAgent</strong></p>
                <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;" />
                <p style="font-size: 12px; color: #94a3b8;">Email này được gửi tự động, vui lòng không trả lời.</p>
            </div>
        </div>
    </body>
    </html>
    """


def send_onboard_email(to_email: str, fullname: str) -> None:
    """Send onboarding welcome email to new CTV registration."""
    if not to_email or not settings.MAIL_USERNAME:
        logger.warning("Skipping email: no recipient or SMTP not configured")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Chào mừng {fullname} đến với TrustAgent!"
    msg["From"] = settings.MAIL_FROM
    msg["To"] = to_email

    html_content = _load_onboard_template(fullname)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.MAIL_HOST, settings.MAIL_PORT) as server:
            server.starttls()
            server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
            server.sendmail(settings.MAIL_FROM, to_email, msg.as_string())
        logger.info(f"Onboard email sent to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send onboard email to {to_email}: {e}")


# Keep backward-compatible alias
def send_welcome_email(to_email: str, fullname: str, register_code: str) -> None:
    """Backward-compatible wrapper — now sends the onboard template."""
    send_onboard_email(to_email, fullname)
