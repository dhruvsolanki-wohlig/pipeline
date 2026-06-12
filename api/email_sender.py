import os
import smtplib
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path


def _smtp_config():
    user = os.environ.get("EMAIL_USER", "")
    password = os.environ.get("EMAIL_PASSWORD", "")
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": user,
        "password": password,
        "from_addr": user,
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


def send_email_with_pdf(
    recipients: list[str],
    subject: str,
    body_line: str,
    html_path: str,
    pdf_path: str,
) -> dict:
    if not recipients:
        return {"success": False, "error": "No recipients selected"}

    cfg = _smtp_config()
    if not cfg["user"] or not cfg["password"]:
        return {"success": False, "error": "EMAIL_USER or EMAIL_PASSWORD not set in .env"}

    msg = MIMEMultipart()
    msg["From"] = cfg["from_addr"]
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
        server.sendmail(cfg["from_addr"], recipients, msg.as_string())
        server.quit()
        return {"success": True, "sent_to": recipients}
    except smtplib.SMTPAuthenticationError as e:
        return {"success": False, "error": f"SMTP auth failed: {e}. Check EMAIL_USER/EMAIL_PASSWORD in .env"}
    except Exception as e:
        return {"success": False, "error": f"SMTP error: {type(e).__name__}: {e}"}
