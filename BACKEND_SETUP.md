# StreamVault Backend & Database Setup Guide

This guide walks you through setting up the Supabase PostgreSQL database and running the StreamVault media server and admin dashboard on a fresh Linux server.

---

## 1. Supabase PostgreSQL Database Setup

1. Create a new project on [Supabase](https://supabase.com).
2. Open the **SQL Editor** in your Supabase project dashboard.
3. Open `supabase/setup.sql` from this repository, copy its contents, paste them into the SQL editor, and click **Run**.
4. The setup script will automatically:
   - Enable required extensions (`uuid-ossp`, `pg_trgm`, `unaccent`).
   - Create enums (`media_type_enum`, `badge_type_enum`, `processing_status_enum`).
   - Create all application tables (`profiles`, `media_items`, `seasons`, `episodes`, `media_streams`, `watch_progress`, `watch_history`, `user_likes`, `custom_lists`, `custom_list_items`, `user_login_logs`, `auth_email_rate_limits`, `system_settings`).
   - Create high-performance GIN trigram indexes for sub-millisecond search and billboard ordering.
   - Configure Row Level Security (RLS) policies and security-definer RPC functions (`get_home_feed`, `sync_watch_progress`, `search_media`, `record_user_telemetry_heartbeat`, `admin_inspect_email_auth_state`, `get_platform_telemetry_kpis`).
   - Attach auto-provisioning triggers on `auth.users` for new profile creation.

---

## 2. Environment Configuration

1. In the repository root, copy the template configuration:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and fill in your actual credentials:
   - `VITE_SUPABASE_URL`: Your Supabase Project URL (`https://<project-ref>.supabase.co`).
   - `VITE_SUPABASE_ANON_KEY`: Your Supabase Public Anon Key.
   - `SUPABASE_URL`: Your Supabase Project URL.
   - `SUPABASE_ANON_KEY`: Your Supabase Public Anon Key.
   - `SUPABASE_SERVICE_ROLE_KEY`: Your Supabase Secret Service Role Key (kept strictly confidential on the server).
   - `HMAC_STREAM_SECRET`: Generate a secure random hex key:
     ```bash
     openssl rand -hex 32
     ```
   - `ADMIN_API_SECRET`: Generate a secure random hex key:
     ```bash
     openssl rand -hex 32
     ```
   - `ARIA2_RPC_SECRET`: Generate a random hex key:
     ```bash
     openssl rand -hex 16
     ```
   - `TMDB_API_READ_ACCESS_TOKEN`: (Optional) Free Read Access Token from [TMDB](https://www.themoviedb.org/settings/api).
   - `OMDB_API_KEY`: (Optional) Free API key from [OMDb API](https://www.omdbapi.com/apikey.aspx).

---

## 3. Automated Server Installation

For Debian/Ubuntu systems, you can use the automated installer script:
```bash
chmod +x install.sh
./install.sh
```
The installer installs system dependencies (`ffmpeg`, `nginx`, `aria2`, `python3-venv`), creates Python and Node virtual environments, builds the frontend, and initializes system services.

---

## 4. Starting StreamVault Services

To start all services (FastAPI on 8000, Local Admin on 4000, Nginx Gateway on 8080, and Aria2c daemon on 6800):
```bash
chmod +x start.sh
./start.sh
```

To stop all services:
```bash
./start.sh stop
```

To restart services:
```bash
./start.sh restart
```

---

## 5. Architecture Overview

- **Nginx Single-Origin Gateway (Port 8080):** Reverse proxies frontend static files, the `/api` endpoints to FastAPI, and HLS `.m3u8` / `.ts` stream segments with HTTP byte-range caching.
- **FastAPI Media Server (Port 8000):** Validates HMAC playback tokens, resolves resume points, coordinates transcoder workers, and powers metadata APIs.
- **Local Admin Panel (Port 4000):** Bound to `127.0.0.1` for local management. Provides file ingestion, batch remuxing, queue inspection, telemetry KPIs, and database sync.
- **Supabase Cloud (PostgreSQL + Auth):** Handles user authentication, watch progress sync, user favorites, and catalog metadata.

