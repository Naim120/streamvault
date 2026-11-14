# StreamVault 🎬

> A high-performance, self-hostable, Netflix-style streaming platform with adaptive HLS video streaming, multi-track audio & subtitles, instantaneous playback resume, and automated metadata ingestion.

---

## ✨ Features

- **Netflix-Style User Experience:**
  - Dynamic Hero Billboard with trailer previews, priority controls, and badges (*Recently Added*, *New Season*).
  - Categorized Rails (*Continue Watching*, *Recently Added*, *Most Watched*, *Most Liked*, *Critics' Picks*).
  - Detailed modal with season/episode selector, backdrop banners, creator credits, and cast members.
  - Sub-millisecond instant search with PostgreSQL GIN trigram matching and typo tolerance.

- **High-Performance Video Player:**
  - HLS (`hls.js`) adaptive multi-bitrate streaming with segment pre-warming.
  - Multi-track AAC/E-AC-3 audio track switching with language badges.
  - Embedded and external `.vtt` subtitle support.
  - Instantaneous server-side resume: loads directly at your last watched segment without re-fetching playlist chunks or buffering from `0:00`.
  - Next-episode auto-play countdown for TV series.

- **Automated Media Ingestion & Transcoding:**
  - Zero-reencode remux pipeline for H.264/AAC media (near-instant processing).
  - Hardware-accelerated transcode worker supporting Intel VAAPI, AMD AMF, and Nvidia NVENC.
  - Background file watcher monitoring `media/incoming` for dropped files.
  - Automatic TMDB and OMDb metadata scraping with poster/backdrop caching.

- **Real-Time Telemetry & Admin Dashboard:**
  - Decoupled Local Admin Panel bound securely to `127.0.0.1:4000`.
  - Real-time KPIs: active users online, current viewers, total watch hours, and site engagement.
  - Bulk media ingestion modal with per-file metadata mapping, season assignment, and audio track selection.
  - Built-in Aria2c background download manager for remote torrent and direct-link ingestion.

- **Enterprise-Grade Security:**
  - Row Level Security (RLS) on all PostgreSQL tables.
  - HMAC-SHA256 signed, time-limited video playback tokens (`.m3u8` access tokens).
  - Dual-stack IPv4/IPv6 login logging with anti-spoofing telemetry heartbeat verification.
  - Server-side rate limiting on transactional email verification.

---

## 🛠️ Tech Stack

| Layer | Technologies |
|---|---|
| **Frontend** | React 19, TypeScript, Vite, Tailwind CSS, Lucide React, Hls.js, TanStack Query |
| **Backend** | Python 3.12+, FastAPI, Uvicorn, Pydantic, FFmpeg, FFprobe, Watchdog, HTTPX |
| **Database & Auth** | Supabase (PostgreSQL 15+, Supabase Auth, Row Level Security, pg_trgm, UUID-OSSP) |
| **Gateway & Server** | Nginx (reverse proxy, byte-range caching, CORS handling, single-origin routing) |
| **Download Daemon** | Aria2c RPC daemon |

---

## 🚀 Quick Start

### 1. Prerequisites
- Linux server (Debian/Ubuntu recommended) or macOS
- Python 3.12 or higher
- Node.js 20+ and npm
- FFmpeg and FFprobe installed (`sudo apt install ffmpeg`)
- Nginx installed (`sudo apt install nginx`)
- A free [Supabase](https://supabase.com) account

### 2. Database Initialization
1. Create a project in [Supabase](https://supabase.com).
2. Open the **SQL Editor** in the Supabase Dashboard.
3. Copy the contents of [`supabase/setup.sql`](./supabase/setup.sql), paste into the SQL editor, and click **Run**.

### 3. Environment Configuration
1. Clone the repository and copy the environment template:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` and fill in your Supabase project credentials and generated secrets:
   ```bash
   # Supabase Credentials
   VITE_SUPABASE_URL=https://your-project-id.supabase.co
   VITE_SUPABASE_ANON_KEY=your_supabase_anon_key_here
   SUPABASE_URL=https://your-project-id.supabase.co
   SUPABASE_ANON_KEY=your_supabase_anon_key_here
   SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key_here

   # Generate secrets with openssl:
   HMAC_STREAM_SECRET=$(openssl rand -hex 32)
   ADMIN_API_SECRET=$(openssl rand -hex 32)
   ARIA2_RPC_SECRET=$(openssl rand -hex 16)
   ```

### 4. Running the Platform
StreamVault provides a unified process manager script:

```bash
# Start all services (FastAPI on 8000, Admin on 4000, Gateway on 8080, Aria2 on 6800):
./start.sh

# Stop all services:
./start.sh stop

# Restart all services:
./start.sh restart
```

Once started:
- **Public Streaming Gateway:** `http://localhost:8080` (or `http://<server-ip>:8080`)
- **Local Admin Dashboard:** `http://127.0.0.1:4000`

---

## 📁 Repository Structure

```
streamvault/
├── admin-local/          # Local management dashboard (Python http.server + Tailwind)
│   ├── modules/          # Admin pages (Media Ingestion, Telemetry, Metadata)
│   └── server.py         # Admin server entrypoint (port 4000)
├── backend/              # FastAPI media streaming & transcoder backend
│   ├── app/
│   │   ├── api/          # Endpoints (Stream token, Metadata, Telemetry, Auth)
│   │   ├── transcoder/   # Remux/Transcode pipeline & worker
│   │   └── config.py     # Pydantic settings & environment binding
│   └── Dockerfile        # Container recipe for media server
├── frontend/             # React 19 Netflix-style web application
│   ├── src/
│   │   ├── components/   # Billboard, VideoPlayer, DetailModal, Search, Rails
│   │   ├── hooks/        # Watch progress, telemetry heartbeat, search hooks
│   │   └── lib/          # Supabase client, stream pre-warming, error handling
│   └── package.json
├── media/                # Storage directories (incoming, library, temp)
├── nginx/                # Nginx configuration templates
├── supabase/
│   └── setup.sql         # Master idempotent database replication script
├── docker-compose.yml    # Docker Compose multi-service deployment
├── install.sh            # Automated server installer script
├── start.sh              # Service runner & background daemon orchestrator
└── README.md
```

---

## 🛡️ License

This project is licensed under the [MIT License](LICENSE).

