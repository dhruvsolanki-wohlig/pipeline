import asyncio
import sys
import os
import json
import subprocess
import requests
from typing import Optional
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Request, BackgroundTasks
# pyrefly: ignore [missing-import]
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
# pyrefly: ignore [missing-import]
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()

from api.settings_manager import load_settings, save_settings, check_schedule_action
from api.email_sender import send_email_with_pdf, generate_pdf

PIPELINE_DIR = Path(__file__).resolve().parent.parent
import tempfile

if os.environ.get("VERCEL"):
    TMP_DIR = tempfile.gettempdir()
    os.environ["DOWNLOAD_FOLDER"] = TMP_DIR
    os.environ["OUTPUT_JSON_FILE"] = f"{TMP_DIR}/all_files_extracted_data.json"
    os.environ["INPUT_FILE"] = f"{TMP_DIR}/all_files_extracted_data.json"
    os.environ["OUTPUT_FILE"] = f"{TMP_DIR}/workforce_analysis_output.json"
    REPORT_HTML = Path(TMP_DIR) / "workforce_report.html"
    os.environ["REPORT_HTML"] = str(REPORT_HTML)
    REPORT_PDF = Path(TMP_DIR) / "workforce_report.pdf"
else:
    REPORT_HTML = PIPELINE_DIR / "reports" / "workforce_report.html"
    REPORT_PDF = PIPELINE_DIR / "reports" / "workforce_report.pdf"

import io
from contextlib import redirect_stdout, redirect_stderr

def _run_pipeline_sync():
    """Run the Python pipeline directly."""
    if str(PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(PIPELINE_DIR))
        
    try:
        from drive_extract import run_extraction
        from llm_analysis import run_audit
        from report_service import generate_report
    except ImportError as e:
        return False, "", str(e)

    f_out = io.StringIO()
    f_err = io.StringIO()
    ok = False

    with redirect_stdout(f_out), redirect_stderr(f_err):
        try:
            run_extraction()
            run_audit()
            generate_report()
            ok = True
        except SystemExit:
            ok = False
        except Exception as e:
            import traceback
            traceback.print_exc(file=f_err)
            ok = False

    return ok, f_out.getvalue(), f_err.getvalue()


def _generate_report_pdf():
    if not REPORT_HTML.exists():
        ok, _, _ = _run_pipeline_sync()
        if not ok:
            return False
    if not REPORT_HTML.exists():
        return False
    ok = generate_pdf(str(REPORT_HTML), str(REPORT_PDF))
    return ok


def _scheduled_job():
    settings = load_settings()
    if not check_schedule_action(settings):
        return {"ran": False, "reason": "schedule_not_due"}

    # Generation Phase
    ok = _generate_report_pdf()
    if not ok:
        return {"ran": False, "reason": "pipeline_or_pdf_failed"}

    # Sending Phase
    recipients = settings.get("recipients", [])
    if not recipients:
        return {"ran": False, "reason": "no_recipients"}

    result = send_email_with_pdf(
        recipients=recipients,
        subject=settings.get("subject", "Company report"),
        body_line=settings.get("body_line", "Please find the attached company workforce report."),
        html_path=str(REPORT_HTML),
        pdf_path=str(REPORT_PDF),
    )
        
    # Reset state after sending
    settings["last_run"] = datetime.now().isoformat()

    # Calculate next_run
    if settings.get("continuous"):
        cron_expr = settings.get("cron_expression")
        if cron_expr:
            try:
                from croniter import croniter
                now_local = datetime.now()
                cron = croniter(cron_expr, now_local)
                next_t = cron.get_next(datetime)
                settings["next_run"] = next_t.isoformat()
            except Exception:
                pass
        else:
            interval = settings.get("interval_hours", 24)
            next_run = settings.get("next_run")
            if next_run:
                try:
                    dt = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
                    settings["next_run"] = (dt + timedelta(hours=interval)).isoformat()
                except Exception:
                    pass
    else:
        settings["active"] = False

    save_settings(settings)
    return {"ran": True, "email_result": result}


app = FastAPI(title="Pipeline API")

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "https://pipeline-three-flame.vercel.app",
    "https://*.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===========================
# Cron
# ===========================
CRON_SECRET = os.environ.get("CRON_SECRET", "")

@app.get("/api/cron")
async def handle_cron(request: Request):
    """Cron entrypoint — runs pipeline + sends email if schedule is due.
    WARNING: Pipeline takes 30-60s. Vercel free tier kills functions after 10s.
    Upgrade to Vercel Pro ($20/mo) for 60s timeout, or run locally."""
    if CRON_SECRET:
        auth = request.headers.get("Authorization", "")
        token = request.query_params.get("token", "")
        vercel_sig = request.headers.get("x-vercel-signature", "")
        valid = (
            auth == f"Bearer {CRON_SECRET}" or
            token == CRON_SECRET or
            vercel_sig == CRON_SECRET
        )
        if not valid:
            return JSONResponse(
                {"error": "Unauthorized", "detail": "Invalid or missing CRON_SECRET"},
                status_code=401
            )
    
    settings = load_settings()
    due = check_schedule_action(settings)
    
    if not due:
        return JSONResponse({
            "status": "ok",
            "ran": False,
            "reason": "schedule_not_due",
            "active": settings.get("active", False),
            "next_run": settings.get("next_run"),
            "continuous": settings.get("continuous", False),
        })
    
    # Run pipeline
    ok, stdout, stderr = _run_pipeline_sync()
    if not ok:
        return JSONResponse({
            "status": "error",
            "ran": False,
            "reason": "pipeline_failed",
            "error": stderr or stdout,
        }, status_code=500)
    
    # Send email
    recipients = settings.get("recipients", [])
    if not recipients:
        return JSONResponse({"status": "ok", "ran": True, "email_sent": False, "reason": "no_recipients"})
    
    result = send_email_with_pdf(
        recipients=recipients,
        subject=settings.get("subject", "Company Workforce Report"),
        body_line=settings.get("body_line", "Please find the attached report."),
        html_path=str(REPORT_HTML),
        pdf_path=str(REPORT_PDF),
    )
    
    # Update schedule state
    settings["last_run"] = datetime.now().isoformat()
    if settings.get("continuous"):
        interval = settings.get("interval_hours", 24)
        next_run = settings.get("next_run")
        if next_run:
            try:
                dt = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
                settings["next_run"] = (dt + timedelta(hours=interval)).isoformat()
            except Exception:
                pass
    else:
        settings["active"] = False
    
    save_settings(settings)
    
    return JSONResponse({
        "status": "ok",
        "ran": True,
        "email_sent": result.get("success", False),
        "email_result": result,
        "next_run": settings.get("next_run"),
        "active": settings.get("active"),
    })


# ===========================
# SendGrid Test — sends a simple test email, returns full API response
# ===========================
@app.post("/api/test-sendgrid")
def test_sendgrid():
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    from_addr = os.environ.get("SENDGRID_FROM", "")
    
    if not api_key:
        return JSONResponse({"error": "SENDGRID_API_KEY not set"}, status_code=400)
    if not from_addr:
        return JSONResponse({"error": "SENDGRID_FROM not set"}, status_code=400)
    
    settings = load_settings()
    recipients = settings.get("recipients", [])
    if not recipients:
        return JSONResponse({"error": "No recipients selected in Settings"}, status_code=400)
    
    payload = {
        "personalizations": [{"to": [{"email": r} for r in recipients]}],
        "from": {"email": from_addr},
        "subject": "SendGrid Test — Wohlig Pipeline",
        "content": [{"type": "text/plain", "value": "This is a test email. If you received this, SendGrid is configured correctly."}],
    }
    
    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    
    return {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "body": resp.text,
        "success": resp.status_code == 202,
        "from": from_addr,
        "to": recipients,
    }


# ===========================
# Debug — check what's missing
# ===========================
@app.get("/api/debug")
def debug():
    smtp_user = os.environ.get("SMTP_USER") or os.environ.get("EMAIL_USER", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD") or os.environ.get("EMAIL_PASSWORD", "")
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_from = os.environ.get("SMTP_FROM") or os.environ.get("EMAIL_FROM") or os.environ.get("EMAIL_USER") or smtp_user
    ollama_key = os.environ.get("OLLAMA_API_KEY", "")
    cron_secret = os.environ.get("CRON_SECRET", "")
    vercel = os.environ.get("VERCEL", "")
    
    report_exists = REPORT_HTML.exists()
    pdf_exists = REPORT_PDF.exists()
    
    settings = load_settings()
    
    return {
        "smtp_configured": bool(smtp_user and smtp_pass),
        "smtp_user_set": bool(smtp_user),
        "smtp_password_set": bool(smtp_pass),
        "smtp_host": smtp_host or "not set",
        "smtp_from": smtp_from or "not set",
        "ollama_key_set": bool(ollama_key),
        "cron_secret_set": bool(cron_secret),
        "is_vercel": bool(vercel),
        "report_html_exists": report_exists,
        "report_pdf_exists": pdf_exists,
        "report_html_path": str(REPORT_HTML),
        "report_pdf_path": str(REPORT_PDF),
        "settings_recipients": settings.get("recipients", []),
        "settings_active": settings.get("active", False),
        "settings_next_run": settings.get("next_run"),
    }


# ===========================
# Test Email — sends a simple test email (no pipeline)
# ===========================
@app.post("/api/test-email")
def test_email():
    """Send a simple test email to verify SMTP config works."""
    smtp_user = os.environ.get("SMTP_USER") or os.environ.get("EMAIL_USER", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD") or os.environ.get("EMAIL_PASSWORD", "")
    
    if not smtp_user or not smtp_pass:
        return JSONResponse({
            "success": False,
            "error": "EMAIL_USER or EMAIL_PASSWORD not set in Vercel environment variables.",
            "hint": "Go to Vercel Dashboard → Settings → Environment Variables and add EMAIL_USER, EMAIL_PASSWORD, SMTP_HOST, SMTP_PORT"
        }, status_code=400)
    
    settings = load_settings()
    recipients = settings.get("recipients", [])
    if not recipients:
        return JSONResponse({
            "success": False,
            "error": "No recipients selected. Open Settings in the dashboard and select email recipients first."
        }, status_code=400)
    
    import smtplib
    from email.mime.text import MIMEText
    
    cfg = {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": smtp_user,
        "password": smtp_pass,
        "from_addr": os.environ.get("SMTP_FROM") or os.environ.get("EMAIL_FROM") or smtp_user,
    }
    
    msg = MIMEText("This is a test email from the Wohlig Pipeline Dashboard.\n\nIf you received this, SMTP is configured correctly.")
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = "Test Email — Wohlig Pipeline"
    
    try:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
        server.starttls()
        server.login(cfg["user"], cfg["password"])
        server.sendmail(cfg["from_addr"], recipients, msg.as_string())
        server.quit()
        return {"success": True, "sent_to": recipients, "message": "SMTP works! Test email sent."}
    except smtplib.SMTPAuthenticationError as e:
        return JSONResponse({
            "success": False,
            "error": f"SMTP authentication failed: {e}",
            "hint": "Check that SMTP_USER and SMTP_PASSWORD are correct. For Gmail, use an App Password (not your regular password)."
        }, status_code=500)
    except smtplib.SMTPConnectError as e:
        return JSONResponse({
            "success": False,
            "error": f"Cannot connect to SMTP server: {e}",
            "hint": "Vercel may block outbound SMTP on port 587. Try port 465 with SMTP_SSL instead, or use a transactional email service like SendGrid."
        }, status_code=500)
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": f"SMTP error: {type(e).__name__}: {e}",
        }, status_code=500)


# ===========================
# Pipeline Runner (SSE)
# ===========================
@app.post("/api/run-pipeline")
async def run_pipeline(request: Request, background_tasks: BackgroundTasks):
    async def event_stream():
        stages = [
            ("drive_extract", "Fetching Excel from Google Drive"),
            ("llm_analysis", "LLM Analysis"),
            ("report_service", "Generating HTML Report"),
        ]

        for stage_id, stage_name in stages:
            yield f"data: {json.dumps({'stage': stage_id, 'status': 'running', 'message': stage_name})}\n\n"
            await asyncio.sleep(0.2)

        ok, stdout, stderr = _run_pipeline_sync()

        if not ok:
            yield f"data: {json.dumps({'stage': 'pipeline', 'status': 'failed', 'message': stderr or stdout})}\n\n"
            return

        # try to read generated html
        html_content = ""
        if REPORT_HTML.exists():
            with open(REPORT_HTML, "r", encoding="utf-8") as f:
                html_content = f.read()

        yield f"data: {json.dumps({'stage': 'pipeline', 'status': 'success', 'html': html_content})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ===========================
# Pipeline + Email (SSE) - The "Go" button uses this
# ===========================
class RunAndEmailPayload(BaseModel):
    recipients: list[str] = []
    subject: str = "Company Workforce Report"
    body_line: str = "Dear Team,\n\nPlease find the attached Company Workforce Report for your review."

@app.post("/api/run-and-email")
async def run_and_email(request: Request, payload: RunAndEmailPayload):
    """Run full pipeline, generate PDF, and send email in one shot.
    Settings are passed directly in the request body — no file dependency."""
    recipients = payload.recipients
    subject = payload.subject
    body_line = payload.body_line
    
    async def event_stream():
        stages = [
            ("drive_extract", "Fetching Excel from Google Drive"),
            ("llm_analysis", "LLM Analysis"),
            ("report_service", "Generating HTML Report"),
        ]

        for stage_id, stage_name in stages:
            yield f"data: {json.dumps({'stage': stage_id, 'status': 'running', 'message': stage_name})}\n\n"
            await asyncio.sleep(0.2)

        # Run pipeline
        ok, stdout, stderr = _run_pipeline_sync()

        if not ok:
            yield f"data: {json.dumps({'stage': 'pipeline', 'status': 'failed', 'message': stderr or stdout})}\n\n"
            return

        # Read generated html
        html_content = ""
        if REPORT_HTML.exists():
            with open(REPORT_HTML, "r", encoding="utf-8") as f:
                html_content = f.read()

        yield f"data: {json.dumps({'stage': 'pipeline', 'status': 'success', 'html': html_content})}\n\n"
        await asyncio.sleep(0.2)

        # Send email
        if not recipients:
            yield f"data: {json.dumps({'stage': 'email', 'status': 'failed', 'message': 'No recipients selected. Go to Settings to select email recipients.'})}\n\n"
            return

        yield f"data: {json.dumps({'stage': 'email', 'status': 'running', 'message': 'Sending email via Resend...'})}\n\n"

        result = send_email_with_pdf(
            recipients=recipients,
            subject=subject,
            body_line=body_line,
            html_path=str(REPORT_HTML),
            pdf_path=str(REPORT_PDF),
        )

        if result.get("success"):
            yield f"data: {json.dumps({'stage': 'email', 'status': 'success', 'message': f'Email sent to {', '.join(recipients)}'})}\n\n"
        else:
            yield f"data: {json.dumps({'stage': 'email', 'status': 'failed', 'message': result.get('error', 'Email sending failed')})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ===========================
# Report Serving
# ===========================
@app.get("/api/report")
def get_report():
    if not REPORT_HTML.exists():
        return JSONResponse({"detail": "Report not found. Run pipeline first."}, status_code=404)
    with open(REPORT_HTML, "r", encoding="utf-8") as f:
        content = f.read()
    return JSONResponse({"html": content})


@app.get("/api/report/download")
def download_report():
    if not REPORT_HTML.exists():
        return JSONResponse({"detail": "Report not found"}, status_code=404)
    return FileResponse(str(REPORT_HTML), media_type="text/html", filename="workforce_report.html")


# ===========================
# PDF
# ===========================
@app.post("/api/generate-pdf")
def generate_report_pdf_endpoint():
    ok = _generate_report_pdf()
    if ok:
        return {"success": True, "pdf_path": str(REPORT_PDF)}
    return JSONResponse({"success": False, "detail": "PDF generation failed"}, status_code=500)


@app.get("/api/pdf")
def download_pdf():
    if not REPORT_PDF.exists():
        return JSONResponse({"detail": "PDF not found"}, status_code=404)
    return FileResponse(str(REPORT_PDF), media_type="application/pdf", filename="workforce_report.pdf")


# ===========================
# Settings
# ===========================
class SettingsPayload(BaseModel):
    recipients: list[str] = []
    next_run: Optional[str] = None
    stop_run: Optional[str] = None
    continuous: bool = False
    active: bool = False
    subject: str = "Company report"
    body_line: str = "Please find the attached company workforce report."
    interval_hours: int = 24
    cron_expression: str = ""
    generation_done: bool = False


@app.get("/api/settings")
def get_settings():
    return load_settings()


@app.post("/api/settings")
def update_settings(payload: SettingsPayload):
    old_settings = load_settings()
    was_active = old_settings.get("active", False)
    
    settings = payload.dict()
    # Keep existing last_run
    settings["last_run"] = old_settings.get("last_run")
    
    if payload.active and not was_active:
        # Force immediate run
        settings["last_run"] = None
        
    save_settings(settings)
    return {"status": "ok", "settings": settings}


# ===========================
# Send Email
# ===========================
class SendEmailPayload(BaseModel):
    recipients: list[str] = []
    subject: str = "Company report"
    body_line: str = "Please find the attached company workforce report."


@app.post("/api/send-email")
def send_email_endpoint(payload: SendEmailPayload):
    if not _generate_report_pdf():
        return JSONResponse({"success": False, "detail": "PDF generation failed"}, status_code=500)
    result = send_email_with_pdf(
        recipients=payload.recipients,
        subject=payload.subject,
        body_line=payload.body_line,
        html_path=str(REPORT_HTML),
        pdf_path=str(REPORT_PDF),
    )
    return result


# ===========================
# Static Report View
# ===========================
@app.get("/")
def root():
    if REPORT_HTML.exists():
        return FileResponse(str(REPORT_HTML))
    return {"message": "Pipeline API is running. Use /api/run-pipeline to generate report."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
