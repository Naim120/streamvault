import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from fastapi import Request, HTTPException, Response, Query, Header, File, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import httpx

# Add root directory to python path and load root .env
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DOTENV_PATH = BASE_DIR / ".env"
if DOTENV_PATH.exists():
    load_dotenv(dotenv_path=DOTENV_PATH, override=True)

if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from backend.app.config import settings
from backend.app.database import db

MEDIA_SERVER_URL = os.getenv("MEDIA_SERVER_INTERNAL_URL", f"http://127.0.0.1:{settings.MEDIA_SERVER_PORT}")
ADMIN_SECRET = (
    getattr(settings, "ADMIN_API_SECRET", "")
    or os.getenv("ADMIN_API_SECRET", "")
    or os.getenv("ADMIN_SECRET", "")
).strip()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

class MediaUpdatePayload(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    type: Optional[str] = None
    release_year: Optional[int] = None
    maturity_rating: Optional[str] = None
    runtime_minutes: Optional[int] = None
    synopsis: Optional[str] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    trailer_youtube_key: Optional[str] = None
    genres: Optional[List[str]] = None
    audio_languages: Optional[List[str]] = None
    badge: Optional[str] = None
    badge_duration_days: Optional[int] = None
    badge_expires_at: Optional[str] = None
    is_published: Optional[bool] = None
    is_featured_billboard: Optional[bool] = None
    billboard_priority: Optional[int] = None

class BillboardPayload(BaseModel):
    is_featured_billboard: bool = False
    exclusive: bool = True
    billboard_priority: Optional[int] = 100

