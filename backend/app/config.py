import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from typing import List

# Base workspace path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DOTENV_PATH = BASE_DIR / ".env"
if DOTENV_PATH.exists():
    load_dotenv(dotenv_path=DOTENV_PATH, override=True)

class Settings(BaseSettings):
    # App
    APP_NAME: str = "StreamVault Media Server"
    DEBUG: bool = False
    
    # Server Binding
    MEDIA_SERVER_HOST: str = "127.0.0.1"
    MEDIA_SERVER_PORT: int = 8000
    
    # Directories
    MEDIA_INCOMING_DIR: str = str(BASE_DIR / "media" / "incoming")
    MEDIA_LIBRARY_DIR: str = str(BASE_DIR / "media" / "library")
    MEDIA_TEMP_DIR: str = str(BASE_DIR / "media" / "temp")
    
    # Security
    HMAC_STREAM_SECRET: str = ""
    STREAM_TOKEN_TTL_SECONDS: int = 14400 # 4 hours
    ADMIN_API_SECRET: str = ""
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:4000,http://127.0.0.1:5173,http://127.0.0.1:4000"
    STREAM_PUBLIC_BASE_URL: str = ""
    
    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""
    
    # Hardware & Transcoding Safety
    DEFAULT_TRANSCODING_ENABLED: bool = False
    MAX_TRANSCODE_THREADS: int = 4
    HW_ACCEL_PREFERENCE: str = "auto"
    
    # Metadata APIs
    TMDB_API_READ_ACCESS_TOKEN: str = ""
    TMDB_API_KEY: str = ""
    OMDB_API_KEY: str = ""
    
    # Aria2 RPC
    ARIA2_RPC_HOST: str = "127.0.0.1"
    ARIA2_RPC_PORT: int = 6800
    ARIA2_RPC_SECRET: str = ""
    ARIA2_RPC_SSL: bool = False
    ARIA2_DOWNLOAD_DIR: str = str(BASE_DIR / "media" / "incoming")

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()

# Ensure runtime directories exist
for path_str in [settings.MEDIA_INCOMING_DIR, settings.MEDIA_LIBRARY_DIR, settings.MEDIA_TEMP_DIR]:
    Path(path_str).mkdir(parents=True, exist_ok=True)
