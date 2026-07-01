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
        
    config_path = os.path.join(CONFIG_DIR, "config.ini")
    
    # Optional: Update API keys in config.ini if provided via request
    if (req.fmp_api_key and req.fmp_api_key.strip() and not req.fmp_api_key.startswith("YOUR_")) or \
       (req.openai_api_key and req.openai_api_key.strip() and not req.openai_api_key.startswith("YOUR_")):
        try:
            import configparser
            config = configparser.ConfigParser()
            if os.path.exists(config_path):
                config.read(config_path)
            else:
                config["API_KEYS"] = {}
            
            if "API_KEYS" not in config:
                config["API_KEYS"] = {}
                
            if req.fmp_api_key and req.fmp_api_key.strip() and not req.fmp_api_key.startswith("YOUR_"):
                config["API_KEYS"]["fmp_api_key"] = req.fmp_api_key.strip()
            if req.openai_api_key and req.openai_api_key.strip() and not req.openai_api_key.startswith("YOUR_"):
                config["API_KEYS"]["openai_api_key"] = req.openai_api_key.strip()
                
            with open(config_path, "w", encoding="utf-8") as f:
                config.write(f)
            logger.info("Updated config.ini with keys provided in analysis request")
        except Exception as e:
            logger.warning(f"Failed to update config.ini with request keys: {e}")

    python_exe = sys.executable
    analysis_script = os.path.join(PROJECT_ROOT, "core", "src", "generate_financial_analysis.py")
    report_script = os.path.join(PROJECT_ROOT, "core", "src", "create_equity_report.py")
    pdf_script = os.path.join(PROJECT_ROOT, "core", "src", "generate_pdf_report.py")

    # Determine paths
    analysis_dir = os.path.join(OUTPUT_DIR, req.ticker, "analysis")
    report_output_dir = os.path.join(OUTPUT_DIR, req.ticker, "report")
    os.makedirs(analysis_dir, exist_ok=True)
    os.makedirs(report_output_dir, exist_ok=True)

    # 1. Run Financial Analysis
    append_task_log(task_id, "--- Step 1: Starting Financial Analysis Pipeline ---")
    
    cmd1 = [
        python_exe,
        analysis_script,
        "--company-ticker", req.ticker,
        "--company-name", req.company_name,
        "--config-file", config_path,
        "--years-limit", str(req.years_limit),
        "--revenue-growth-2025", str(req.revenue_growth_2025),
        "--revenue-growth-2026", str(req.revenue_growth_2026),
        "--revenue-growth-2027", str(req.revenue_growth_2027),
        "--margin-improvement", str(req.margin_improvement),
    ]
    if req.peers:
        cmd1.extend(["--peer-tickers"] + req.peers)
    if req.generate_text:
        cmd1.append("--generate-text-sections")
    if req.enable_sensitivity_analysis:
        cmd1.append("--enable-sensitivity-analysis")
    if req.enable_catalyst_analysis:
        cmd1.append("--enable-catalyst-analysis")
    if req.enable_enhanced_news:
        cmd1.append("--enable-enhanced-news")

    success = run_process(cmd1, task_id, cwd=SRC_ROOT)
    if not success:
        tasks[task_id]["status"] = "failed"
        try:
            db = SessionLocal()
            crud.update_report_request(db, task_id, "failed")
            db.close()
        except Exception as e:
            logger.warning(f"Failed to update report status: {e}")
        return

    # Ensure all required text files exist (so create_equity_report.py doesn't crash)
    required_text_files = [
        "tagline", "company_overview", "investment_overview",
        "valuation_overview", "risks", "competitor_analysis", "major_takeaways"
    ]
    for text_type in required_text_files:
        file_path = os.path.join(analysis_dir, f"{text_type}.txt")
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"{req.company_name} ({req.ticker}) {text_type.replace('_', ' ')} details.")

    # 2. Run Report Generator (HTML)
    append_task_log(task_id, "--- Step 2: Generating HTML Equity Report ---")
    
    cmd2 = [
        python_exe,
        report_script,
        "--company-ticker", req.ticker,
        "--company-name", req.company_name,
        "--analysis-csv", os.path.join(analysis_dir, "financial_metrics_and_forecasts.csv"),
        "--ratios-csv", os.path.join(analysis_dir, "ratios_raw_data.csv"),
        "--tagline-file", os.path.join(analysis_dir, "tagline.txt"),
        "--company-overview-file", os.path.join(analysis_dir, "company_overview.txt"),
        "--investment-overview-file", os.path.join(analysis_dir, "investment_overview.txt"),
        "--valuation-overview-file", os.path.join(analysis_dir, "valuation_overview.txt"),
        "--risks-file", os.path.join(analysis_dir, "risks.txt"),
        "--competitor-analysis-file", os.path.join(analysis_dir, "competitor_analysis.txt"),
        "--major-takeaways-file", os.path.join(analysis_dir, "major_takeaways.txt"),
        "--config-file", config_path,
        "--output-dir", report_output_dir,
    ]
    
    optional_mappings = [
        ("news-summary-file", "news_summary.txt"),
        ("peer-ebitda-csv", "peer_ebitda_comparison.csv"),
        ("peer-ev-ebitda-csv", "peer_ev_ebitda_comparison.csv"),
        ("sensitivity-analysis-file", "sensitivity_analysis.json"),
        ("catalyst-analysis-file", "catalyst_analysis.json"),
        ("enhanced-news-file", "enhanced_news.json"),
        ("retail-sentiment-file", "retail_sentiment.json"),
    ]
    for param, filename in optional_mappings:
        file_path = os.path.join(analysis_dir, filename)
        if os.path.exists(file_path):
            cmd2.extend([f"--{param}", file_path])
            
    if req.enable_enhanced_charts:
        cmd2.append("--enable-enhanced-charts")
    if req.enable_valuation_analysis:
        cmd2.append("--enable-valuation-analysis")
    if req.generate_text:
        cmd2.append("--enable-text-regeneration")

    success = run_process(cmd2, task_id, cwd=SRC_ROOT)
    if not success:
        tasks[task_id]["status"] = "failed"
        try:
            db = SessionLocal()
            crud.update_report_request(db, task_id, "failed")
            db.close()
        except Exception as e:
            logger.warning(f"Failed to update report status: {e}")
        return

    # 3. Run PDF Report Generator (optional)
    if req.generate_pdf:
        append_task_log(task_id, "--- Step 3: Generating PDF Equity Report ---")
        cmd3 = [
            python_exe,
            pdf_script,
            "--company-ticker", req.ticker,
            "--company-name", req.company_name,
            "--analysis-dir", analysis_dir,
            "--output-dir", report_output_dir,
            "--config-file", config_path,
        ]
        success = run_process(cmd3, task_id, cwd=SRC_ROOT)
        if not success:
            tasks[task_id]["status"] = "failed"
            try:
                db = SessionLocal()
                crud.update_report_request(db, task_id, "failed")
                db.close()
            except Exception as e:
                logger.warning(f"Failed to update report status: {e}")
            return

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
        
    html_files = []
    pdf_files = []
    if os.path.exists(report_output_dir):
        all_files = os.listdir(report_output_dir)
        prof_html = [f for f in all_files if f.endswith('.html') and 'Professional' in f]
        other_html = [f for f in all_files if f.endswith('.html') and 'Professional' not in f] if not prof_html else []
        html_files = prof_html + other_html
        
        prof_pdf = [f for f in all_files if f.endswith('.pdf') and 'Professional_Equity_Report' in f]
        other_pdf = [f for f in all_files if f.endswith('.pdf') and 'Equity_Report' in f and f not in prof_pdf] if not prof_pdf else []
        pdf_files = prof_pdf + other_pdf

    tasks[task_id]["result"] = {
        "report_dir": report_output_dir,
        "ticker": req.ticker,
        "html": html_files,
        "pdf": pdf_files
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


# ============== Portfolio, Watchlist, Chat & Market Routes ==============
from .portfolio_engine import fetch_batch_quotes, fetch_market_indices, search_ticker, fetch_stock_quote
from .chat_engine import FinancialChatEngine
from .database.models import PortfolioHolding, WatchlistItem

chat_engine = FinancialChatEngine()

# --- Portfolio Holding Models ---
class HoldingCreate(BaseModel):
    symbol: str
    company_name: Optional[str] = ""
    shares: float
    buy_price: float
    buy_date: str
    market: Optional[str] = "US"

class WatchlistCreate(BaseModel):
    symbol: str
    company_name: Optional[str] = ""
    market: Optional[str] = "US"


# --- Portfolio Holdings ---
@app.get("/api/portfolio")
async def get_portfolio(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = SessionLocal()
    holdings = db.query(PortfolioHolding).filter(PortfolioHolding.user_id == user["id"]).all()
    result = []
    for h in holdings:
        result.append({
            "id": h.id,
            "symbol": h.symbol,
            "company_name": h.company_name or h.symbol,
            "shares": float(h.shares or 0),
            "buy_price": float(h.buy_price or 0),
            "buy_date": h.buy_date,
            "market": h.market or "US",
        })
    db.close()

    # Enrich with live quotes
    if result:
        try:
            symbols_by_market: Dict[str, List[str]] = {}
            for h in result:
                m = h["market"]
                symbols_by_market.setdefault(m, []).append(h["symbol"])

            quote_map: Dict[str, dict] = {}
            import asyncio
            for mkt, syms in symbols_by_market.items():
                quotes = await fetch_batch_quotes(syms, mkt)
                for q in quotes:
                    quote_map[q["symbol"]] = q

            for h in result:
                q = quote_map.get(h["symbol"], {})
                current_price = q.get("price", 0)
                h["current_price"] = current_price
                h["currency"] = q.get("currency", "$")
                h["name"] = q.get("name") or h["company_name"]
                h["changePercent"] = q.get("changePercent", 0)
                h["sparkline"] = q.get("sparkline", [])
                invested = h["shares"] * h["buy_price"]
                current_val = h["shares"] * current_price
                h["invested"] = invested
                h["current_value"] = current_val
                h["pnl"] = current_val - invested
                h["pnl_pct"] = ((current_val - invested) / invested * 100) if invested > 0 else 0
        except Exception as e:
            logger.warning(f"Failed to enrich portfolio with live quotes: {e}")

    return result


@app.post("/api/portfolio")
async def add_holding(body: HoldingCreate, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = SessionLocal()
    h = PortfolioHolding(
        user_id=user["id"],
        symbol=body.symbol.upper().strip(),
        company_name=body.company_name,
        shares=str(body.shares),
        buy_price=str(body.buy_price),
        buy_date=body.buy_date,
        market=body.market,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    db.close()
    return {"id": h.id, "symbol": h.symbol, "message": "Holding added"}


@app.delete("/api/portfolio/{holding_id}")
async def delete_holding(holding_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = SessionLocal()
    h = db.query(PortfolioHolding).filter(
        PortfolioHolding.id == holding_id,
        PortfolioHolding.user_id == user["id"]
    ).first()
    if not h:
        db.close()
        raise HTTPException(status_code=404, detail="Holding not found")
    db.delete(h)
    db.commit()
    db.close()
    return {"success": True}


# --- Watchlist ---
@app.get("/api/watchlist")
async def get_watchlist(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = SessionLocal()
    items = db.query(WatchlistItem).filter(WatchlistItem.user_id == user["id"]).all()
    result = [{"id": i.id, "symbol": i.symbol, "company_name": i.company_name or i.symbol, "market": i.market or "US"} for i in items]
    db.close()

    if result:
        try:
            symbols_by_market: Dict[str, List[str]] = {}
            for item in result:
                m = item["market"]
                symbols_by_market.setdefault(m, []).append(item["symbol"])

            quote_map: Dict[str, dict] = {}
            for mkt, syms in symbols_by_market.items():
                quotes = await fetch_batch_quotes(syms, mkt)
                for q in quotes:
                    quote_map[q["symbol"]] = q

            for item in result:
                q = quote_map.get(item["symbol"], {})
                item["price"] = q.get("price", 0)
                item["currency"] = q.get("currency", "$")
                item["change"] = q.get("change", 0)
                item["changePercent"] = q.get("changePercent", 0)
                item["volume"] = q.get("volume", "--")
                item["marketCap"] = q.get("marketCap", "--")
                item["name"] = q.get("name") or item["company_name"]
                item["sparkline"] = q.get("sparkline", [])
        except Exception as e:
            logger.warning(f"Failed to enrich watchlist with live quotes: {e}")

    return result


@app.post("/api/watchlist")
async def add_watchlist(body: WatchlistCreate, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = SessionLocal()
    item = WatchlistItem(
        user_id=user["id"],
        symbol=body.symbol.upper().strip(),
        company_name=body.company_name,
        market=body.market,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    db.close()
    return {"id": item.id, "symbol": item.symbol, "message": "Added to watchlist"}


@app.delete("/api/watchlist/{item_id}")
async def delete_watchlist(item_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = SessionLocal()
    item = db.query(WatchlistItem).filter(
        WatchlistItem.id == item_id,
        WatchlistItem.user_id == user["id"]
    ).first()
    if not item:
        db.close()
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    db.close()
    return {"success": True}


# --- Market Indices ---
@app.get("/api/market/indices")
async def get_market_indices(market: str = "US", request: Request = None):
    if request:
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        indices = await fetch_market_indices(market)
        return indices
    except Exception as e:
        logger.warning(f"Failed to fetch market indices: {e}")
        return []


# --- Symbol Search ---
@app.get("/api/market/search")
async def search_market(q: str, market: str = "US", request: Request = None):
    if request:
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        results = await search_ticker(q, market)
        return results
    except Exception as e:
        logger.warning(f"Symbol search failed: {e}")
        return []


# --- AI Chat (streaming SSE) ---
from fastapi.responses import StreamingResponse
import asyncio

@app.get("/api/chat/stream")
async def chat_stream(message: str, history_json: str = "[]", market: str = "US", request: Request = None):
    if request:
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        history = json.loads(history_json) if history_json else []
    except Exception:
        history = []

    async def event_generator():
        try:
            async for chunk in chat_engine.chat_stream(message, history, market):
                data = json.dumps({"content": chunk})
                yield f"data: {data}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'content': f'Error: {e}'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")