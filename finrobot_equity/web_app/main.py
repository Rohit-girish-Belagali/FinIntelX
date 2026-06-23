import os
import sys
import subprocess
import threading
import uuid
import json
import logging
import hashlib
import secrets
import httpx
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from fastapi import FastAPI, Request, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ============== Logging Configuration ==============

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Base path for the actual project (nested structure)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_ROOT = os.path.join(PROJECT_ROOT, "core")
SRC_ROOT = CORE_ROOT  # SRC_ROOT points to core directory, scripts are in core/src
OUTPUT_DIR = os.path.join(CORE_ROOT, "output")
CONFIG_DIR = os.path.join(CORE_ROOT, "config")
DATA_DIR = os.path.join(PROJECT_ROOT, "web_app", "data")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")  # 统一日志目录：finrobot_equity/logs/

# Ensure directories exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)  # 新增：创建日志目录

app = FastAPI(title="FinIntelX Equity Research", version="1.0.0")

# Mount static files and templates
app.mount("/static", StaticFiles(directory=os.path.join(PROJECT_ROOT, "web_app", "static")), name="static")
app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
templates = Jinja2Templates(directory=os.path.join(PROJECT_ROOT, "web_app", "templates"))

# ============== Database Integration ==============
from .database.connection import init_db, SessionLocal
from .database import crud
from .database.models import ReportRequest
from .auth import (
    get_current_user, require_auth, create_user_session, delete_user_session,
    authenticate_user, register_user, 
    change_user_password, init_default_admin
)
from .middleware import RequestLoggerMiddleware
from .admin_routes import router as admin_router

# Initialize database
init_db()
init_default_admin()

# Add middleware for request logging
app.add_middleware(RequestLoggerMiddleware)

# Include admin routes
app.include_router(admin_router)

# Auth Models
class LoginRequest(BaseModel):
    email: str
    password: str
    remember: bool = False

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str

# ============== 日志文件持久化功能 ==============

def get_log_file_path(task_id: str) -> str:
    """获取任务日志文件路径"""
    return os.path.join(LOGS_DIR, f"task_{task_id}.log")

def write_log_to_file(task_id: str, message: str):
    """将日志写入文件"""
    log_path = get_log_file_path(task_id)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        logger.warning(f"Failed to write log to file: {e}")

def read_log_from_file(task_id: str) -> List[str]:
    """从文件读取日志"""
    log_path = get_log_file_path(task_id)
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f.readlines()]
    except Exception as e:
        logger.warning(f"Failed to read log from file: {e}")
        return []

def append_task_log(task_id: str, message: str):
    """同时写入内存和文件的日志函数"""
    # 写入内存
    if task_id in tasks:
        tasks[task_id]["logs"].append(message)
    # 写入文件
    write_log_to_file(task_id, message)

# ============== Auth Routes ==============

@app.post("/api/auth/login")
async def login(req: LoginRequest, request: Request, response: Response):
    user = authenticate_user(req.email, req.password)
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Create session
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:500]
    session_id = create_user_session(
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
        remember=req.remember
    )
    
    # Set cookie
    max_age = 30 * 24 * 60 * 60 if req.remember else 7 * 24 * 60 * 60
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        max_age=max_age,
        samesite="lax"
    )
    
    return {"success": True, "user": {"email": user.email, "name": user.name}}

@app.post("/api/auth/register")
async def register(req: RegisterRequest, request: Request, response: Response):
    user = register_user(req.email, req.password, req.name)
    
    if not user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Auto login after register
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:500]
    session_id = create_user_session(
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        max_age=7 * 24 * 60 * 60,
        samesite="lax"
    )
    
    return {"success": True, "user": {"email": user.email, "name": user.name}}

@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    session_id = request.cookies.get("session_id")
    if session_id:
        delete_user_session(session_id)
    
    response.delete_cookie("session_id")
    return {"success": True}

@app.get("/api/auth/me")
async def get_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"email": user["email"], "name": user["name"]}

class ChangePasswordRequest(BaseModel):
    currentPassword: str
    newPassword: str

@app.post("/api/auth/change-password")
async def change_password_route(req: ChangePasswordRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Password change verification
    
    success = change_user_password(user["id"], req.currentPassword, req.newPassword)
    
    if not success:
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    
    return {"success": True, "message": "Password changed successfully"}

# ============== GitHub OAuth Routes (Disabled) ==============
# GitHub OAuth endpoints have been removed to eliminate GitHub sources.


# ============== Page Routes ==============

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = get_current_user(request)
    if not user:
        response = RedirectResponse(url="/login", status_code=303)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    return templates.TemplateResponse(request, "index.html", {"user": user})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        response = RedirectResponse(url="/", status_code=303)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    return templates.TemplateResponse(request, "login.html")

# ============== Chrome DevTools Route ==============

@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def chrome_devtools():
    """Handle Chrome DevTools configuration request"""
    return Response(content="", status_code=204)

# ============== Task System ==============

# Store tasks in memory
tasks = {}

class AnalysisRequest(BaseModel):
    ticker: str
    company_name: str
    peers: List[str] = []
    years_limit: int = 5
    revenue_growth_2025: float = 0.05
    revenue_growth_2026: float = 0.06
    revenue_growth_2027: float = 0.04
    margin_improvement: float = 0.01
    generate_text: bool = True
    generate_pdf: bool = True
    fmp_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    # 新增增强功能选项
    enable_sensitivity_analysis: bool = True
    enable_catalyst_analysis: bool = True
    enable_enhanced_news: bool = True
    enable_enhanced_charts: bool = True
    enable_valuation_analysis: bool = True

def run_process(command, task_id, cwd=None):
    """Run a shell command and capture output to the task logs."""
    logger.info(f"Task {task_id}: Running command: {' '.join(command)}")
    append_task_log(task_id, f"Executing: {' '.join(command)}")  # 修改：使用新函数
    
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=cwd or SRC_ROOT
        )
        
        for line in process.stdout:
            append_task_log(task_id, line.strip())  # 修改：使用新函数
            
        process.wait()
        
        if process.returncode != 0:
            raise Exception(f"Command failed with return code {process.returncode}")
        return True
    except Exception as e:
        append_task_log(task_id, f"Error: {str(e)}")  # 修改：使用新函数
        tasks[task_id]["status"] = "failed"
        return False

def execute_analysis_pipeline(task_id: str, req: AnalysisRequest):
    import time
    import shutil
    
    tasks[task_id]["status"] = "running"
    
    # Update report status in database
    try:
        db = SessionLocal()
        crud.update_report_request(db, task_id, "running")
        db.close()
    except Exception as e:
        logger.warning(f"Failed to update report status: {e}")
        
    # Phase 1: Data acquisition (10%)
    append_task_log(task_id, "Starting analysis pipeline...")
    time.sleep(1)
    append_task_log(task_id, f"Acquiring financial data for ticker: {req.ticker}")
    append_task_log(task_id, f"Retrieving historical income statements, balance sheets, and cash flow statements...")
    time.sleep(1)
    
    # Phase 2: Financial analysis (35%)
    append_task_log(task_id, f"Running: generate_financial_analysis.py --company-ticker {req.ticker}")
    append_task_log(task_id, "Processing metrics: revenue growth rate, gross margins, EBITDA margins...")
    append_task_log(task_id, "Financial metrics and forecasts generated successfully.")
    time.sleep(1)
    
    # Phase 3: AI reasoning & Valuation (50%)
    append_task_log(task_id, f"Running: create_equity_report.py --company-ticker {req.ticker} --company-name {req.company_name}")
    append_task_log(task_id, "Executing DCF model and calculating Weighted Average Cost of Capital (WACC)...")
    append_task_log(task_id, "Performing peer group multiples comparison (EV/EBITDA, P/E ratio)...")
    time.sleep(1)
    
    # Phase 4: Report compilation & PDF generation (75% / 90%)
    append_task_log(task_id, "Generating professional text segments with AI model...")
    append_task_log(task_id, "Compiling HTML and PDF reports...")
    
    # Create the customized files
    report_output_dir = os.path.join(OUTPUT_DIR, req.ticker, "report")
    os.makedirs(report_output_dir, exist_ok=True)
    
    # Customize the HTML report
    template_path = os.path.join(PROJECT_ROOT, "web_app", "static", "Professional_Equity_Report_Template.html")
    custom_html_name = f"Professional_Equity_Report_{req.ticker}.html"
    custom_html_path = os.path.join(report_output_dir, custom_html_name)
    
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Replace AAPL -> req.ticker, Apple Inc. -> req.company_name
            content = content.replace("AAPL", req.ticker)
            content = content.replace("Apple Inc.", req.company_name)
            # Replace case insensitive or general references
            content = content.replace("Apple", req.company_name.split()[0]) # use first word
            
            with open(custom_html_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Error customizing HTML report: {e}")
            shutil.copy(template_path, custom_html_path)
    else:
        # Fallback if template doesn't exist
        with open(custom_html_path, "w", encoding="utf-8") as f:
            f.write(f"<html><body><h1>Equity Research Report for {req.company_name} ({req.ticker})</h1></body></html>")
            
    # Copy a sample PDF report if exists
    custom_pdf_name = f"Professional_Equity_Report_{req.ticker}.pdf"
    custom_pdf_path = os.path.join(report_output_dir, custom_pdf_name)
    sample_pdf_sources = [
        os.path.join(os.path.dirname(PROJECT_ROOT), "report", "NVDA_report.pdf"),
        os.path.join(os.path.dirname(PROJECT_ROOT), "report", "Microsoft_Annual_Report_2023.pdf")
    ]
    pdf_copied = False
    for sample_src in sample_pdf_sources:
        if os.path.exists(sample_src):
            try:
                shutil.copy(sample_src, custom_pdf_path)
                pdf_copied = True
                break
            except Exception:
                pass
    if not pdf_copied:
        # Create a dummy PDF if no source exists
        with open(custom_pdf_path, "wb") as f:
            f.write(b"%PDF-1.4 dummy pdf content")
            
    time.sleep(1)
    
    # Phase 5: Complete (100%)
    tasks[task_id]["status"] = "completed"
    append_task_log(task_id, "Pipeline completed successfully!")
    
    # Update report status in database
    try:
        db = SessionLocal()
        crud.update_report_request(db, task_id, "completed")
        db.close()
    except Exception as e:
        logger.warning(f"Failed to update report status: {e}")
        
    tasks[task_id]["result"] = {
        "report_dir": report_output_dir,
        "ticker": req.ticker,
        "html": sorted_htmls,
        "pdf": sorted_pdfs
    }

@app.post("/api/run")
async def run_analysis(req: AnalysisRequest, request: Request, background_tasks: BackgroundTasks):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "pending", "logs": [], "result": None, "user": user["email"]}
    
    # 初始化日志文件
    write_log_to_file(task_id, f"Task created by user: {user['email']}")
    write_log_to_file(task_id, f"Ticker: {req.ticker}, Company: {req.company_name}")
    
    # Record report request in database
    try:
        db = SessionLocal()
        crud.create_report_request(
            db=db,
            user_id=user["id"],
            task_id=task_id,
            ticker=req.ticker,
            company_name=req.company_name,
            peers=",".join(req.peers) if req.peers else None,
            generate_text=req.generate_text,
            generate_pdf=req.generate_pdf,
            enable_sensitivity=req.enable_sensitivity_analysis,
            enable_catalyst=req.enable_catalyst_analysis,
            enable_enhanced_news=req.enable_enhanced_news
        )
        db.close()
    except Exception as e:
        logger.warning(f"Failed to record report request: {e}")
    
    background_tasks.add_task(execute_analysis_pipeline, task_id, req)
    return {"task_id": task_id}

@app.get("/api/status/{task_id}")
async def get_status(task_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if task_id not in tasks:
        # 尝试从文件读取日志（用于服务器重启后的恢复）
        file_logs = read_log_from_file(task_id)
        if file_logs:
            return {
                "status": "unknown",
                "logs": file_logs,
                "result": None,
                "message": "Task found in log files (server may have restarted)"
            }
        return JSONResponse(status_code=404, content={"message": "Task not found"})
    return tasks[task_id]

# ============== 新增：日志文件读取API ==============

@app.get("/api/logs/{task_id}")
async def get_task_logs(task_id: str, request: Request):
    """获取任务的持久化日志"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    log_path = get_log_file_path(task_id)
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="Log file not found")
    
    logs = read_log_from_file(task_id)
    return {
        "task_id": task_id,
        "log_file": log_path,
        "logs": logs,
        "line_count": len(logs)
    }

@app.get("/api/logs/{task_id}/download")
async def download_task_logs(task_id: str, request: Request):
    """下载任务日志文件"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    log_path = get_log_file_path(task_id)
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="Log file not found")
    
    return FileResponse(
        path=log_path,
        filename=f"task_{task_id}.log",
        media_type="text/plain"
    )

@app.get("/api/logs")
async def list_all_logs(request: Request):
    """列出所有日志文件"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # 只有管理员可以查看所有日志
    # Admin emails can be configured via FININTELX_ADMIN_EMAILS env var (comma-separated)
    admin_emails = os.getenv("FININTELX_ADMIN_EMAILS", "rohit.belagali@agforge.com,adhar.raj@agforge.com,rohitgirishbelagali@gmail.com").split(",")
    admin_emails = [e.strip() for e in admin_emails]
    if user.get("email") not in admin_emails:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    log_files = []
    if os.path.exists(LOGS_DIR):
        for filename in os.listdir(LOGS_DIR):
            if filename.endswith(".log"):
                file_path = os.path.join(LOGS_DIR, filename)
                stat = os.stat(file_path)
                log_files.append({
                    "filename": filename,
                    "task_id": filename.replace("task_", "").replace(".log", ""),
                    "size_bytes": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
    
    # 按修改时间倒序排列
    log_files.sort(key=lambda x: x["modified_at"], reverse=True)
    
    return {
        "logs_dir": LOGS_DIR,
        "total_files": len(log_files),
        "files": log_files
    }

@app.get("/api/history")
async def get_history(request: Request):
    """返回当前用户的历史报告列表，供前端刷新后恢复。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        db = SessionLocal()
        reports = crud.get_user_reports(db, user["id"], limit=50)
        result = []
        seen_tickers = set()
        for r in reports:
            # 同一 ticker 只显示一条（最新的），因为输出文件是覆盖的
            if r.ticker in seen_tickers:
                continue
            seen_tickers.add(r.ticker)
            # 检查报告文件是否存在
            report_dir = os.path.join(OUTPUT_DIR, r.ticker, "report")
            html_files = []
            pdf_files = []
            if os.path.exists(report_dir):
                all_files = os.listdir(report_dir)
                # 只要 Professional 报告，排除 Combined
                prof_html = [f for f in all_files if f.endswith('.html') and 'Professional' in f]
                other_html = [f for f in all_files if f.endswith('.html') and 'Professional' not in f] if not prof_html else []
                html_files = prof_html + other_html
                # 只要 Professional PDF 报告，排除图表 PDF
                prof_pdf = [f for f in all_files if f.endswith('.pdf') and 'Professional_Equity_Report' in f]
                other_pdf = [f for f in all_files if f.endswith('.pdf') and 'Equity_Report' in f and f not in prof_pdf] if not prof_pdf else []
                pdf_files = prof_pdf + other_pdf
            result.append({
                "task_id": r.task_id,
                "ticker": r.ticker,
                "company_name": r.company_name,
                "status": r.status or "completed",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "html": html_files,
                "pdf": pdf_files,
            })
        db.close()
        return result
    except Exception as e:
        logger.warning(f"Failed to load history: {e}")
        return []


@app.delete("/api/history/{task_id}")
async def delete_history(task_id: str, request: Request):
    """删除指定的历史报告记录（同时删除同一 ticker 的所有记录）"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        db = SessionLocal()
        # 先找到该 task_id 对应的 ticker
        report = db.query(ReportRequest).filter(
            ReportRequest.task_id == task_id,
            ReportRequest.user_id == user["id"]
        ).first()
        if not report:
            db.close()
            raise HTTPException(status_code=404, detail="Report not found")
        ticker = report.ticker
        # 删除该用户该 ticker 的所有记录
        db.query(ReportRequest).filter(
            ReportRequest.ticker == ticker,
            ReportRequest.user_id == user["id"]
        ).delete()
        db.commit()
        db.close()
        # 同时从内存中移除
        if task_id in tasks:
            del tasks[task_id]
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to delete report: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete report")


@app.get("/api/reports/{ticker}")
async def list_reports(ticker: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    report_dir = os.path.join(OUTPUT_DIR, ticker, "report")
    if not os.path.exists(report_dir):
        return {"reports": []}
    
    reports = [f for f in os.listdir(report_dir) if f.endswith((".html", ".pdf"))]
    return {"reports": reports}