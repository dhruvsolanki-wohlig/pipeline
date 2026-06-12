import os
import smtplib
import subprocess
import base64
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path


def _smtp_config():
    user = os.environ.get("SMTP_USER") or os.environ.get("EMAIL_USER", "")
    password = os.environ.get("SMTP_PASSWORD") or os.environ.get("EMAIL_PASSWORD", "")
    from_addr = os.environ.get("SMTP_FROM") or os.environ.get("EMAIL_FROM") or os.environ.get("EMAIL_USER") or user
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": user,
        "password": password,
        "from_addr": from_addr,
    }


def generate_pdf(html_path: str, pdf_path: str) -> bool:
    chrome_candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for chrome in chrome_candidates:
        if Path(chrome).exists():
            try:
                cmd = [
                    chrome,
                    "--headless",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--print-to-pdf-no-header",
                    f"--print-to-pdf={pdf_path}",
                    html_path,
                ]
                subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)
                if Path(pdf_path).exists() and Path(pdf_path).stat().st_size > 0:
                    return True
            except Exception:
                continue

    try:
        import weasyprint
        weasyprint.HTML(filename=html_path).write_pdf(pdf_path)
        return True
    except Exception:
        pass

    try:
        import pdfkit
        pdfkit.from_file(html_path, pdf_path)
        return True
    except Exception:
        pass

    return False


def _send_via_sendgrid(recipients, subject, body_line, html_path, pdf_path):
    """Send email via SendGrid Web API v3 (HTTPS, works on Vercel)."""
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    from_addr = os.environ.get("SENDGRID_FROM", "")

    if not api_key:
        return None
    if not from_addr:
        return {"success": False, "error": "SENDGRID_FROM not set", "method": "sendgrid"}

    attachments = []
    if Path(pdf_path).exists():
        with open(pdf_path, "rb") as f:
            attachments.append({
                "content": base64.b64encode(f.read()).decode(),
                "filename": "Company_Report.pdf",
                "type": "application/pdf",
                "disposition": "attachment",
            })
    if Path(html_path).exists():
        with open(html_path, "rb") as f:
            attachments.append({
                "content": base64.b64encode(f.read()).decode(),
                "filename": "Workforce_Report.html",
                "type": "text/html",
                "disposition": "attachment",
            })

    payload = {
        "personalizations": [{"to": [{"email": r} for r in recipients]}],
        "from": {"email": from_addr},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body_line}],
        "attachments": attachments,
    }

    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code == 202:
            return {"success": True, "sent_to": recipients, "method": "sendgrid"}
        return {"success": False, "error": f"SendGrid API error ({resp.status_code}): {resp.text}", "method": "sendgrid"}
    except Exception as e:
        return {"success": False, "error": f"SendGrid failed: {e}", "method": "sendgrid"}


def _send_via_smtp(recipients, subject, body_line, html_path, pdf_path):
    """Send email via SMTP (works locally, blocked on Vercel)."""
    cfg = _smtp_config()
    if not cfg["user"] or not cfg["password"]:
        return {"success": False, "error": "EMAIL_USER or EMAIL_PASSWORD not set", "method": "smtp"}

    msg = MIMEMultipart()
    msg["From"] = cfg["from_addr"] or cfg["user"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_line, "plain"))

    has_pdf = False
    if Path(html_path).exists():
        has_pdf = generate_pdf(html_path, pdf_path)

    if has_pdf and Path(pdf_path).exists():
        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", 'attachment; filename="Company_Report.pdf"')
        msg.attach(part)
    elif Path(html_path).exists():
        with open(html_path, "rb") as f:
            part = MIMEBase("text", "html")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", 'attachment; filename="Company_Report.html"')
        msg.attach(part)

    try:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
        server.starttls()
        server.login(cfg["user"], cfg["password"])
        server.sendmail(cfg["from_addr"] or cfg["user"], recipients, msg.as_string())
        server.quit()
        return {"success": True, "sent_to": recipients, "method": "smtp"}
    except smtplib.SMTPAuthenticationError as e:
        return {"success": False, "error": f"SMTP auth failed: {e}", "method": "smtp"}
    except smtplib.SMTPConnectError as e:
        return {"success": False, "error": f"SMTP connect failed: {e}. Vercel blocks outbound SMTP.", "method": "smtp"}
    except Exception as e:
        return {"success": False, "error": f"SMTP error: {type(e).__name__}: {e}", "method": "smtp"}


def send_email_with_pdf(
    recipients: list[str],
    subject: str,
    body_line: str,
    html_path: str,
    pdf_path: str,
) -> dict:
    if not recipients:
        return {"success": False, "error": "No recipients selected"}

    # 1. Try SendGrid first (HTTPS, works on Vercel)
    result = _send_via_sendgrid(recipients, subject, body_line, html_path, pdf_path)
    if result and result.get("success"):
        return result

    # 2. Fall back to SMTP (works locally)
    smtp_result = _send_via_smtp(recipients, subject, body_line, html_path, pdf_path)
    if smtp_result.get("success"):
        return smtp_result

    errors = []
    if result:
        errors.append(f"SendGrid: {result.get('error', 'unknown')}")
    errors.append(f"SMTP: {smtp_result.get('error', 'unknown')}")
    return {"success": False, "error": " | ".join(errors)}
