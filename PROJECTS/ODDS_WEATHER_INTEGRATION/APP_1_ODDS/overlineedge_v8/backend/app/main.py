import logging
import logging.handlers
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router

# ============================================================
# ADDED: persistent, shareable server log file
# ============================================================
# Every log line the server prints (including startup, request info,
# AND the asyncio "Future exception was never retrieved" tracebacks that
# show up when a scrape fails) now ALSO gets written to a plain text file
# on disk automatically. No more copy-pasting terminal output — just grab
# the file and share it directly.
#
#   backend/data/logs/server.log
#
# It rotates automatically at 5 MB (keeps 3 backups) so it never grows
# unbounded.
LOG_DIR = Path("data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = LOG_DIR / "server.log"

_file_handler = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
))

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
))

logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])

app = FastAPI(title="OverlineEdge v8")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
app.include_router(router)

@app.get("/")
async def root():
    return {"service":"overlineedge_v8","status":"running"}
