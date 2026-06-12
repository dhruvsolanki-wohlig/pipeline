import asyncio
import sys
import os
import json
from typing import Optional
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()

from api.settings_manager import load_settings, save_settings, check_schedule_action
from api.email_sender import send_email_with_pdf, generate_pdf

PIPELINE_DIR = Path(__file__).resolve().parent.parent
REPORT_HTML = PIPELINE_DIR / "reports" / "workforce_report.html"
REPORT_PDF = PIPELINE_DIR / "reports" / "workforce_report.pdf"

import io
from contextlib import redirect_stdout, redirect_stderr

def _run_pipeline_sync():
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
        except Exception:
            import traceback
            traceback.print_exc(file=f_err)
            ok = False

    return ok, f_out.getvalue(), f_err.getvalue()


def _scheduled_job():
    settings = load_settings()
    if not check_schedule_action(settings):
        return

    ok, _, _ = _run_pipeline_sync()
    if not ok:
        return

    recipients = settings.get("recipients", [])
    if not recipients:
        return

    send_email_with_pdf(
        recipients=recipients,
        subject=settings.get("subject", "Company Workforce Report"),
        body_line=settings.get("body_line", "Please find the attached report."),
        html_path=str(REPORT_HTML),
        pdf_path=str(REPORT_PDF),
    )

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


scheduler = BackgroundScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    scheduler.add_job(_scheduled_job, "interval", minutes=1, id="poll_schedule")
    yield
    scheduler.shutdown()

app = FastAPI(title="Pipeline API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ──
@app.get("/health")
def health():
    return {"status": "ok"}


# ── Pipeline Runner (SSE) ──
@app.post("/api/run-pipeline")
async def run_pipeline(request: Request):
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

        html_content = ""
        if REPORT_HTML.exists():
            with open(REPORT_HTML, "r", encoding="utf-8") as f:
                html_content = f.read()
        yield f"data: {json.dumps({'stage': 'pipeline', 'status': 'success', 'html': html_content})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Pipeline + Email (Go button) ──
class RunAndEmailPayload(BaseModel):
    recipients: list[str] = []
    subject: str = "Company Workforce Report"
    body_line: str = "Dear Team,\n\nPlease find the attached Company Workforce Report for your review."

@app.post("/api/run-and-email")
async def run_and_email(request: Request, payload: RunAndEmailPayload):
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

        ok, stdout, stderr = _run_pipeline_sync()
        if not ok:
            yield f"data: {json.dumps({'stage': 'pipeline', 'status': 'failed', 'message': stderr or stdout})}\n\n"
            return

        html_content = ""
        if REPORT_HTML.exists():
            with open(REPORT_HTML, "r", encoding="utf-8") as f:
                html_content = f.read()
        yield f"data: {json.dumps({'stage': 'pipeline', 'status': 'success', 'html': html_content})}\n\n"
        await asyncio.sleep(0.2)

        if not recipients:
            yield f"data: {json.dumps({'stage': 'email', 'status': 'failed', 'message': 'No recipients selected.'})}\n\n"
            return

        yield f"data: {json.dumps({'stage': 'email', 'status': 'running', 'message': 'Sending email via SMTP...'})}\n\n"

        result = send_email_with_pdf(
            recipients=recipients,
            subject=subject,
            body_line=body_line,
            html_path=str(REPORT_HTML),
            pdf_path=str(REPORT_PDF),
        )

        if result.get("success"):
            yield f"data: {json.dumps({'stage': 'email', 'status': 'success', 'message': f'Email sent to {", ".join(recipients)}'})}\n\n"
        else:
            yield f"data: {json.dumps({'stage': 'email', 'status': 'failed', 'message': result.get('error', 'Email sending failed')})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Report ──
@app.get("/api/report")
def get_report():
    if not REPORT_HTML.exists():
        return JSONResponse({"detail": "Report not found. Run pipeline first."}, status_code=404)
    with open(REPORT_HTML, "r", encoding="utf-8") as f:
        content = f.read()
    return JSONResponse({"html": content})


# ── Settings ──
class SettingsPayload(BaseModel):
    recipients: list[str] = []
    next_run: Optional[str] = None
    stop_run: Optional[str] = None
    continuous: bool = False
    active: bool = False
    subject: str = "Company report"
    body_line: str = "Please find the attached company workforce report."
    interval_hours: int = 24

@app.get("/api/settings")
def get_settings():
    return load_settings()

@app.post("/api/settings")
def update_settings(payload: SettingsPayload):
    old_settings = load_settings()
    was_active = old_settings.get("active", False)
    settings = payload.dict()
    settings["last_run"] = old_settings.get("last_run")
    if payload.active and not was_active:
        settings["last_run"] = None
    save_settings(settings)
    return {"status": "ok", "settings": settings}


# ── Root ──
@app.get("/")
def root():
    if REPORT_HTML.exists():
        return FileResponse(str(REPORT_HTML))
    return {"message": "Pipeline API is running."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
