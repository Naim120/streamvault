import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, File, UploadFile, Form
from fastapi.responses import JSONResponse
import httpx
from .common import settings, MEDIA_SERVER_URL, ADMIN_SECRET

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/api/admin/incoming-files/{filename:path}/probe")
async def probe_incoming_file_proxy(filename: str):
    """Proxy route to Media Server to probe streams of an incoming video file"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/incoming-files/{filename}/probe"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.post("/api/admin/incoming/upload-subtitle")
async def upload_incoming_subtitle_proxy(
    file: UploadFile = File(...),
    target_filename: str = Form(...),
    lang: Optional[str] = Form("en"),
    label: Optional[str] = Form(None)
):
    url = f"{MEDIA_SERVER_URL}/api/metadata/incoming/upload-subtitle"
    MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
    file_bytes = await file.read(MAX_SUBTITLE_BYTES + 1)
    if len(file_bytes) > MAX_SUBTITLE_BYTES:
        raise HTTPException(status_code=413, detail="Subtitle file exceeds maximum limit of 5 MB")
    files = {"file": (file.filename, file_bytes, file.content_type or "text/plain")}
    data = {"target_filename": target_filename}
    if lang: data["lang"] = lang
    if label: data["label"] = label
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers={"X-Admin-Secret": ADMIN_SECRET}, data=data, files=files)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.delete("/api/admin/incoming/external-subtitle")
async def delete_incoming_external_subtitle_proxy(target_filename: str = Query(...), ext_id: str = Query(...)):
    url = f"{MEDIA_SERVER_URL}/api/metadata/incoming/external-subtitle"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.delete(url, headers={"X-Admin-Secret": ADMIN_SECRET}, params={"target_filename": target_filename, "ext_id": ext_id})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.post("/api/system/transcode/cancel")
async def cancel_transcode_proxy():
    try:
        headers = {}
        if ADMIN_SECRET:
            headers["X-Admin-Secret"] = ADMIN_SECRET
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{MEDIA_SERVER_URL}/api/system/transcode/cancel", headers=headers)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as e:
        from backend.app.transcoder.worker import transcode_queue
        res = await transcode_queue.cancel_active_job()
        return res


@router.delete("/api/metadata/incoming-file/{filename:path}")
async def proxy_delete_incoming_file(filename: str):
    try:
        headers = {}
        if ADMIN_SECRET:
            headers["X-Admin-Secret"] = ADMIN_SECRET
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.delete(f"{MEDIA_SERVER_URL}/api/metadata/incoming-file/{filename}", headers=headers)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as e:
        from pathlib import Path
        if Path(filename).is_absolute() or ".." in Path(filename).parts:
            raise HTTPException(status_code=403, detail="Invalid file path")
        incoming = Path(settings.MEDIA_INCOMING_DIR).resolve()
        target = (incoming / filename).resolve()
        if target.is_symlink() or not target.is_relative_to(incoming):
            raise HTTPException(status_code=403, detail="Invalid file path")
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        size_mb = round(target.stat().st_size / (1024 * 1024), 2)
        target.unlink()
        return {"status": "deleted", "filename": filename, "reclaimed_mb": size_mb}


@router.post("/api/admin/bulk-delete")
async def proxy_bulk_delete(payload: Dict[str, Any]):
    """Proxy route to Media Server to bulk delete raw files, library streams, and DB records"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/bulk-delete"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers={"X-Admin-Secret": ADMIN_SECRET}, json=payload)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.get("/api/admin/incoming-files")
async def proxy_get_incoming_files():
    """Proxy route to Media Server to list incoming media files"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/incoming-files"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                logger.warning(f"Media server returned {resp.status_code} on incoming-files: {resp.text}")
                return []
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.RequestError as e:
            logger.warning(f"Failed to connect to Media Server for incoming-files: {e}")
            return []


@router.post("/api/admin/auto-ingest")
async def proxy_auto_ingest(payload: Dict[str, Any]):
    """Proxy route to Media Server to auto-ingest a file"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/auto-ingest"
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(url, headers={"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"}, json=payload)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.post("/api/admin/bulk-ingest")
async def proxy_bulk_ingest(payload: Dict[str, Any]):
    """Proxy route to Media Server to bulk ingest files"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/bulk-ingest"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers={"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"}, json=payload)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.delete("/api/admin/transcode/queue/{identifier:path}")
@router.delete("/api/system/transcode/queue/{identifier:path}")
async def proxy_cancel_queued_transcode(identifier: str):
    """Proxy route to Media Server to cancel an individual queued transcode job"""
    url = f"{MEDIA_SERVER_URL}/api/system/transcode/queue/{identifier}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.delete(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.post("/api/admin/transcode/queue/clear")
@router.post("/api/system/transcode/queue/clear")
async def proxy_clear_queued_transcodes():
    """Proxy route to Media Server to cancel and clear all waiting queued transcode jobs"""
    url = f"{MEDIA_SERVER_URL}/api/system/transcode/queue/clear"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")




MEDIA_HTML = r"""      <!-- TAB 3: MEDIA INGESTION & TRANSCODING QUEUE -->
      <section id="tab-media" class="space-y-6 hidden">
        <!-- VIDEO INGESTION & INCOMING WATCHER (Dropzone & Scanner Header) -->
        <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <div class="flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse"></span>
                <h3 class="text-sm font-bold text-white uppercase tracking-wider">Video Ingestion &amp; Incoming Watcher</h3>
              </div>
              <p class="text-xs text-neutral-400 mt-0.5">Files dropped into <code class="bg-[#1c1c1c] text-neutral-300 px-1.5 py-0.5 rounded font-mono text-[11px]">media/incoming</code> are scanned and prepared for HLS VOD transcoding.</p>
            </div>
            <div class="flex items-center gap-2">
              <span id="transcoding-badge" class="px-2.5 py-1 rounded-lg text-xs font-bold uppercase tracking-wider bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                Transcoding Active
              </span>
            </div>
          </div>
        </div>

        <!-- 2-COLUMN RESPONSIVE LAYOUT -->
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          <!-- LEFT COLUMN (lg:col-span-8): INCOMING FILES -->
          <div class="lg:col-span-8 space-y-4">
            <!-- Filter Tabs + Refresh Button -->
            <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-sm">
              <div class="flex items-center gap-2 flex-wrap">
                <button type="button" id="tab-btn-pending" onclick="setIncomingTab('pending')" class="px-3.5 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-1.5 bg-red-600/20 text-red-400 border border-red-500/40 cursor-pointer">
                  <span>⏳ Pending Ingest</span>
                  <span id="badge-pending-count" class="px-1.5 py-0.2 rounded-full bg-red-600 text-white text-[10px] font-mono font-bold">0</span>
                </button>
                <button type="button" id="tab-btn-published" onclick="setIncomingTab('published')" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition flex items-center gap-1.5 border border-transparent cursor-pointer">
                  <span>✓ Published in Library</span>
                  <span id="badge-published-count" class="px-1.5 py-0.2 rounded-full bg-neutral-800 text-neutral-300 text-[10px] font-mono font-bold">0</span>
                </button>
                <button type="button" id="tab-btn-all" onclick="setIncomingTab('all')" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition flex items-center gap-1.5 border border-transparent cursor-pointer">
                  <span>📁 All Incoming Files</span>
                  <span id="badge-all-count" class="px-1.5 py-0.2 rounded-full bg-neutral-800 text-neutral-300 text-[10px] font-mono font-bold">0</span>
                </button>
                <button type="button" id="tab-btn-queue" onclick="setIncomingTab('queue')" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition flex items-center gap-1.5 border border-transparent cursor-pointer">
                  <span>🕒 Ingest Queue</span>
                  <span id="badge-queue-count" class="px-1.5 py-0.2 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[10px] font-mono font-bold">0</span>
                </button>
              </div>
              <div class="flex items-center gap-2">
                <button type="button" onclick="fetchIncomingFiles()" class="px-3 py-1.5 rounded-lg text-xs font-semibold text-neutral-400 hover:text-white bg-[#181818] hover:bg-neutral-800 border border-neutral-800 hover:border-neutral-700 transition flex items-center gap-1.5 cursor-pointer">
                  <svg class="w-3.5 h-3.5 text-neutral-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                  <span>Refresh Folder</span>
                </button>
              </div>
            </div>

            <!-- Search & Page Size Bar (PART 3) -->
            <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-3 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5">
              <div class="relative flex-1">
                <input
                  type="text"
                  id="incoming-search-input"
                  oninput="onIncomingSearchInput()"
                  placeholder="Search files in current tab by filename, title, or S01E01..."
                  class="w-full bg-[#181818] border border-neutral-700 focus:border-red-500 rounded-xl px-3.5 py-2 text-xs text-white placeholder-neutral-500 focus:outline-none transition"
                >
                <button type="button" onclick="clearIncomingSearch()" class="absolute right-3 top-2 text-neutral-500 hover:text-white text-sm cursor-pointer">&times;</button>
              </div>
              <div class="flex items-center gap-2">
                <select id="incoming-page-size" onchange="onIncomingPageSizeChange(this.value)" class="bg-[#181818] border border-neutral-700 text-neutral-300 text-xs rounded-xl px-2.5 py-2 focus:outline-none focus:border-red-500 font-mono cursor-pointer">
                  <option value="10" selected>10 / page</option>
                  <option value="20">20 / page</option>
                  <option value="50">50 / page</option>
                  <option value="100">100 / page</option>
                </select>
              </div>
            </div>

            <!-- Contextual Bulk Action Bar (Hidden when active tab has 0 files) -->
            <div id="contextual-bulk-bar" class="hidden bg-[#181818] border border-neutral-800 rounded-2xl p-4 space-y-3.5 transition-all shadow-lg">
              <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div class="flex items-center gap-3">
                  <input type="checkbox" id="select-all-incoming" onchange="toggleSelectAllIncoming(this)" class="w-4 h-4 rounded accent-red-600 cursor-pointer">
                  <label for="select-all-incoming" class="text-xs font-bold text-white cursor-pointer select-none">
                    Select All (<span id="bulk-selected-count">0 of 0 on page selected</span>)
                  </label>
                </div>
                <div class="flex items-center gap-2">
                  <div class="relative inline-block" id="bulk-action-dropdown-wrap">
                    <button
                      type="button"
                      id="btn-bulk-action"
                      disabled
                      onclick="toggleBulkActionMenu(event)"
                      class="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-neutral-800 disabled:text-neutral-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl text-xs font-bold transition flex items-center gap-2 shadow-lg cursor-pointer"
                    >
                      <span id="btn-bulk-action-text">Bulk Action</span>
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                    </button>

                    <!-- Dropdown Menu: 2 Options (Ingest/Re-Ingest & Delete) -->
                    <div
                      id="menu-bulk-action"
                      class="hidden absolute right-0 mt-2 w-56 bg-[#161616] border border-neutral-700 rounded-xl shadow-2xl p-1.5 z-50 space-y-1"
                    >
                      <button
                        type="button"
                        id="btn-bulk-opt-ingest"
                        onclick="closeBulkActionMenu(); openBulkIngestModal();"
                        class="w-full text-left px-3 py-2 rounded-lg text-xs font-bold text-white hover:bg-red-600/20 hover:text-red-400 transition flex items-center justify-between cursor-pointer"
                      >
                        <span id="label-bulk-opt-ingest">🚀 Ingest Selected</span>
                        <span id="count-bulk-opt-ingest" class="font-mono text-[10px] px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-300">0</span>
                      </button>
                      <button
                        type="button"
                        id="btn-bulk-opt-delete"
                        onclick="closeBulkActionMenu(); bulkDeleteSelected();"
                        class="w-full text-left px-3 py-2 rounded-lg text-xs font-bold text-red-400 hover:bg-red-950/60 hover:text-red-300 transition flex items-center justify-between cursor-pointer"
                      >
                        <span>🗑️ Delete Selected</span>
                        <span id="count-bulk-opt-delete" class="font-mono text-[10px] px-1.5 py-0.5 rounded bg-red-950/80 text-red-300">0</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Incoming Files List -->
            <div id="incoming-files-list" class="space-y-3 text-xs">
              <div class="text-neutral-500 text-xs py-1">Scanning incoming files...</div>
            </div>

            <!-- Pagination Footer (PART 3) -->
            <div id="incoming-pagination-footer" class="bg-[#141414] border border-neutral-800 rounded-2xl px-4 py-3 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-neutral-400">
              <div id="incoming-pagination-info" class="font-mono text-[11px]">Showing 0 to 0 of 0 files</div>
              <div class="flex items-center gap-1.5 font-mono text-xs">
                <button type="button" id="btn-inc-first" onclick="changeIncomingPage('first')" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800 cursor-pointer">&laquo;</button>
                <button type="button" id="btn-inc-prev" onclick="changeIncomingPage(-1)" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800 cursor-pointer">&lsaquo; Prev</button>
                <span id="incoming-page-display" class="px-3 py-1.5 rounded-lg bg-black border border-neutral-800 text-white font-bold text-[11px]">Page 1 of 1</span>
                <button type="button" id="btn-inc-next" onclick="changeIncomingPage(1)" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800 cursor-pointer">Next &rsaquo;</button>
                <button type="button" id="btn-inc-last" onclick="changeIncomingPage('last')" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800 cursor-pointer">&raquo;</button>
              </div>
            </div>
          </div>

          <!-- RIGHT COLUMN (lg:col-span-4 lg:sticky lg:top-6): STATIC CONTROL SIDEBAR -->
          <div class="lg:col-span-4 lg:sticky lg:top-6 space-y-4">
            
            <!-- Card 1: Live Transcoding Status & Queue Card -->
            <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5 space-y-3.5 shadow-xl transition-all duration-300">
              <div class="flex items-center justify-between pb-2.5 border-b border-neutral-800">
                <div class="flex items-center gap-2.5">
                  <span id="queue-status-dot" class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>
                  <div>
                    <h4 id="queue-status-title" class="text-xs font-bold text-white flex items-center gap-2">Engine Idle — Ready</h4>
                    <p id="queue-status-sub" class="text-[10px] text-neutral-400">Sequential processor</p>
                  </div>
                </div>
                <button type="button" id="queue-badge" onclick="setIncomingTab('queue')" class="px-2.5 py-1 bg-neutral-800 text-neutral-400 hover:text-white hover:bg-neutral-700 rounded-lg text-[10px] font-mono font-bold cursor-pointer transition">0 in queue</button>
              </div>

              <!-- Animated Progress Bar Container -->
              <div id="queue-progress-container" class="space-y-2 hidden">
                <div class="flex items-center justify-between text-xs font-mono">
                  <span id="queue-job-title" class="font-bold text-white truncate max-w-[200px]">Processing...</span>
                  <span id="queue-progress-pct" class="text-red-400 font-extrabold text-xs">0%</span>
                </div>
                <div class="w-full h-2.5 bg-black rounded-full overflow-hidden border border-neutral-800">
                  <div id="queue-progress-bar" class="h-full bg-gradient-to-r from-red-600 via-amber-500 to-emerald-500 transition-all duration-300" style="width: 0%"></div>
                </div>
                <p id="queue-job-stage" class="text-[10px] text-neutral-400 font-mono truncate">Stage: Probing media streams...</p>
              </div>

              <!-- Cancel Active Job Button (Prominent Red Button) -->
              <div id="cancel-job-container" class="hidden pt-1">
                <button type="button" id="btn-cancel-active-job" onclick="cancelActiveTranscodeJob()" class="w-full py-2.5 px-3 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white font-bold text-xs rounded-xl transition flex items-center justify-center gap-2 shadow-lg shadow-red-900/40 cursor-pointer">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                  <span>✕ Cancel Active Job</span>
                </button>
              </div>

              <!-- Compact Queue Preview (up to 3 waiting jobs with individual cancel buttons) -->
              <div id="sidebar-queue-preview" class="hidden pt-2 border-t border-neutral-800/80 space-y-2">
                <div class="flex items-center justify-between text-[11px] font-bold text-neutral-400">
                  <span class="flex items-center gap-1.5">
                    <span>Waiting Queue</span>
                    <span id="sidebar-queue-count" class="px-1.5 py-0.2 rounded-full bg-neutral-800 text-neutral-300 text-[10px] font-mono">0</span>
                  </span>
                  <button type="button" onclick="setIncomingTab('queue')" class="text-[10px] text-red-400 hover:text-red-300 transition cursor-pointer">View All →</button>
                </div>
                <div id="sidebar-queue-list" class="space-y-1.5"></div>
              </div>
            </div>

            <!-- Card 2: Live Hardware & ABR Resource Allocator Card -->
            <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5 space-y-4 shadow-xl">
              <div class="flex items-center justify-between pb-2.5 border-b border-neutral-800">
                <div class="flex items-center gap-2">
                  <svg class="w-4 h-4 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"></path></svg>
                  <h4 class="text-xs font-bold text-white uppercase tracking-wider">Live Resource Allocator</h4>
                </div>
                <span id="side-governor-badge" class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">Active</span>
              </div>

              <!-- ABR Mode Switch -->
              <div class="space-y-1.5">
                <label class="text-[10px] font-bold text-neutral-400 uppercase tracking-wider">Transcoding Pipeline Mode</label>
                <div class="grid grid-cols-2 gap-1.5 bg-[#181818] p-1 rounded-xl border border-neutral-800">
                  <button type="button" id="side-mode-remux" onclick="setTranscodeMode('lossless_remux')" class="py-1.5 px-2 rounded-lg text-[11px] font-bold transition flex items-center justify-center gap-1 cursor-pointer bg-neutral-800 text-white">
                    <span>Lossless Remux</span>
                  </button>
                  <button type="button" id="side-mode-abr" onclick="setTranscodeMode('abr_transcode')" class="py-1.5 px-2 rounded-lg text-[11px] font-semibold text-neutral-400 hover:text-white transition flex items-center justify-center gap-1 cursor-pointer">
                    <span>Multi-Bitrate ABR</span>
                  </button>
                </div>
                <p class="text-[10px] text-neutral-500 leading-tight" id="side-mode-desc">Mode B: Zero-CPU stream copy for 8-bit H.264.</p>
              </div>

              <!-- CPU Core Allocation Slider -->
              <div class="space-y-1.5">
                <div class="flex items-center justify-between text-[11px]">
                  <label class="text-[10px] font-bold text-neutral-400 uppercase tracking-wider">CPU Core Allocation</label>
                  <span id="side-cpu-val" class="font-mono text-emerald-400 font-bold text-[11px]">2 / 8 Cores (~35%)</span>
                </div>
                <input type="range" id="side-cpu-slider" min="1" max="8" value="2" oninput="onCpuSliderInput(this.value)" onchange="applyCpuSliderChange(this.value)" class="w-full accent-red-600 cursor-pointer bg-neutral-800 h-1.5 rounded-lg">
                <div class="flex items-center justify-between text-[10px] text-neutral-500 font-mono">
                  <span>1 Core</span>
                  <span id="side-cpu-max-label">8 Cores</span>
                </div>
              </div>

              <!-- RAM Buffer Limit Selector -->
              <div class="space-y-1.5">
                <label class="text-[10px] font-bold text-neutral-400 uppercase tracking-wider">FFmpeg RAM Buffer Limit</label>
                <select id="side-ram-select" onchange="applyRamLimit(this.value)" class="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-red-500 transition cursor-pointer font-mono">
                  <option value="512">512 MB (Ultra-Light)</option>
                  <option value="1024">1024 MB (1 GB)</option>
                  <option value="2048">2048 MB (2 GB — Recommended)</option>
                  <option value="4096">4096 MB (4 GB)</option>
                  <option value="8192">8192 MB (8 GB)</option>
                </select>
              </div>

              <!-- GPU Hardware Offload Toggle -->
              <div class="pt-2 border-t border-neutral-800 flex items-center justify-between gap-3">
                <div>
                  <span class="text-xs font-bold text-white flex items-center gap-1.5 flex-wrap">
                    <span>GPU Acceleration</span>
                    <span id="side-gpu-badge" class="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold bg-neutral-800 text-neutral-400 border border-neutral-700">Checking...</span>
                  </span>
                  <p class="text-[10px] text-neutral-500 mt-0.5">NVENC / VAAPI hardware offload</p>
                </div>
                <label class="relative inline-flex items-center cursor-pointer">
                  <input type="checkbox" id="side-gpu-toggle" onchange="applyGpuToggle(this.checked)" class="sr-only peer">
                  <div class="w-9 h-5 bg-neutral-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-neutral-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-red-600 peer-disabled:opacity-30 peer-disabled:cursor-not-allowed"></div>
                </label>
              </div>

              <!-- GPU Installation & Implementation Notice Box -->
              <div id="side-gpu-notice-box" class="hidden p-3 bg-neutral-900/95 border border-amber-500/40 rounded-xl space-y-3 text-xs animate-fade-in shadow-xl">
                <div class="flex items-start gap-2">
                  <span class="w-2 h-2 rounded-full bg-amber-400 mt-1 flex-shrink-0 animate-ping"></span>
                  <div class="flex-1 min-w-0">
                    <span id="side-gpu-notice-title" class="font-bold text-amber-200 text-[11px] block truncate">NVIDIA GTX 1050 Ti Detected</span>
                    <span id="side-gpu-os-host-badge" class="inline-block mt-0.5 px-1.5 py-0.2 rounded text-[9px] font-mono font-semibold bg-neutral-800 text-neutral-300 border border-neutral-700">Host: Linux</span>
                    <p id="side-gpu-notice-desc" class="text-[10px] text-neutral-300 mt-1 leading-relaxed">
                      GPU hardware is detected, but running open-source <code class="text-amber-300 font-mono">nouveau</code>. Hardware encoding requires proper drivers.
                    </p>
                  </div>
                </div>

                <!-- Multi-OS Platform Switcher Tabs -->
                <div class="space-y-1.5 pt-1 border-t border-neutral-800/80">
                  <div class="flex items-center justify-between text-[10px] text-neutral-400 font-bold uppercase tracking-wider">
                    <span>Guide by Platform:</span>
                    <span class="text-neutral-500 font-normal text-[9px]">(Click to Switch)</span>
                  </div>
                  <div class="grid grid-cols-3 gap-1 bg-black/60 p-1 rounded-lg border border-neutral-800" id="gpu-os-tabs">
                    <button type="button" onclick="selectGpuGuideOs('linux')" id="tab-os-linux" class="py-1 px-1.5 rounded text-[10px] font-bold transition flex items-center justify-center gap-1 cursor-pointer bg-neutral-800 text-white border border-amber-500/40">
                      <span>🐧 Linux</span>
                    </button>
                    <button type="button" onclick="selectGpuGuideOs('windows')" id="tab-os-windows" class="py-1 px-1.5 rounded text-[10px] font-semibold transition flex items-center justify-center gap-1 cursor-pointer text-neutral-400 hover:text-white">
                      <span>🪟 Windows</span>
                    </button>
                    <button type="button" onclick="selectGpuGuideOs('macos')" id="tab-os-macos" class="py-1 px-1.5 rounded text-[10px] font-semibold transition flex items-center justify-center gap-1 cursor-pointer text-neutral-400 hover:text-white">
                      <span>🍎 macOS</span>
                    </button>
                  </div>
                </div>

                <!-- OS Guide 1: LINUX -->
                <div id="guide-content-linux" class="space-y-2">
                  <div class="flex items-center justify-between gap-1 flex-wrap">
                    <span class="text-[10px] text-neutral-400 font-semibold">Distribution:</span>
                    <div class="flex items-center gap-1 text-[9px]">
                      <button type="button" onclick="selectLinuxDistro('ubuntu')" id="btn-distro-ubuntu" class="px-1.5 py-0.5 rounded font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 cursor-pointer">Ubuntu/Debian</button>
                      <button type="button" onclick="selectLinuxDistro('arch')" id="btn-distro-arch" class="px-1.5 py-0.5 rounded font-mono text-neutral-400 hover:text-white bg-neutral-800 cursor-pointer">Arch</button>
                      <button type="button" onclick="selectLinuxDistro('fedora')" id="btn-distro-fedora" class="px-1.5 py-0.5 rounded font-mono text-neutral-400 hover:text-white bg-neutral-800 cursor-pointer">Fedora</button>
                    </div>
                  </div>

                  <div class="bg-black/90 rounded-lg p-2 border border-neutral-800 flex items-center justify-between gap-2">
                    <code id="side-gpu-linux-cmd" class="text-[10px] font-mono text-emerald-400 truncate select-all">sudo apt update &amp;&amp; sudo apt install -y nvidia-driver-580 &amp;&amp; sudo reboot</code>
                    <button type="button" onclick="copyTextFromElement('side-gpu-linux-cmd')" class="px-2 py-1 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 text-[10px] font-bold rounded cursor-pointer transition flex-shrink-0 border border-neutral-700">
                      Copy
                    </button>
                  </div>
                  <p class="text-[9px] text-neutral-400 leading-tight">
                    • Intel VAAPI: <code class="text-neutral-300 font-mono">sudo apt install -y intel-media-va-driver-non-free vainfo</code>
                  </p>
                </div>

                <!-- OS Guide 2: WINDOWS -->
                <div id="guide-content-windows" class="hidden space-y-2">
                  <p class="text-[10px] text-neutral-300 leading-relaxed">
                    Install NVIDIA GeForce drivers. Windows places <code class="text-cyan-300 font-mono">nvcuda.dll</code> into System32 for instant NVENC acceleration:
                  </p>
                  <div class="bg-black/90 rounded-lg p-2 border border-neutral-800 flex items-center justify-between gap-2">
                    <code id="side-gpu-win-cmd" class="text-[10px] font-mono text-cyan-400 truncate select-all">winget install Nvidia.Driver.Display</code>
                    <button type="button" onclick="copyTextFromElement('side-gpu-win-cmd')" class="px-2 py-1 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 text-[10px] font-bold rounded cursor-pointer transition flex-shrink-0 border border-neutral-700">
                      Copy
                    </button>
                  </div>
                  <div class="flex items-center justify-between text-[10px] pt-0.5">
                    <span class="text-neutral-400">Manual download:</span>
                    <a href="https://www.nvidia.com/Download/index.aspx" target="_blank" rel="noopener noreferrer" class="text-red-400 hover:text-red-300 underline font-semibold flex items-center gap-1">
                      <span>nvidia.com/drivers</span>
                      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                    </a>
                  </div>
                  <p class="text-[9px] text-neutral-400 leading-tight">
                    • FFmpeg with NVENC: <code class="text-neutral-300 font-mono">winget install Gyan.FFmpeg</code><br>
                    • Intel QuickSync: <code class="text-neutral-300 font-mono">winget install Intel.GraphicsDriver</code>
                  </p>
                </div>

                <!-- OS Guide 3: MACOS -->
                <div id="guide-content-macos" class="hidden space-y-2">
                  <p class="text-[10px] text-neutral-300 leading-relaxed">
                    macOS uses Apple's native <strong class="text-white">VideoToolbox</strong> engine built into Apple Silicon (M1-M4) &amp; Intel Macs. <strong>Zero driver installs required!</strong>
                  </p>
                  <div class="bg-black/90 rounded-lg p-2 border border-neutral-800 flex items-center justify-between gap-2">
                    <code id="side-gpu-mac-cmd" class="text-[10px] font-mono text-purple-400 truncate select-all">brew install ffmpeg</code>
                    <button type="button" onclick="copyTextFromElement('side-gpu-mac-cmd')" class="px-2 py-1 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 text-[10px] font-bold rounded cursor-pointer transition flex-shrink-0 border border-neutral-700">
                      Copy
                    </button>
                  </div>
                  <p class="text-[9px] text-neutral-400 leading-tight">
                    Homebrew FFmpeg includes <code class="text-purple-300 font-mono">--enable-videotoolbox</code> out-of-the-box, providing instant zero-CPU hardware encoding with Apple Silicon Media Engines.
                  </p>
                </div>

                <!-- Clear Architecture Choice (Option 1 vs Option 2) -->
                <div class="text-[10px] text-neutral-400 space-y-1 border-t border-neutral-800 pt-2 leading-normal">
                  <p><span class="text-white font-semibold">• Option 1 (Keep CPU):</span> No action needed. CPU mode runs safely governed to your allocated cores with 0 risk of freezing.</p>
                  <p><span class="text-white font-semibold">• Option 2 (Enable GPU):</span> Follow the instructions above for your OS. StreamVault will auto-detect the hardware encoder and unlock hardware offload.</p>
                </div>
              </div>

            </div>

          </div>
        </div>
      </section>
"""

INGEST_MODAL_HTML = r"""  <!-- Ingest / Re-Ingest Stream Configuration Modal -->
  <div id="ingest-config-modal" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 hidden" onclick="if(event.target === this) closeIngestConfigModal()">
    <div class="bg-[#141414] border border-neutral-700/80 rounded-2xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl overflow-hidden" onclick="event.stopPropagation()">
      <!-- Modal Header -->
      <div class="px-6 py-4 border-b border-neutral-800 flex items-center justify-between bg-[#181818]/90">
        <div class="space-y-1 min-w-0 pr-4">
          <div class="flex items-center gap-2 flex-wrap">
            <h3 id="ingest-modal-title" class="text-base font-bold text-white truncate">Ingest &amp; Stream Configuration</h3>
            <span id="ingest-modal-type-badge" class="px-2 py-0.5 bg-neutral-800 text-neutral-300 border border-neutral-700 rounded text-[10px] font-bold">Single File</span>
          </div>
          <div class="flex items-center gap-2 text-xs text-neutral-400 flex-wrap">
            <span id="ingest-modal-filename" class="font-mono text-neutral-300 truncate max-w-md">filename.mkv</span>
            <span id="ingest-modal-video-badge" class="px-2 py-0.5 bg-cyan-950/80 text-cyan-300 border border-cyan-500/30 rounded text-[10px] font-bold font-mono">Probing...</span>
          </div>
        </div>
        <button type="button" id="btn-close-x-ingest-modal" onclick="closeIngestConfigModal()" class="w-8 h-8 rounded-full bg-neutral-800/80 hover:bg-neutral-700 text-neutral-400 hover:text-white flex items-center justify-center transition cursor-pointer flex-shrink-0 text-sm font-bold">✕</button>
      </div>

      <!-- Loading State -->
      <div id="ingest-modal-loading" class="p-12 flex flex-col items-center justify-center space-y-3">
        <svg class="animate-spin h-8 w-8 text-red-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
        </svg>
        <span class="text-xs text-neutral-400 font-medium">Probing audio &amp; subtitle streams with ffprobe...</span>
      </div>

      <!-- Content Body: Scrollable Vertical Stack of Media Cards -->
      <div id="ingest-modal-body" class="p-6 overflow-y-auto max-h-[75vh] space-y-6 pr-1 custom-scrollbar hidden">
        <!-- Rendered dynamically: one distinct configuration card per media file -->
      </div>

      <!-- Modal Footer -->
      <div class="px-6 py-4 border-t border-neutral-800 bg-[#181818]/90 flex items-center justify-between">
        <button type="button" id="btn-cancel-ingest-modal" onclick="closeIngestConfigModal()" class="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 rounded-xl text-xs font-semibold cursor-pointer transition">
          Cancel
        </button>
        <div class="flex items-center gap-3">
          <button type="button" onclick="submitIngestConfig()" id="btn-submit-ingest-config" class="px-5 py-2 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold transition flex items-center gap-2 cursor-pointer shadow-lg shadow-red-600/30">
            <span id="btn-submit-ingest-text">🚀 Ingest &amp; Publish</span>
          </button>
        </div>
      </div>
    </div>
  </div>
"""

MEDIA_INGEST_JS = r"""    async function deleteIncomingFile(filename, sizeMb, isPublished, btn = null) {
      const note = isPublished
        ? "✓ The published video in media/library will CONTINUE STREAMING normally without interruption!\n\n(Re-Ingesting this title will require re-uploading the file.)"
        : "The video has not been ingested into the library yet. Deleting it will remove it permanently.";
      
      const ok = await showAdminConfirm({
        title: 'Delete Raw Incoming Video',
        message: `Delete raw video file "${filename}" (${sizeMb} MB) from incoming to reclaim disk space?\n\n${note}`,
        confirmText: 'Delete Raw Video',
        isDanger: true
      });
      if (!ok) return;

      if (btn) setButtonProcessing(btn, true, '⏳ Deleting...');

      try {
        const res = await fetch(`/api/metadata/incoming-file/${encodeURIComponent(filename)}`, {
          method: 'DELETE'
        });
        const data = await res.json();
        if (res.ok) {
          showAdminToast(`✓ Deleted "${filename}". Reclaimed ${data.reclaimed_mb || sizeMb} MB disk space!`, 'success');
          fetchIncomingFiles();
        } else {
          showAdminToast(data.detail || 'Failed to delete file', 'error');
        }
      } catch (err) {
        showAdminToast('Network error while deleting file', 'error');
      } finally {
        if (btn) setButtonProcessing(btn, false);
      }
    }


    function renderDropdownItemRow(target, type, lang, isChecked, customValue, isDefault = false) {
      const isNone = (type === 'sub' && lang === 'None');
      const safeLang = lang.replace(/[^a-zA-Z0-9]/g, '_');
      const inputId = `label-input-${type}-${target}-${safeLang}`;
      const cbName = target === 'bulk' ? (type === 'audio' ? 'bulk-audio' : 'bulk-sub') : `${type}-${target}`;
      const displayLabel = cleanTrackWatermarks(customValue || lang);

      return `
        <div class="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-xs hover:bg-neutral-800 text-neutral-300 hover:text-white select-none transition group">
          <label class="flex items-center gap-2 cursor-pointer min-w-0 flex-1">
            <input type="checkbox" name="${cbName}" value="${escapeHtml(lang)}" ${isChecked ? 'checked' : ''} data-target="${escapeHtml(target)}" data-type="${escapeHtml(type)}" onchange="handleDropdownCheckboxChange(this.dataset.target, this.dataset.type, this)" class="w-3.5 h-3.5 rounded accent-red-600 cursor-pointer flex-shrink-0">
            <span class="truncate">${isNone ? '🚫 None (No Subtitles)' : escapeHtml(lang)}</span>
          </label>
          ${isNone ? '' : `
            <input 
              type="text" 
              id="${inputId}" 
              value="${escapeHtml(displayLabel)}" 
              placeholder="Display Name" 
              title="Player Display Name for ${escapeHtml(lang)}"
              data-target="${escapeHtml(target)}"
              data-type="${escapeHtml(type)}"
              onclick="event.stopPropagation()"
              oninput="updateDropdownButtonLabel(this.dataset.target, this.dataset.type)"
              class="track-label-input w-28 bg-black border border-neutral-700 focus:border-red-500 rounded px-1.5 py-0.5 text-[11px] text-white placeholder-neutral-500 font-medium ${isChecked ? '' : 'hidden'}"
            >
          `}
          ${type === 'audio' ? `
            <button
              type="button"
              id="default-btn-audio-${target}-${safeLang}"
              data-target="${escapeHtml(target)}"
              data-lang="${escapeHtml(lang)}"
              data-safelang="${escapeHtml(safeLang)}"
              data-default="${isDefault ? 'true' : 'false'}"
              onclick="event.stopPropagation(); setDefaultAudioTrack(this.dataset.target, this.dataset.safelang)"
              title="Set as Default Audio Track"
              class="default-audio-pill ${isChecked ? '' : 'hidden'} px-2 py-0.5 rounded text-[10px] cursor-pointer transition flex items-center gap-1 flex-shrink-0 ${isDefault ? 'bg-amber-500/20 text-amber-400 border border-amber-500/50 font-bold' : 'bg-neutral-800 text-neutral-400 border border-neutral-700 hover:text-white'}"
            >
              ${isDefault ? '★ Default' : '☆ Set Default'}
            </button>
          ` : ''}
        </div>
      `;
    }

    function setDefaultAudioTrack(target, selectedSafeLang) {
      const name = target === 'bulk' ? 'bulk-audio' : `audio-${target}`;
      const allCbs = document.querySelectorAll(`input[name="${name}"]`);

      allCbs.forEach(cb => {
        const safeLang = cb.value.replace(/[^a-zA-Z0-9]/g, '_');
        if (safeLang === selectedSafeLang && !cb.checked) {
          cb.checked = true;
          const inputEl = document.getElementById(`label-input-audio-${target}-${safeLang}`);
          if (inputEl) inputEl.classList.remove('hidden');
        }
      });

      allCbs.forEach(cb => {
        const safeLang = cb.value.replace(/[^a-zA-Z0-9]/g, '_');
        const btn = document.getElementById(`default-btn-audio-${target}-${safeLang}`);
        if (btn) {
          const isThisDefault = (safeLang === selectedSafeLang);
          btn.setAttribute('data-default', isThisDefault ? 'true' : 'false');
          if (isThisDefault) {
            btn.className = 'default-audio-pill px-2 py-0.5 rounded text-[10px] cursor-pointer transition flex items-center gap-1 flex-shrink-0 bg-amber-500/20 text-amber-400 border border-amber-500/50 font-bold';
            btn.innerHTML = '★ Default';
          } else {
            btn.className = `default-audio-pill ${cb.checked ? '' : 'hidden'} px-2 py-0.5 rounded text-[10px] cursor-pointer transition flex items-center gap-1 flex-shrink-0 bg-neutral-800 text-neutral-400 border border-neutral-700 hover:text-white`;
            btn.innerHTML = '☆ Set Default';
          }
        }
      });

      updateDropdownButtonLabel(target, 'audio');
    }

    let selectedIncoming = new Set();
    let currentIncomingTab = 'pending';
    let cachedIncomingFiles = [];
    let wasBusyPreviously = false;

    let incomingCurrentPage = 1;
    let incomingPageSize = 10;

    function getDisplayFileList() {
      let list = Array.isArray(cachedIncomingFiles) ? cachedIncomingFiles : [];
      if (currentIncomingTab === 'pending') {
        list = list.filter(f => !f.is_published);
      } else if (currentIncomingTab === 'published') {
        list = list.filter(f => f.is_published);
      }

      const query = (document.getElementById('incoming-search-input')?.value || '').trim().toLowerCase();
      if (query) {
        list = list.filter(f => {
          const fn = (f.filename || '').toLowerCase();
          const pubTitle = (f.published_title || '').toLowerCase();
          const matchedTitle = (f.matched_media?.title || '').toLowerCase();
          const cleanTitle = (f.parsed?.clean_title || '').toLowerCase();
          const sNum = f.parsed?.season_number;
          const eNum = f.parsed?.episode_number;
          const sPattern = (sNum !== undefined && sNum !== null) ? `s${String(sNum).padStart(2, '0')}` : '';
          const ePattern = (eNum !== undefined && eNum !== null) ? `e${String(eNum).padStart(2, '0')}` : '';
          const epCode = (sPattern && ePattern) ? `${sPattern}${ePattern}` : '';

          return fn.includes(query) ||
                 pubTitle.includes(query) ||
                 matchedTitle.includes(query) ||
                 cleanTitle.includes(query) ||
                 (epCode && epCode.includes(query)) ||
                 (sPattern && sPattern.includes(query)) ||
                 (ePattern && ePattern.includes(query));
        });
      }
      return list;
    }

    function getCurrentPageFileList() {
      const displayList = getDisplayFileList();
      const total = displayList.length;
      const totalPages = Math.max(1, Math.ceil(total / incomingPageSize));
      const safePage = Math.max(1, Math.min(incomingCurrentPage, totalPages));
      const startIdx = (safePage - 1) * incomingPageSize;
      return displayList.slice(startIdx, startIdx + incomingPageSize);
    }

    function onIncomingSearchInput() {
      selectedIncoming.clear();
      incomingCurrentPage = 1;
      renderIncomingFiles();
    }

    function clearIncomingSearch() {
      selectedIncoming.clear();
      const input = document.getElementById('incoming-search-input');
      if (input) input.value = '';
      incomingCurrentPage = 1;
      renderIncomingFiles();
    }

    function onIncomingPageSizeChange(val) {
      selectedIncoming.clear();
      incomingPageSize = parseInt(val, 10) || 10;
      incomingCurrentPage = 1;
      renderIncomingFiles();
    }

    function changeIncomingPage(target) {
      selectedIncoming.clear();
      const displayList = getDisplayFileList();
      const total = displayList.length;
      const totalPages = Math.max(1, Math.ceil(total / incomingPageSize));

      if (target === 'first') {
        incomingCurrentPage = 1;
      } else if (target === 'last') {
        incomingCurrentPage = totalPages;
      } else if (typeof target === 'number') {
        incomingCurrentPage += target;
      }
      incomingCurrentPage = Math.max(1, Math.min(incomingCurrentPage, totalPages));
      renderIncomingFiles();
    }

    // Global click listener to close multi-select popovers when clicking outside
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.multi-select-dropdown-container') && 
          !e.target.closest('.bulk-dropdown-btn') && 
          !e.target.closest('.card-dropdown-btn') && 
          !e.target.closest('.multi-select-popover')) {
        closeAllMultiDropdowns();
      }
    });

    function closeAllMultiDropdowns() {
      document.querySelectorAll('.multi-select-popover').forEach(el => el.classList.add('hidden'));
    }

    function toggleMultiDropdown(id, event) {
      if (event) event.stopPropagation();
      const popover = document.getElementById(`popover-${id}`);
      if (!popover) return;
      const isOpen = !popover.classList.contains('hidden');
      closeAllMultiDropdowns();
      if (!isOpen) {
        popover.classList.remove('hidden');
      }
    }

    function getDropdownSummaryText(type, selectedItems, defaultItem = null) {
      const filtered = (selectedItems || []).filter(v => v !== 'None' && v !== '');
      if (type === 'audio') {
        if (!filtered || filtered.length === 0) return '🔊 Select Audio ▾';
        const formatted = filtered.map(item => {
          if (defaultItem && (item === defaultItem || item === `★ ${defaultItem}`)) {
            return item.startsWith('★ ') ? item : `★ ${item}`;
          }
          return item;
        });
        if (defaultItem && !formatted.some(f => f.startsWith('★ ')) && formatted.length > 0) {
          formatted[0] = `★ ${formatted[0]}`;
        }
        if (formatted.length <= 2) return `🔊 Audio: ${formatted.join(', ')} (${formatted.length}) ▾`;
        return `🔊 Audio: ${formatted.slice(0, 2).join(', ')} (+${formatted.length - 2}) ▾`;
      } else {
        if (!filtered || filtered.length === 0) return '💬 Subtitles: None ▾';
        if (filtered.length <= 2) return `💬 Subs: ${filtered.join(', ')} (${filtered.length}) ▾`;
        return `💬 Subs: ${filtered.slice(0, 2).join(', ')} (+${filtered.length - 2}) ▾`;
      }
    }

    function updateDropdownButtonLabel(target, type) {
      const name = target === 'bulk' ? (type === 'audio' ? 'bulk-audio' : 'bulk-sub') : `${type}-${target}`;
      const labelId = target === 'bulk' ? (type === 'audio' ? 'label-dropdown-bulk-audio' : 'label-dropdown-bulk-subs') : `label-dropdown-${type}-${target}`;
      const labelEl = document.getElementById(labelId);
      if (!labelEl) return;
      const checkedInputs = Array.from(document.querySelectorAll(`input[name="${name}"]:checked`));

      if (type === 'sub') {
        const checkedLabels = checkedInputs.map(cb => {
          if (cb.value === 'None') return 'None';
          const safeLang = cb.value.replace(/[^a-zA-Z0-9]/g, '_');
          const inputEl = document.getElementById(`label-input-${type}-${target}-${safeLang}`);
          const custom = inputEl ? cleanTrackWatermarks(inputEl.value.trim()) : '';
          return custom || cb.value;
        });
        labelEl.innerText = getDropdownSummaryText('sub', checkedLabels);
        return;
      }

      // Audio handling with ★ Default indication
      if (checkedInputs.length === 0) {
        labelEl.innerText = '🔊 Select Audio ▾';
        return;
      }

      let defaultLabel = null;
      const otherLabels = [];

      checkedInputs.forEach(cb => {
        const safeLang = cb.value.replace(/[^a-zA-Z0-9]/g, '_');
        const inputEl = document.getElementById(`label-input-audio-${target}-${safeLang}`);
        const custom = inputEl ? cleanTrackWatermarks(inputEl.value.trim()) : '';
        const display = custom || cb.value;
        const btn = document.getElementById(`default-btn-audio-${target}-${safeLang}`);
        const isDef = btn && btn.getAttribute('data-default') === 'true';

        if (isDef && !defaultLabel) {
          defaultLabel = `★ ${display}`;
        } else {
          otherLabels.push(display);
        }
      });

      if (!defaultLabel && otherLabels.length > 0) {
        defaultLabel = `★ ${otherLabels.shift()}`;
      }

      const allFormatted = defaultLabel ? [defaultLabel, ...otherLabels] : otherLabels;
      const count = checkedInputs.length;

      if (count <= 2) {
        labelEl.innerText = `🔊 Audio: ${allFormatted.join(', ')} (${count}) ▾`;
      } else {
        labelEl.innerText = `🔊 Audio: ${allFormatted.slice(0, 2).join(', ')} (+${count - 2}) ▾`;
      }
    }

    function getDropdownTrackConfigs(target, type) {
      const name = target === 'bulk' ? (type === 'audio' ? 'bulk-audio' : 'bulk-sub') : `${type}-${target}`;
      const checkedInputs = Array.from(document.querySelectorAll(`input[name="${name}"]:checked`));
      const configs = [];
      let idx = 0;
      let hasDefault = false;
      checkedInputs.forEach(cb => {
        if (type === 'sub' && cb.value === 'None') return;
        const safeLang = cb.value.replace(/[^a-zA-Z0-9]/g, '_');
        const inputEl = document.getElementById(`label-input-${type}-${target}-${safeLang}`);
        const custom = inputEl ? cleanTrackWatermarks(inputEl.value.trim()) : '';
        const item = {
          index: idx,
          lang: getLangCode(cb.value),
          label: custom || cb.value
        };
        if (type === 'audio') {
          const btn = document.getElementById(`default-btn-audio-${target}-${safeLang}`);
          const isDef = btn ? (btn.getAttribute('data-default') === 'true') : (idx === 0);
          item.is_default = isDef;
          if (isDef) hasDefault = true;
        }
        configs.push(item);
        idx++;
      });
      if (type === 'audio' && configs.length > 0 && !hasDefault) {
        configs[0].is_default = true;
      }
      return configs;
    }

    function handleDropdownCheckboxChange(target, type, clickedInput) {
      const name = target === 'bulk' ? (type === 'audio' ? 'bulk-audio' : 'bulk-sub') : `${type}-${target}`;
      if (type === 'sub') {
        const allSubs = document.querySelectorAll(`input[name="${name}"]`);
        if (clickedInput && clickedInput.value === 'None') {
          if (clickedInput.checked) {
            allSubs.forEach(cb => { if (cb.value !== 'None') cb.checked = false; });
          } else {
            const anyChecked = Array.from(allSubs).some(cb => cb.value !== 'None' && cb.checked);
            if (!anyChecked) clickedInput.checked = true;
          }
        } else {
          if (clickedInput && clickedInput.checked) {
            allSubs.forEach(cb => { if (cb.value === 'None') cb.checked = false; });
          } else {
            const anyChecked = Array.from(allSubs).some(cb => cb.value !== 'None' && cb.checked);
            if (!anyChecked) {
              allSubs.forEach(cb => { if (cb.value === 'None') cb.checked = true; });
            }
          }
        }
      }

      // Synchronize visibility of the compact text inputs and default audio buttons
      const allCbs = document.querySelectorAll(`input[name="${name}"]`);
      allCbs.forEach(cb => {
        const safeLang = cb.value.replace(/[^a-zA-Z0-9]/g, '_');
        const inputEl = document.getElementById(`label-input-${type}-${target}-${safeLang}`);
        if (inputEl) {
          if (cb.checked) {
            inputEl.classList.remove('hidden');
          } else {
            inputEl.classList.add('hidden');
          }
        }
        if (type === 'audio') {
          const btn = document.getElementById(`default-btn-audio-${target}-${safeLang}`);
          if (btn) {
            if (cb.checked) {
              btn.classList.remove('hidden');
            } else {
              btn.classList.add('hidden');
            }
          }
        }
      });

      // Mutual exclusion and auto-default logic for Audio
      if (type === 'audio') {
        const checkedAudio = Array.from(document.querySelectorAll(`input[name="${name}"]:checked`));
        if (checkedAudio.length === 0) {
          allCbs.forEach(cb => {
            const s = cb.value.replace(/[^a-zA-Z0-9]/g, '_');
            const btn = document.getElementById(`default-btn-audio-${target}-${s}`);
            if (btn) {
              btn.setAttribute('data-default', 'false');
              btn.className = 'default-audio-pill hidden px-2 py-0.5 rounded text-[10px] cursor-pointer transition flex items-center gap-1 flex-shrink-0 bg-neutral-800 text-neutral-400 border border-neutral-700 hover:text-white';
              btn.innerHTML = '☆ Set Default';
            }
          });
        } else {
          let hasDefault = false;
          for (const cb of checkedAudio) {
            const s = cb.value.replace(/[^a-zA-Z0-9]/g, '_');
            const btn = document.getElementById(`default-btn-audio-${target}-${s}`);
            if (btn && btn.getAttribute('data-default') === 'true') {
              hasDefault = true;
              break;
            }
          }
          if (!hasDefault) {
            const targetCb = (clickedInput && clickedInput.checked) ? clickedInput : checkedAudio[0];
            const targetSafeLang = targetCb.value.replace(/[^a-zA-Z0-9]/g, '_');
            setDefaultAudioTrack(target, targetSafeLang);
            return;
          }
        }
      }

      updateDropdownButtonLabel(target, type);
    }

    function clearDropdownSelection(target, type, event) {
      if (event) event.stopPropagation();
      const name = target === 'bulk' ? (type === 'audio' ? 'bulk-audio' : 'bulk-sub') : `${type}-${target}`;
      const allCbs = document.querySelectorAll(`input[name="${name}"]`);
      allCbs.forEach(cb => {
        if (type === 'audio') {
          cb.checked = false;
        } else {
          cb.checked = (cb.value === 'None');
        }
        const safeLang = cb.value.replace(/[^a-zA-Z0-9]/g, '_');
        const inputEl = document.getElementById(`label-input-${type}-${target}-${safeLang}`);
        if (inputEl) {
          if (cb.checked) {
            inputEl.classList.remove('hidden');
          } else {
            inputEl.classList.add('hidden');
          }
        }
        if (type === 'audio') {
          const btn = document.getElementById(`default-btn-audio-${target}-${safeLang}`);
          if (btn) {
            btn.classList.add('hidden');
            btn.setAttribute('data-default', 'false');
            btn.className = 'default-audio-pill hidden px-2 py-0.5 rounded text-[10px] cursor-pointer transition flex items-center gap-1 flex-shrink-0 bg-neutral-800 text-neutral-400 border border-neutral-700 hover:text-white';
            btn.innerHTML = '☆ Set Default';
          }
        }
      });
      updateDropdownButtonLabel(target, type);
    }

    function selectNoneSubtitle(target, event) {
      if (event) event.stopPropagation();
      clearDropdownSelection(target, 'sub', event);
    }

    async function attachExternalSubtitleToFile(filename, input) {
      if (!input?.files?.[0]) return;
      const file = input.files[0];
      const formData = new FormData();
      formData.append('file', file);
      formData.append('target_filename', filename);
      formData.append('lang', 'en');

      try {
        const res = await fetch('/api/admin/incoming/upload-subtitle', {
          method: 'POST',
          body: formData
        });
        if (res.ok) {
          showAdminToast({ type: 'success', title: 'Subtitle Attached', message: `Attached "${file.name}" to "${filename}".` });
          await fetchIncomingFiles();
        } else {
          const err = await res.json().catch(() => ({}));
          showAdminToast({ type: 'error', title: 'Attachment Failed', message: err.detail || 'Could not attach subtitle' });
        }
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Network Error', message: e.message });
      }
    }

    function initBulkDropdowns() {
      const audioList = document.getElementById('bulk-audio-dropdown-list');
      if (audioList && audioList.children.length === 0) {
        audioList.innerHTML = ALL_AUDIO_LANGS.map(lang => {
          const isChecked = lang === 'English';
          const isDefault = lang === 'English';
          return renderDropdownItemRow('bulk', 'audio', lang, isChecked, lang, isDefault);
        }).join('');
        updateDropdownButtonLabel('bulk', 'audio');
      }

      const subList = document.getElementById('bulk-sub-dropdown-list');
      if (subList && subList.children.length === 0) {
        subList.innerHTML = ALL_SUBTITLE_LANGS.map(sub => {
          const isChecked = sub === 'English';
          return renderDropdownItemRow('bulk', 'sub', sub, isChecked, sub, false);
        }).join('');
        updateDropdownButtonLabel('bulk', 'sub');
      }
    }


    function getBulkAudioAndSubLanguages() {
      const checkedAudio = Array.from(document.querySelectorAll('input[name="bulk-audio"]:checked')).map(cb => cb.value);
      const checkedSub = Array.from(document.querySelectorAll('input[name="bulk-sub"]:checked')).map(cb => cb.value).filter(v => v !== 'None');
      const audio_tracks_config = getDropdownTrackConfigs('bulk', 'audio');
      const subtitle_tracks_config = getDropdownTrackConfigs('bulk', 'sub');
      return {
        audio_languages: checkedAudio.length > 0 ? checkedAudio : ['English'],
        subtitle_languages: checkedSub,
        audio_tracks_config: audio_tracks_config.length > 0 ? audio_tracks_config : [{ index: 0, lang: 'en', label: 'English', is_default: true }],
        subtitle_tracks_config: subtitle_tracks_config
      };
    }

    function toggleBulkActionMenu(event) {
      if (event) event.stopPropagation();
      const menu = document.getElementById('menu-bulk-action');
      if (menu) menu.classList.toggle('hidden');
    }

    function closeBulkActionMenu() {
      const menu = document.getElementById('menu-bulk-action');
      if (menu) menu.classList.add('hidden');
    }

    document.addEventListener('click', (e) => {
      const wrap = document.getElementById('bulk-action-dropdown-wrap');
      if (wrap && !wrap.contains(e.target)) {
        closeBulkActionMenu();
      }
    });

    function updateSelectedIncomingUI() {
      const pageFiles = getCurrentPageFileList();
      const selectedPageFiles = pageFiles.filter(f => selectedIncoming.has(f.filename));
      const totalInTab = getDisplayFileList().length;

      const bulkBar = document.getElementById('contextual-bulk-bar');
      if (bulkBar) {
        if (totalInTab === 0) {
          bulkBar.classList.add('hidden');
        } else {
          bulkBar.classList.remove('hidden');
        }
      }

      // Update count & master checkbox strictly on current page
      const countEl = document.getElementById('bulk-selected-count');
      if (countEl) {
        countEl.innerText = `${selectedPageFiles.length} of ${pageFiles.length} on page selected`;
      }

      const allMaster = document.getElementById('select-all-incoming');
      if (allMaster) {
        allMaster.checked = pageFiles.length > 0 && selectedPageFiles.length === pageFiles.length;
      }

      // Primary bulk action dropdown button
      const bulkBtn = document.getElementById('btn-bulk-action');
      const bulkBtnText = document.getElementById('btn-bulk-action-text');
      const bulkMenu = document.getElementById('menu-bulk-action');

      if (bulkBtn && bulkBtnText) {
        if (selectedPageFiles.length === 0) {
          bulkBtn.disabled = true;
          bulkBtnText.innerText = 'Bulk Action';
          if (bulkMenu) bulkMenu.classList.add('hidden');
        } else {
          bulkBtn.disabled = false;
          bulkBtnText.innerText = `Bulk Action (${selectedPageFiles.length})`;
        }
      }

      // Dropdown menu options
      const optIngestBtn = document.getElementById('btn-bulk-opt-ingest');
      const optIngestLabel = document.getElementById('label-bulk-opt-ingest');
      const optIngestCount = document.getElementById('count-bulk-opt-ingest');
      const optDeleteCount = document.getElementById('count-bulk-opt-delete');

      const rawAvailableCount = selectedPageFiles.filter(f => !f.is_raw_source_removed).length;

      if (optIngestCount) optIngestCount.innerText = rawAvailableCount;
      if (optDeleteCount) optDeleteCount.innerText = selectedPageFiles.length;

      if (optIngestLabel) {
        if (currentIncomingTab === 'pending') {
          optIngestLabel.innerText = '🚀 Ingest Selected';
        } else if (currentIncomingTab === 'published') {
          optIngestLabel.innerText = '↻ Re-Ingest Selected';
        } else {
          optIngestLabel.innerText = '🚀 Ingest / Re-Ingest Selected';
        }
      }

      if (optIngestBtn) {
        if (selectedPageFiles.length > 0 && rawAvailableCount === 0) {
          optIngestBtn.disabled = true;
          optIngestBtn.classList.add('opacity-40', 'cursor-not-allowed');
          optIngestBtn.title = 'Raw source files were removed; only Delete is available';
        } else {
          optIngestBtn.disabled = false;
          optIngestBtn.classList.remove('opacity-40', 'cursor-not-allowed');
          optIngestBtn.title = '';
        }
      }

      // Synchronize disabling of each single file row's controls when selected
      pageFiles.forEach(f => {
        const safeId = f.filename.replace(/[^a-zA-Z0-9]/g, '_');
        const isSelected = selectedIncoming.has(f.filename);

        const allCheckboxes = document.querySelectorAll('.incoming-checkbox');
        allCheckboxes.forEach(cb => {
          if (cb.dataset.filename === f.filename) {
            cb.checked = isSelected;
          }
        });

        const rowEl = document.getElementById(`incoming-row-${safeId}`);
        if (rowEl) {
          if (isSelected) {
            rowEl.className = 'bg-neutral-900 border border-red-500/60 bg-red-950/15 ring-1 ring-red-500/30 p-3.5 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 transition';
          } else {
            rowEl.className = `bg-neutral-900 border ${f.is_published ? 'border-neutral-800/80 bg-neutral-900/50' : 'border-neutral-800'} p-3.5 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 hover:border-neutral-700 transition`;
          }
        }

        const badge = document.getElementById(`bulk-badge-${safeId}`);
        if (badge) {
          badge.classList.toggle('hidden', !isSelected);
        }

        const singleBtn = document.getElementById(`btn-ingest-${safeId}`);
        if (singleBtn) {
          singleBtn.disabled = isSelected;
        }
      });
    }

    function toggleFileSelect(filename, checked) {
      if (checked) {
        selectedIncoming.add(filename);
      } else {
        selectedIncoming.delete(filename);
      }
      updateSelectedIncomingUI();
    }

    function toggleSelectAllIncoming(masterCheckbox) {
      const pageFiles = getCurrentPageFileList();
      const checked = masterCheckbox.checked;
      pageFiles.forEach(f => {
        if (checked) {
          selectedIncoming.add(f.filename);
        } else {
          selectedIncoming.delete(f.filename);
        }
      });
      updateSelectedIncomingUI();
    }

    async function bulkDeleteSelected() {
      const pageFiles = getCurrentPageFileList();
      const selectedItems = pageFiles.filter(f => selectedIncoming.has(f.filename));
      if (selectedItems.length === 0) {
        showAdminToast('Please select at least 1 item on current page to delete.', 'warning');
        return;
      }

      const rawCount = selectedItems.filter(f => !f.is_raw_source_removed).length;
      const publishedCount = selectedItems.filter(f => f.is_published || f.is_raw_source_removed).length;

      let msg = `Permanently delete ${selectedItems.length} selected media item(s)?\n\n`;
      if (rawCount > 0) {
        msg += `• ${rawCount} raw video file(s) in media/incoming will be deleted to reclaim disk space.\n`;
      }
      if (publishedCount > 0) {
        msg += `• ${publishedCount} published stream(s) in media/library and catalog records will be permanently wiped.\n`;
      }
      msg += `\nThis action cannot be undone.`;

      const ok = await showAdminConfirm({
        title: 'Bulk Delete Selected Media',
        message: msg,
        confirmText: `🗑️ Permanently Delete (${selectedItems.length})`,
        isDanger: true
      });
      if (!ok) return;

      const btn = document.getElementById('btn-bulk-action');
      if (btn) setButtonProcessing(btn, true, '⏳ Deleting...');

      try {
        const payloadItems = selectedItems.map(f => ({
          filename: f.filename,
          is_raw_source_removed: !!f.is_raw_source_removed,
          media_id: f.matched_media?.id || null,
          episode_id: (f.matched_media?.type === 'series') ? f.matched_media?.id : null,
          target_id: f.matched_media?.id || null
        }));

        const res = await fetch('/api/admin/bulk-delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items: payloadItems })
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Bulk delete failed' }));
          throw new Error(err.detail || 'Bulk delete failed');
        }
        const data = await res.json();
        selectedIncoming.clear();
        showAdminToast(`Successfully deleted ${data.deleted_count || selectedItems.length} item(s)! Reclaimed ${data.freed_mb || 0} MB.`, 'success');
        updateSelectedIncomingUI();
        fetchIncomingFiles();
        loadCatalog();
      } catch (e) {
        showAdminToast('Bulk delete failed: ' + e.message, 'error');
      } finally {
        if (btn) setButtonProcessing(btn, false);
        updateSelectedIncomingUI();
      }
    }

    function setIncomingTab(tab) {
      selectedIncoming.clear();
      currentIncomingTab = tab;
      incomingCurrentPage = 1;
      ['pending', 'published', 'all', 'queue'].forEach(t => {
        const btn = document.getElementById(`tab-btn-${t}`);
        if (!btn) return;
        if (t === tab) {
          btn.className = 'px-3.5 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-1.5 bg-red-600/20 text-red-400 border border-red-500/40 cursor-pointer';
        } else {
          btn.className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition flex items-center gap-1.5 border border-transparent cursor-pointer';
        }
      });
      renderIncomingFiles();
    }

    function updateActiveJobRowButtons() {
      if (window.currentActiveJob && window.currentActiveJob.filename) {
        const safeId = window.currentActiveJob.filename.replace(/[^a-zA-Z0-9]/g, '_');
        const btn = document.getElementById('btn-ingest-' + safeId);
        if (btn) {
          const pct = Math.round(window.currentActiveJob.progress_pct || 0);
          btn.disabled = true;
          btn.className = 'single-ingest-btn px-3.5 py-1.5 bg-amber-600/30 text-amber-300 border border-amber-500/50 rounded-lg text-xs font-bold flex items-center gap-1.5 cursor-not-allowed';
          btn.innerHTML = `
            <svg class="animate-spin -ml-0.5 mr-1.5 h-3.5 w-3.5 text-amber-300 inline" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
            </svg>
            <span>⏳ Processing (${pct}%)...</span>
          `;
        }
        const row = document.getElementById('incoming-row-' + safeId);
        if (row && !row.classList.contains('border-amber-500/60')) {
          row.classList.remove('border-neutral-800');
          row.classList.add('border-amber-500/60', 'bg-amber-950/20', 'ring-1', 'ring-amber-500/30');
          const cb = row.querySelector('.incoming-checkbox');
          if (cb) { cb.disabled = true; cb.classList.add('opacity-40', 'pointer-events-none'); }
          const ctrl = document.getElementById('controls-row-' + safeId);
          if (ctrl) ctrl.classList.add('opacity-40', 'pointer-events-none');
        }
      }
    }

    function renderQueueStatus(data) {
      if (!data) return;

      window.currentActiveJob = (data.is_busy && data.active_job) ? data.active_job : null;
      window.currentQueuedJobs = data.queued_jobs || [];

      const dot = document.getElementById('queue-status-dot');
      const titleEl = document.getElementById('queue-status-title');
      const subEl = document.getElementById('queue-status-sub');
      const badgeEl = document.getElementById('queue-badge');
      const progContainer = document.getElementById('queue-progress-container');
      const jobTitleEl = document.getElementById('queue-job-title');
      const progPctEl = document.getElementById('queue-progress-pct');
      const progBar = document.getElementById('queue-progress-bar');
      const stageEl = document.getElementById('queue-job-stage');
      const cancelCont = document.getElementById('cancel-job-container');

      const isBusy = !!(data.is_busy && data.active_job);
      const qLen = data.queue_length || 0;
      const totalJobs = qLen + (isBusy ? 1 : 0);

      // Update Tab 4 count badge
      const bqCount = document.getElementById('badge-queue-count');
      if (bqCount) {
        bqCount.innerText = totalJobs;
        bqCount.className = totalJobs > 0
          ? 'px-1.5 py-0.2 rounded-full bg-amber-500 text-black text-[10px] font-mono font-black animate-pulse'
          : 'px-1.5 py-0.2 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[10px] font-mono font-bold';
      }

      if (badgeEl) {
        badgeEl.innerText = `${totalJobs} job${totalJobs === 1 ? '' : 's'} in queue`;
        badgeEl.className = totalJobs > 0 
          ? 'px-2.5 py-1 bg-red-600/20 text-red-400 border border-red-500/40 rounded-lg text-[10px] font-mono font-bold cursor-pointer hover:bg-red-600 hover:text-white transition'
          : 'px-2.5 py-1 bg-neutral-800 text-neutral-400 rounded-lg text-[10px] font-mono font-bold cursor-pointer hover:bg-neutral-700 transition';
      }

      if (isBusy) {
        const job = data.active_job;
        if (dot) dot.className = 'w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse';
        if (titleEl) titleEl.innerText = `Transcoding Active: ${job.title}`;
        if (subEl) subEl.innerText = `Sequential Processing (concurrency = 1) • ${qLen} more waiting in queue`;

        if (progContainer) progContainer.classList.remove('hidden');
        if (jobTitleEl) jobTitleEl.innerText = `${job.title}`;
        const pct = Math.max(0, Math.min(100, job.progress_pct || 0));
        if (progPctEl) progPctEl.innerText = `${pct}%`;
        if (progBar) progBar.style.width = `${pct}%`;
        if (stageEl) stageEl.innerText = `Stage: ${job.stage || 'Remuxing/Transcoding...'}`;
        if (cancelCont) cancelCont.classList.remove('hidden');
        wasBusyPreviously = true;
        updateActiveJobRowButtons();
      } else {
        if (dot) dot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-500';
        if (titleEl) titleEl.innerText = 'Engine Idle — Ready';
        if (subEl) subEl.innerText = 'All media queued in library processed';
        if (progContainer) progContainer.classList.add('hidden');
        if (cancelCont) cancelCont.classList.add('hidden');

        // Auto-refresh incoming list and catalog if a job just finished!
        if (wasBusyPreviously) {
          wasBusyPreviously = false;
          showAdminToast('✓ Ingestion completed & published to library!', 'success');
          fetchIncomingFiles();
          loadCatalog();
        }
      }

      // Update right sidebar compact preview of waiting jobs
      const sbPreview = document.getElementById('sidebar-queue-preview');
      const sbCount = document.getElementById('sidebar-queue-count');
      const sbList = document.getElementById('sidebar-queue-list');
      if (sbPreview && sbList) {
        const waitingJobs = data.queued_jobs || [];
        if (waitingJobs.length > 0) {
          sbPreview.classList.remove('hidden');
          if (sbCount) sbCount.innerText = waitingJobs.length;
          const top3 = waitingJobs.slice(0, 3);
          sbList.innerHTML = top3.map((q, idx) => `
            <div class="flex items-center justify-between gap-2 p-1.5 bg-neutral-900/90 border border-neutral-800 rounded-lg text-xs">
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-1.5">
                  <span class="text-[10px] font-mono text-amber-400 font-bold">#${idx + 1}</span>
                  <span class="text-white font-semibold truncate text-[11px]">${escapeHtml(q.title || q.filename)}</span>
                </div>
                <p class="text-[10px] text-neutral-500 font-mono truncate">${escapeHtml(q.filename)}</p>
              </div>
              <button type="button" data-identifier="${escapeHtml(q.job_id || q.filename)}" onclick="cancelQueuedTranscodeJob(this.dataset.identifier, this)" title="Cancel this queued job" class="px-1.5 py-0.5 text-neutral-400 hover:text-red-400 hover:bg-red-500/10 rounded transition text-[11px] font-bold cursor-pointer">
                ✕
              </button>
            </div>
          `).join('');
        } else {
          sbPreview.classList.add('hidden');
          sbList.innerHTML = '';
        }
      }

      // If on the dedicated queue tab, refresh its view immediately
      if (currentIncomingTab === 'queue') {
        renderQueuedJobsView();
      }
    }

    async function cancelActiveTranscodeJob() {
      const btn = document.getElementById('btn-cancel-active-job');
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="animate-pulse">Cancelling active job...</span>';
      }
      try {
        const res = await fetch('/api/system/transcode/cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        if (res.ok) {
          showAdminToast('✓ ' + (data.message || 'Transcoding job cancelled and partial segments cleaned.'), 'warning');
          await updateQueueStatus();
          fetchIncomingFiles();
        } else {
          showAdminToast('Failed to cancel job: ' + (data.detail || JSON.stringify(data)), 'error');
        }
      } catch (err) {
        showAdminToast('Error cancelling job: ' + err.message, 'error');
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg><span>✕ Cancel Active Job</span>';
        }
      }
    }

    async function cancelQueuedTranscodeJob(identifier, btn) {
      if (btn) setButtonProcessing(btn, true, '⏳ Cancelling...');
      try {
        const res = await fetch(`/api/admin/transcode/queue/${encodeURIComponent(identifier)}`, {
          method: 'DELETE'
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          showAdminToast('✓ ' + (data.message || 'Queued job cancelled.'), 'warning');
          await updateQueueStatus();
          fetchIncomingFiles();
        } else {
          showAdminToast('Failed to cancel queued job: ' + (data.detail || JSON.stringify(data)), 'error');
        }
      } catch (err) {
        showAdminToast('Error cancelling queued job: ' + err.message, 'error');
      } finally {
        if (btn) setButtonProcessing(btn, false);
      }
    }

    async function cancelAllQueuedTranscodeJobs(btn) {
      if (!confirm('Are you sure you want to cancel all waiting queued transcode jobs?')) return;
      if (btn) setButtonProcessing(btn, true, '⏳ Cancelling all...');
      try {
        const res = await fetch('/api/admin/transcode/queue/clear', {
          method: 'POST'
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          showAdminToast('✓ ' + (data.message || 'All waiting jobs cleared from queue.'), 'warning');
          await updateQueueStatus();
          fetchIncomingFiles();
        } else {
          showAdminToast('Failed to clear queue: ' + (data.detail || JSON.stringify(data)), 'error');
        }
      } catch (err) {
        showAdminToast('Error clearing queue: ' + err.message, 'error');
      } finally {
        if (btn) setButtonProcessing(btn, false);
      }
    }

    function renderQueuedJobsView() {
      const cont = document.getElementById('incoming-files-list');
      const bulkBar = document.getElementById('contextual-bulk-bar');
      const pagFooter = document.getElementById('incoming-pagination-footer');
      if (bulkBar) bulkBar.classList.add('hidden');
      if (pagFooter) pagFooter.classList.add('hidden');

      if (!cont) return;

      const activeJob = window.currentActiveJob;
      const queuedJobs = window.currentQueuedJobs || [];

      if (!activeJob && queuedJobs.length === 0) {
        cont.innerHTML = `
          <div class="bg-neutral-900/60 border border-neutral-800 rounded-xl p-8 text-center space-y-2">
            <div class="w-10 h-10 mx-auto rounded-full bg-neutral-800 text-neutral-400 flex items-center justify-center font-bold text-lg">🕒</div>
            <h5 class="text-white font-bold text-sm">Queue is currently empty</h5>
            <p class="text-neutral-400 text-xs">No active or waiting transcode jobs. Select files from the <button type="button" onclick="setIncomingTab('pending')" class="text-red-400 underline font-semibold cursor-pointer">Pending Ingest</button> or <button type="button" onclick="setIncomingTab('published')" class="text-red-400 underline font-semibold cursor-pointer">Published in Library</button> tabs to queue jobs.</p>
          </div>
        `;
        return;
      }

      let html = `
        <div class="flex items-center justify-between pb-1 text-xs">
          <span class="text-neutral-400 font-medium">
            ${activeJob ? '1 active job' : '0 active'} • ${queuedJobs.length} waiting in queue
          </span>
          ${queuedJobs.length > 0 ? `
            <button type="button" onclick="cancelAllQueuedTranscodeJobs(this)" class="px-3 py-1.5 bg-red-600/20 hover:bg-red-600 text-red-300 hover:text-white border border-red-500/40 rounded-lg text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow">
              <span>🗑️ Cancel All Waiting (${queuedJobs.length})</span>
            </button>
          ` : ''}
        </div>
      `;

      // Active Job Card
      if (activeJob) {
        const pct = Math.max(0, Math.min(100, Math.round(activeJob.progress_pct || 0)));
        html += `
          <div class="bg-gradient-to-r from-amber-950/30 to-neutral-900 border border-amber-500/60 rounded-xl p-4 space-y-3 ring-1 ring-amber-500/30 shadow-lg">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div class="flex items-center gap-2">
                <span class="px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wider bg-amber-500 text-black flex items-center gap-1 animate-pulse">
                  <span>⚡</span> <span>ACTIVE NOW</span>
                </span>
                <h4 class="text-sm font-bold text-white truncate max-w-md">${escapeHtml(activeJob.title)}</h4>
              </div>
              <button type="button" onclick="cancelActiveTranscodeJob()" class="self-start sm:self-auto px-3 py-1.5 bg-red-600/20 hover:bg-red-600 text-red-300 hover:text-white border border-red-500/40 rounded-lg text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow">
                <span>✕ Cancel Active Job</span>
              </button>
            </div>
            <div class="text-xs text-neutral-400 flex items-center gap-3 font-mono text-[11px] flex-wrap">
              <span>File: <span class="text-neutral-200">${escapeHtml(activeJob.filename)}</span></span>
              ${activeJob.media_type ? `<span class="bg-neutral-800 px-1.5 py-0.5 rounded uppercase text-[10px]">${escapeHtml(activeJob.media_type)}</span>` : ''}
            </div>
            <div class="space-y-1.5">
              <div class="flex items-center justify-between text-xs font-mono">
                <span class="text-neutral-400 text-[11px]">${escapeHtml(activeJob.stage || 'Transcoding segments...')}</span>
                <span class="text-amber-400 font-extrabold text-xs">${pct}%</span>
              </div>
              <div class="w-full h-2 bg-black rounded-full overflow-hidden border border-neutral-800">
                <div class="h-full bg-gradient-to-r from-amber-500 to-emerald-500 transition-all duration-300" style="width: ${pct}%"></div>
              </div>
            </div>
          </div>
        `;
      }

      // Waiting Queued Job Cards
      if (queuedJobs.length > 0) {
        html += `
          <div class="space-y-2.5 pt-1">
            <h5 class="text-xs font-bold text-neutral-400 uppercase tracking-wider">Waiting Queue (${queuedJobs.length})</h5>
        `;

        queuedJobs.forEach((q, idx) => {
          const qId = escapeHtml(q.job_id || q.filename);
          html += `
            <div class="bg-neutral-900 border border-neutral-800 hover:border-neutral-700 rounded-xl p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 transition">
              <div class="flex items-start sm:items-center gap-3 min-w-0">
                <div class="w-8 h-8 rounded-lg bg-neutral-800 border border-neutral-700 flex items-center justify-center font-mono font-bold text-xs text-amber-400 flex-shrink-0">
                  #${idx + 1}
                </div>
                <div class="space-y-1 min-w-0">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-bold text-white text-xs truncate">${escapeHtml(q.title || q.filename)}</span>
                    <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/30">
                      Waiting for processor...
                    </span>
                    ${q.media_type ? `<span class="bg-neutral-800 text-neutral-400 font-mono text-[10px] px-1.5 py-0.5 rounded uppercase">${escapeHtml(q.media_type)}</span>` : ''}
                  </div>
                  <div class="flex items-center gap-3 text-[11px] text-neutral-400 font-mono">
                    <span class="truncate">${escapeHtml(q.filename)}</span>
                    ${q.audio_count !== undefined ? `<span class="text-neutral-500">• ${q.audio_count} audio</span>` : ''}
                    ${q.sub_count !== undefined ? `<span class="text-neutral-500">• ${q.sub_count} subs</span>` : ''}
                  </div>
                </div>
              </div>
              <button type="button" data-identifier="${escapeHtml(qId)}" onclick="cancelQueuedTranscodeJob(this.dataset.identifier, this)" class="self-end sm:self-auto px-3 py-1.5 bg-red-600/20 hover:bg-red-600 text-red-300 hover:text-white border border-red-500/40 rounded-lg text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow">
                <span>✕ Cancel Queued</span>
              </button>
            </div>
          `;
        });

        html += `</div>`;
      }

      cont.innerHTML = html;
    }

    async function updateQueueStatus() {
      try {
        const data = await fetch('/api/system/status').then(r => r.ok ? r.json() : null).catch(() => null);
        renderQueueStatus(data);
      } catch (e) {}
    }

    async function fetchIncomingFiles() {
      try {
        const res = await fetch('/api/admin/incoming-files');
        if (!res.ok) {
          console.warn(`Incoming files response status: ${res.status}`);
          if (Array.isArray(cachedIncomingFiles) && cachedIncomingFiles.length > 0) {
            renderIncomingFiles();
            return;
          }
          throw new Error(`Server temporarily unavailable (${res.status})`);
        }
        const data = await res.json();
        let list = [];
        if (Array.isArray(data)) {
          list = data;
        } else if (data && Array.isArray(data.files)) {
          list = data.files;
        } else {
          console.warn('Unexpected non-array data from incoming-files:', data);
        }

        cachedIncomingFiles = Array.isArray(list) ? list : [];

        const existingFilenames = new Set(cachedIncomingFiles.map(f => f && f.filename).filter(Boolean));
        for (const fn of selectedIncoming) {
          if (!existingFilenames.has(fn)) selectedIncoming.delete(fn);
        }

        renderIncomingFiles();
      } catch (e) {
        console.warn('fetchIncomingFiles error:', e);
        if (!Array.isArray(cachedIncomingFiles)) {
          cachedIncomingFiles = [];
        }
        if (cachedIncomingFiles.length === 0) {
          const cont = document.getElementById('incoming-files-list');
          if (cont) cont.innerHTML = '<div class="text-red-400 py-1">Could not fetch incoming folder: ' + escapeHtml(e.message) + '</div>';
        } else {
          renderIncomingFiles();
        }
      }
    }

    function renderIncomingFiles() {
      // If currently on the dedicated queue tab, render the queue dashboard view
      if (currentIncomingTab === 'queue') {
        renderQueuedJobsView();
        return;
      }

      const pagFooter = document.getElementById('incoming-pagination-footer');
      if (pagFooter) pagFooter.classList.remove('hidden');

      const cont = document.getElementById('incoming-files-list');
      if (!cont) return;

      if (!Array.isArray(cachedIncomingFiles)) cachedIncomingFiles = [];
      const pendingFiles = cachedIncomingFiles.filter(f => !f.is_published);
      const publishedFiles = cachedIncomingFiles.filter(f => f.is_published);

      // Update count badges
      const bp = document.getElementById('badge-pending-count');
      if (bp) bp.innerText = pendingFiles.length;
      const bpub = document.getElementById('badge-published-count');
      if (bpub) bpub.innerText = publishedFiles.length;
      const ba = document.getElementById('badge-all-count');
      if (ba) ba.innerText = cachedIncomingFiles.length;

      const displayList = getDisplayFileList();
      const total = displayList.length;
      const totalPages = Math.max(1, Math.ceil(total / incomingPageSize));
      if (incomingCurrentPage > totalPages) incomingCurrentPage = totalPages;
      if (incomingCurrentPage < 1) incomingCurrentPage = 1;

      const pInfo = document.getElementById('incoming-pagination-info');
      const pDisplay = document.getElementById('incoming-page-display');
      const btnFirst = document.getElementById('btn-inc-first');
      const btnPrev = document.getElementById('btn-inc-prev');
      const btnNext = document.getElementById('btn-inc-next');
      const btnLast = document.getElementById('btn-inc-last');

      if (displayList.length === 0) {
        if (currentIncomingTab === 'pending') {
          cont.innerHTML = `
            <div class="bg-neutral-900/60 border border-neutral-800 rounded-xl p-8 text-center space-y-2">
              <div class="w-10 h-10 mx-auto rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-lg">✓</div>
              <h5 class="text-white font-bold text-sm">No pending files to ingest</h5>
              <p class="text-neutral-400 text-xs">All incoming files have been ingested and published. Switch to the <button type="button" onclick="setIncomingTab('published')" class="text-red-400 underline font-semibold cursor-pointer">Published in Library</button> tab to review titles or drop new videos into <code class="font-mono text-neutral-300">media/incoming</code>.</p>
            </div>
          `;
        } else if (currentIncomingTab === 'published') {
          cont.innerHTML = `
            <div class="bg-neutral-900/60 border border-neutral-800 rounded-xl p-8 text-center space-y-2">
              <div class="w-10 h-10 mx-auto rounded-full bg-neutral-800 text-neutral-400 flex items-center justify-center font-bold text-lg">📂</div>
              <h5 class="text-white font-bold text-sm">No files published in library yet</h5>
              <p class="text-neutral-400 text-xs">Ingest pending files from the "Pending Ingest" tab to publish them into the library.</p>
            </div>
          `;
        } else {
          cont.innerHTML = `
            <div class="bg-neutral-900/60 border border-neutral-800 rounded-xl p-8 text-center space-y-2">
              <div class="w-10 h-10 mx-auto rounded-full bg-neutral-800 text-neutral-400 flex items-center justify-center font-bold text-lg">📁</div>
              <h5 class="text-white font-bold text-sm">No files found in media/incoming</h5>
              <p class="text-neutral-400 text-xs">Drop or upload video files into <code class="font-mono text-neutral-300">media/incoming</code> to process them.</p>
            </div>
          `;
        }
        if (pInfo) pInfo.innerText = 'Showing 0 to 0 of 0 files';
        if (pDisplay) pDisplay.innerText = 'Page 1 of 1';
        if (btnFirst) btnFirst.disabled = true;
        if (btnPrev) btnPrev.disabled = true;
        if (btnNext) btnNext.disabled = true;
        if (btnLast) btnLast.disabled = true;
        updateSelectedIncomingUI();
        return;
      }

      const pageItems = displayList.slice((incomingCurrentPage - 1) * incomingPageSize, incomingCurrentPage * incomingPageSize);

      cont.innerHTML = pageItems.map(f => {
        const isPublished = !!f.is_published;
        const isMatched = !!f.matched_media;
        const safeId = f.filename.replace(/[^a-zA-Z0-9]/g, '_');
        const isChecked = selectedIncoming.has(f.filename);

        const epInfo = f.parsed.season_number ? `<span class="text-neutral-400 font-mono text-[11px] bg-neutral-800 px-1.5 py-0.5 rounded">S${f.parsed.season_number}E${f.parsed.episode_number}</span>` : '';

        // Status badge
        let statusBadge = '';
        if (f.processing_status === 'failed') {
          statusBadge = `<span class="px-2 py-0.5 bg-red-950 text-red-400 border border-red-500/40 rounded text-[10px] font-bold flex items-center gap-1"><span>⚠️</span> <span>Previous Ingest Failed — Retry</span></span>`;
        } else if (f.is_raw_source_removed) {
          statusBadge = `<span class="px-2 py-0.5 bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded text-[10px] font-bold flex items-center gap-1"><span>✓</span> <span>HLS Stream Active in /media/library (Raw source removed)</span></span>`;
        } else if (isPublished) {
          statusBadge = `<span class="px-2 py-0.5 bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded text-[10px] font-bold flex items-center gap-1"><span>✓</span> <span>Published: ${escapeHtml(f.published_title || f.matched_media?.title || f.filename)}</span></span>`;
        } else if (isMatched) {
          statusBadge = `<span class="px-2 py-0.5 bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 rounded text-[10px] font-bold">Auto-Matched: ${escapeHtml(f.matched_media.title)} (${escapeHtml(f.matched_media.type)})</span>`;
        } else {
          statusBadge = `<span class="px-2 py-0.5 bg-neutral-800 text-neutral-300 border border-neutral-700 rounded text-[10px] font-bold">Detected: ${escapeHtml(f.parsed.clean_title)} ${f.parsed.year ? '(' + escapeHtml(f.parsed.year) + ')' : ''}</span>`;
        }

        const actionBtnLabel = isPublished ? '↻ Re-Ingest' : 'Ingest &amp; Publish';

        const isActiveTranscoding = !!(window.currentActiveJob && (
          window.currentActiveJob.filename === f.filename ||
          (f.matched_media && (window.currentActiveJob.media_id === f.matched_media.id || window.currentActiveJob.episode_id === f.matched_media.id))
        ));

        const isQueued = !isActiveTranscoding && !!(window.currentQueuedJobs && window.currentQueuedJobs.some(q => 
          q.filename === f.filename ||
          (f.matched_media && (q.media_id === f.matched_media.id || q.episode_id === f.matched_media.id))
        ));

        const isJobBusy = isActiveTranscoding || isQueued;
        const isRowDisabled = isChecked || isJobBusy;

        let cardBorderClass = 'border-neutral-800';
        if (isActiveTranscoding) {
          cardBorderClass = 'border-amber-500/60 bg-amber-950/20 ring-1 ring-amber-500/30';
        } else if (isQueued) {
          cardBorderClass = 'border-amber-500/30 bg-neutral-900/80';
        } else if (isChecked) {
          cardBorderClass = 'border-red-500/60 bg-red-950/15 ring-1 ring-red-500/30';
        } else if (isPublished) {
          cardBorderClass = 'border-neutral-800/80 bg-neutral-900/50';
        }

        let actionButtonHtml = '';
        if (f.is_raw_source_removed) {
          actionButtonHtml = `
            <button type="button" data-id="${escapeHtml(f.media_id || f.matched_media?.id || f.target_id || '')}" onclick="openDeleteModal(this.dataset.id)" class="px-3.5 py-1.5 bg-red-600/20 hover:bg-red-600 text-red-300 hover:text-white border border-red-500/40 rounded-lg text-xs font-bold transition cursor-pointer flex items-center gap-1.5 shadow">
              <span>🗑️ Delete from Library</span>
            </button>
          `;
        } else if (isActiveTranscoding) {
          const pct = Math.round(window.currentActiveJob.progress_pct || 0);
          actionButtonHtml = `
            <button id="btn-ingest-${safeId}" disabled class="single-ingest-btn px-3.5 py-1.5 bg-amber-600/30 text-amber-300 border border-amber-500/50 rounded-lg text-xs font-bold flex items-center gap-1.5 cursor-not-allowed">
              <svg class="animate-spin -ml-0.5 mr-1.5 h-3.5 w-3.5 text-amber-300 inline" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
              </svg>
              <span>⏳ Processing (${pct}%)...</span>
            </button>
          `;
        } else if (isQueued) {
          actionButtonHtml = `
            <div class="flex items-center gap-1.5">
              <button id="btn-ingest-${safeId}" disabled class="single-ingest-btn px-3 py-1.5 bg-neutral-800 text-amber-400 border border-neutral-700 rounded-lg text-xs font-bold flex items-center gap-1.5 cursor-not-allowed">
                <span>🕒 Queued...</span>
              </button>
              <button type="button" data-identifier="${escapeHtml(f.filename)}" onclick="cancelQueuedTranscodeJob(this.dataset.identifier, this)" class="px-2.5 py-1.5 bg-red-600/20 hover:bg-red-600 text-red-300 hover:text-white border border-red-500/40 rounded-lg text-xs font-bold transition flex items-center gap-1 cursor-pointer shadow">
                <span>✕ Cancel Queue</span>
              </button>
            </div>
          `;
        } else {
          actionButtonHtml = `
            <button data-filename="${escapeHtml(f.filename)}" onclick="openIngestConfigModal(this.dataset.filename)" id="btn-ingest-${safeId}" ${isChecked ? 'disabled' : ''} class="single-ingest-btn px-3.5 py-1.5 ${isPublished ? 'bg-neutral-800 hover:bg-neutral-700 text-neutral-300 border border-neutral-700' : 'bg-red-600 hover:bg-red-700 text-white shadow'} disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-bold transition cursor-pointer flex items-center gap-1.5">
              <span>${actionBtnLabel}</span>
            </button>
          `;
        }

        return `
          <div id="incoming-row-${safeId}" class="bg-neutral-900 border ${cardBorderClass} p-3.5 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 hover:border-neutral-700 transition">
            <div class="flex items-center gap-3 min-w-0">
              <input type="checkbox" data-filename="${escapeHtml(f.filename)}" onchange="toggleFileSelect(this.dataset.filename, this.checked)" ${isChecked ? 'checked' : ''} ${isJobBusy ? 'disabled' : ''} class="incoming-checkbox w-4 h-4 rounded accent-red-600 ${isJobBusy ? 'opacity-40 pointer-events-none' : 'cursor-pointer'} flex-shrink-0">
              <div class="space-y-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="font-bold text-white font-mono text-xs truncate">${escapeHtml(f.filename)}</span>
                  <span class="text-neutral-500 text-[10px]">(${escapeHtml(f.size_mb)} MB)</span>
                  ${statusBadge}
                  ${epInfo}
                  <span id="bulk-badge-${safeId}" class="${isChecked ? '' : 'hidden'} text-[10px] bg-red-950/80 text-red-300 border border-red-500/40 px-2 py-0.5 rounded-full font-semibold flex items-center gap-1">
                    <span>🔒 Controlled by Bulk Bar</span>
                  </span>
                </div>
              </div>
            </div>

            <div class="flex items-center gap-2 flex-shrink-0">
              ${actionButtonHtml}
              ${!f.is_raw_source_removed ? `
                <button type="button" data-filename="${escapeHtml(f.filename)}" onclick="deleteIncomingFile(this.dataset.filename, ${f.size_mb}, ${isPublished}, this)" ${isRowDisabled ? 'disabled' : ''} title="Delete raw file from incoming to reclaim disk space" class="px-2.5 py-1.5 bg-[#141414] hover:bg-red-950/80 text-neutral-400 hover:text-red-400 border border-neutral-700 hover:border-red-800/60 rounded-lg text-xs font-semibold transition cursor-pointer flex items-center gap-1 disabled:opacity-40 disabled:pointer-events-none">
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                  <span>Delete Raw</span>
                </button>
              ` : ''}
            </div>
          </div>
        `;
      }).join('');

      if (pInfo) {
        const start = total === 0 ? 0 : (incomingCurrentPage - 1) * incomingPageSize + 1;
        const end = Math.min(incomingCurrentPage * incomingPageSize, total);
        pInfo.innerText = `Showing ${start} to ${end} of ${total} files`;
      }
      if (pDisplay) {
        pDisplay.innerText = `Page ${incomingCurrentPage} of ${totalPages}`;
      }
      if (btnFirst) btnFirst.disabled = (incomingCurrentPage <= 1);
      if (btnPrev) btnPrev.disabled = (incomingCurrentPage <= 1);
      if (btnNext) btnNext.disabled = (incomingCurrentPage >= totalPages);
      if (btnLast) btnLast.disabled = (incomingCurrentPage >= totalPages);

      updateSelectedIncomingUI();
    }

    let ingestModalItems = [];

    async function openMultiIngestConfigModal(filenames) {
      if (!filenames || filenames.length === 0) return;
      ingestModalItems = [];

      const modal = document.getElementById('ingest-config-modal');
      if (!modal) return;
      modal.classList.remove('hidden');
      updateModalOpenClass();

      const titleEl = document.getElementById('ingest-modal-title');
      const typeBadgeEl = document.getElementById('ingest-modal-type-badge');
      const filenameEl = document.getElementById('ingest-modal-filename');
      const videoBadgeEl = document.getElementById('ingest-modal-video-badge');
      const loadingEl = document.getElementById('ingest-modal-loading');
      const bodyEl = document.getElementById('ingest-modal-body');
      const submitBtn = document.getElementById('btn-submit-ingest-config');
      const submitBtnText = document.getElementById('btn-submit-ingest-text');
      const cancelBtn = document.getElementById('btn-cancel-ingest-modal');
      const closeXBtn = document.getElementById('btn-close-x-ingest-modal');

      if (submitBtn) submitBtn.disabled = true;
      if (cancelBtn) cancelBtn.disabled = false;
      if (closeXBtn) closeXBtn.disabled = false;
      if (bodyEl) {
        bodyEl.classList.add('hidden');
        bodyEl.classList.remove('pointer-events-none', 'opacity-60');
      }
      if (loadingEl) {
        loadingEl.classList.remove('hidden');
        loadingEl.innerHTML = `
          <svg class="animate-spin h-8 w-8 text-red-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
          </svg>
          <span class="text-xs text-neutral-400 font-medium">Probing audio &amp; subtitle streams for ${filenames.length} file${filenames.length === 1 ? '' : 's'} with ffprobe...</span>
        `;
      }

      if (filenames.length === 1) {
        if (titleEl) titleEl.innerText = 'Ingest & Stream Configuration';
        if (typeBadgeEl) {
          typeBadgeEl.innerText = 'Probing Container...';
          typeBadgeEl.className = 'px-2 py-0.5 bg-neutral-800 text-neutral-300 border border-neutral-700 rounded text-[10px] font-bold';
        }
        if (filenameEl) filenameEl.innerText = filenames[0];
        if (videoBadgeEl) {
          videoBadgeEl.innerText = 'Probing...';
          videoBadgeEl.className = 'px-2 py-0.5 bg-neutral-800 text-neutral-400 border border-neutral-700 rounded text-[10px] font-mono';
        }
        if (submitBtnText) submitBtnText.innerText = '🚀 Ingest & Publish';
      } else {
        if (titleEl) titleEl.innerText = 'Bulk Ingest & Stream Configuration';
        if (typeBadgeEl) {
          typeBadgeEl.innerText = `Bulk: ${filenames.length} Files`;
          typeBadgeEl.className = 'px-2 py-0.5 bg-red-950 text-red-300 border border-red-500/40 rounded text-[10px] font-bold';
        }
        if (filenameEl) filenameEl.innerText = `${filenames.length} files selected across current page`;
        if (videoBadgeEl) {
          videoBadgeEl.innerText = 'Multi-Item Ingest';
          videoBadgeEl.className = 'px-2 py-0.5 bg-neutral-800 text-neutral-400 border border-neutral-700 rounded text-[10px] font-mono';
        }
        if (submitBtnText) submitBtnText.innerText = `🚀 Ingest & Publish (${filenames.length})`;
      }

      try {
        const probedItems = [];
        const concurrency = 2;
        for (let c = 0; c < filenames.length; c += concurrency) {
          const chunk = filenames.slice(c, c + concurrency);
          const chunkRes = await Promise.all(chunk.map(async (filename, idxInChunk) => {
            const itemIdx = c + idxInChunk;
            try {
              const res = await fetch(`/api/admin/incoming-files/${encodeURIComponent(filename)}/probe`);
              if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: 'Failed to probe file' }));
                throw new Error(err.detail || 'Probe failed');
              }
              const data = await res.json();
              const embedded_audio = (data.embedded_audio || []).map((a, aIdx) => ({
                raw_stream_idx: a.raw_stream_idx,
                codec: a.codec || 'aac',
                channels: a.channels || 2,
                lang: a.lang || 'en',
                raw_title: a.raw_title || '',
                clean_label: cleanTrackWatermarks(a.clean_label || `Audio ${a.raw_stream_idx}`),
                is_default: Boolean(a.is_default),
                included: a.included !== false
              }));

              // Ensure at least one audio track is default
              if (embedded_audio.length > 0 && !embedded_audio.some(a => a.is_default && a.included)) {
                embedded_audio[0].is_default = true;
                embedded_audio[0].included = true;
              }

              const initialContainerSubs = (data.embedded_subtitles || []).map(s => ({
                id: `stream_${s.raw_stream_idx}`,
                raw_stream_idx: s.raw_stream_idx,
                codec: s.codec || 'subrip',
                lang: s.lang || 'en',
                raw_title: s.raw_title || '',
                label: cleanTrackWatermarks(s.clean_label || `Subtitle ${s.raw_stream_idx}`),
                source_type: 'container',
                source_badge: `STREAM #${s.raw_stream_idx} (${(s.codec || 'SRT').toUpperCase()})`,
                included: true
              }));

              let subtitles = [];
              if (Array.isArray(data.subtitles)) {
                subtitles = data.subtitles.map((s, sIdx) => ({
                  id: s.id || `sub_${sIdx}`,
                  raw_stream_idx: s.raw_stream_idx !== undefined ? s.raw_stream_idx : -1,
                  codec: s.codec || 'subrip',
                  lang: s.lang || 'en',
                  raw_title: s.raw_title || '',
                  label: cleanTrackWatermarks(s.label || `Subtitle ${sIdx + 1}`),
                  vtt_path: s.vtt_path,
                  path: s.path,
                  filename: s.filename,
                  source_type: s.source_type || 'container',
                  source_badge: s.source_badge || 'SUBTITLE',
                  ext_id: s.ext_id,
                  included: true
                }));
              } else if (!data.is_reingest) {
                subtitles = initialContainerSubs.map(s => ({ ...s }));
              } else {
                subtitles = [];
              }

              return {
                itemIdx,
                filename: data.filename || filename,
                display_title: data.display_title || filename,
                is_reingest: Boolean(data.is_reingest),
                video_info: data.video_info || {},
                embedded_audio,
                initialContainerSubs,
                subtitles
              };
            } catch (e) {
              return {
                itemIdx,
                filename,
                error: e.message,
                display_title: filename,
                is_reingest: false,
                video_info: {},
                embedded_audio: [],
                initialContainerSubs: [],
                subtitles: []
              };
            }
          }));
          probedItems.push(...chunkRes);
        }

        ingestModalItems = probedItems;

        // If single file, update modal header with specific file details
        if (filenames.length === 1 && ingestModalItems[0] && !ingestModalItems[0].error) {
          const item = ingestModalItems[0];
          if (titleEl) titleEl.innerText = item.display_title;
          if (typeBadgeEl) {
            if (item.is_reingest) {
              typeBadgeEl.innerText = '↻ Re-Ingest Published';
              typeBadgeEl.className = 'px-2 py-0.5 bg-amber-500/20 text-amber-300 border border-amber-500/40 rounded text-[10px] font-bold';
            } else {
              typeBadgeEl.innerText = '🚀 New Ingest';
              typeBadgeEl.className = 'px-2 py-0.5 bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 rounded text-[10px] font-bold';
            }
          }
          if (submitBtnText) {
            submitBtnText.innerText = item.is_reingest ? '↻ Re-Ingest & Publish' : '🚀 Ingest & Publish';
          }
          if (videoBadgeEl) {
            const v = item.video_info || {};
            const resText = v.height ? `${v.height}p` : 'Video';
            const codecText = (v.codec || 'H.264').toUpperCase();
            videoBadgeEl.innerText = `${resText} • ${codecText}`;
            videoBadgeEl.className = 'px-2 py-0.5 bg-cyan-950/80 text-cyan-300 border border-cyan-500/40 rounded text-[10px] font-mono font-bold';
          }
        }

        renderAllIngestModalCards();

        if (loadingEl) loadingEl.classList.add('hidden');
        if (bodyEl) bodyEl.classList.remove('hidden');
        if (submitBtn) submitBtn.disabled = false;
      } catch (err) {
        if (loadingEl) {
          loadingEl.innerHTML = `
            <div class="text-center space-y-3">
              <div class="text-red-400 font-bold text-sm">Failed to probe files</div>
              <div class="text-neutral-400 text-xs">${escapeHtml(err.message)}</div>
              <button type="button" onclick="closeIngestConfigModal()" class="px-4 py-1.5 bg-neutral-800 hover:bg-neutral-700 text-white rounded-lg text-xs font-semibold cursor-pointer">Close</button>
            </div>
          `;
        }
      }
    }

    function openIngestConfigModal(filename) {
      openMultiIngestConfigModal([filename]);
    }

    function openBulkIngestModal() {
      const pageFiles = getCurrentPageFileList();
      const validSelected = pageFiles.filter(f => selectedIncoming.has(f.filename) && !f.is_raw_source_removed).map(f => f.filename);
      if (validSelected.length === 0) {
        showAdminToast('Please select at least 1 incoming file with raw source to ingest.', 'warning');
        return;
      }
      openMultiIngestConfigModal(validSelected);
    }

    function renderAllIngestModalCards() {
      const bodyEl = document.getElementById('ingest-modal-body');
      if (!bodyEl) return;

      if (ingestModalItems.length === 0) {
        bodyEl.innerHTML = `<div class="text-neutral-400 text-xs text-center py-8">No files to configure.</div>`;
        return;
      }

      bodyEl.innerHTML = ingestModalItems.map((item, itemIdx) => {
        if (item.error) {
          return `
            <div id="ingest-card-${itemIdx}" class="bg-[#181818]/90 border border-red-500/40 rounded-2xl p-5 space-y-3 shadow-xl">
              <div class="flex items-center gap-2">
                <span class="px-2 py-0.5 bg-neutral-800 text-neutral-300 font-mono text-[10px] font-bold rounded">#${itemIdx + 1}</span>
                <span class="font-mono text-xs text-white font-bold truncate">${escapeHtml(item.filename)}</span>
                <span class="px-2 py-0.5 bg-red-950 text-red-400 border border-red-500/40 rounded text-[10px] font-bold">Probe Error</span>
              </div>
              <div class="text-xs text-red-400">${escapeHtml(item.error)}</div>
            </div>
          `;
        }

        const v = item.video_info || {};
        const resText = v.height ? `${v.height}p • ${(v.codec || 'H.264').toUpperCase()}` : '';
        const hasRemovedContainer = (item.initialContainerSubs || []).some(initSub => 
          !(item.subtitles || []).some(s => s.raw_stream_idx === initSub.raw_stream_idx && s.raw_stream_idx >= 0)
        );

        return `
          <div id="ingest-card-${itemIdx}" class="bg-[#181818]/90 border border-neutral-700/80 rounded-2xl p-5 space-y-5 shadow-xl">
            <!-- Card Header (Per Media File) -->
            <div class="flex items-center justify-between gap-3 border-b border-neutral-800 pb-3">
              <div class="space-y-1 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="px-2 py-0.5 bg-neutral-800 text-neutral-300 font-mono text-[10px] font-bold rounded">#${itemIdx + 1}</span>
                  <h4 class="text-sm font-bold text-white truncate max-w-md">${escapeHtml(item.display_title)}</h4>
                  ${item.is_reingest ? '<span class="px-2 py-0.5 bg-amber-500/20 text-amber-300 border border-amber-500/40 rounded text-[10px] font-bold">↻ Re-Ingest Published</span>' : '<span class="px-2 py-0.5 bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 rounded text-[10px] font-bold">🚀 New Ingest</span>'}
                </div>
                <div class="flex items-center gap-2 text-xs text-neutral-400 flex-wrap">
                  <span class="font-mono text-neutral-300 truncate max-w-sm">${escapeHtml(item.filename)}</span>
                  ${resText ? `<span class="px-2 py-0.5 bg-cyan-950/80 text-cyan-300 border border-cyan-500/40 rounded text-[10px] font-mono font-bold">${escapeHtml(resText)}</span>` : ''}
                </div>
              </div>
            </div>

            <!-- Audio Tracks Section -->
            <div class="space-y-2">
              <div class="flex items-center justify-between">
                <label class="text-xs font-bold text-white flex items-center gap-1.5">
                  <span>🔊 AUDIO TRACKS</span>
                  <span id="ingest-audio-count-badge-${itemIdx}" class="text-[11px] font-normal text-neutral-400">(${item.embedded_audio.length} discovered in container)</span>
                </label>
                <span class="text-[11px] text-neutral-500">Select tracks and choose primary default</span>
              </div>
              <div id="ingest-audio-tracks-list-${itemIdx}" class="space-y-2">
                ${renderItemAudioRows(itemIdx)}
              </div>
            </div>

            <!-- Subtitle Tracks Section -->
            <div class="space-y-2 border-t border-neutral-800 pt-4">
              <div class="flex items-center justify-between flex-wrap gap-2">
                <label class="text-xs font-bold text-white flex items-center gap-1.5">
                  <span>💬 SUBTITLE TRACKS</span>
                  <span id="ingest-subs-count-badge-${itemIdx}" class="text-[11px] font-normal text-neutral-400">(${item.subtitles.length} active subtitles)</span>
                </label>
                <div class="flex items-center gap-2">
                  <button type="button" onclick="removeAllIngestSubtitles(${itemIdx})" class="px-2 py-1 bg-red-950/40 hover:bg-red-900/60 text-red-400 border border-red-500/30 rounded text-[11px] font-medium transition cursor-pointer">
                    🗑️ Remove All Subtitles
                  </button>
                  <button type="button" id="btn-restore-container-subs-${itemIdx}" onclick="restoreContainerSubtitles(${itemIdx})" class="${hasRemovedContainer ? '' : 'hidden'} px-2 py-1 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 border border-neutral-700 rounded text-[11px] font-medium transition cursor-pointer">
                    ↻ Restore Container Subtitles
                  </button>
                </div>
              </div>
              <div id="ingest-subs-tracks-list-${itemIdx}" class="space-y-2">
                ${renderItemSubtitleRows(itemIdx)}
              </div>

              <!-- + Add Subtitle File from Device (.srt, .vtt, .ass) -->
              <div class="p-3 bg-neutral-900/60 border border-neutral-800 rounded-xl space-y-2 mt-2">
                <div class="text-[11px] text-neutral-400 font-semibold">+ Add Subtitle File from Device (.srt, .vtt, .ass)</div>
                <div class="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                  <input type="file" id="ingest-modal-sub-file-${itemIdx}" accept=".srt,.vtt,.ass" class="text-xs text-neutral-400 file:mr-2 file:py-1 file:px-2.5 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-neutral-800 file:text-neutral-200 hover:file:bg-neutral-700 cursor-pointer flex-1">
                  <div id="ingest-modal-sub-lang-container-${itemIdx}">
                    ${renderCustomLangDropdown({
                      id: 'ingest-modal-sub-lang-' + itemIdx,
                      className: '',
                      dataIdx: itemIdx,
                      currentCode: 'en',
                      langList: ALL_SUBTITLE_LANGS.filter(l => l !== 'None'),
                      onChangeFnName: null
                    })}
                  </div>
                  <input type="text" id="ingest-modal-sub-label-${itemIdx}" placeholder="Display Label (e.g. English CC)" class="w-40 bg-black border border-neutral-700 rounded-lg px-2.5 py-1 text-xs text-white focus:outline-none focus:border-red-500">
                  <button type="button" id="btn-ingest-attach-sub-${itemIdx}" onclick="attachExternalSubInModal(${itemIdx})" class="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 text-white rounded-lg text-xs font-bold transition cursor-pointer flex items-center justify-center gap-1 border border-neutral-700">
                    + Add
                  </button>
                </div>
                <div id="ingest-sub-upload-msg-${itemIdx}" class="text-[11px] text-emerald-400 hidden"></div>
              </div>
            </div>
          </div>
        `;
      }).join('');
    }

    function renderItemAudioRows(itemIdx) {
      const item = ingestModalItems[itemIdx];
      if (!item || !item.embedded_audio) return '';
      if (item.embedded_audio.length === 0) {
        return `<div class="text-neutral-500 italic text-xs py-2">No audio tracks detected by ffprobe.</div>`;
      }

      return item.embedded_audio.map(a => {
        const rawIdx = a.raw_stream_idx;
        const isDef = !!a.is_default;
        const isChecked = a.included !== false;
        const cleanLabel = cleanTrackWatermarks(a.clean_label || `Audio ${rawIdx}`);
        const channelsText = a.channels ? `${a.channels}ch` : '';
        const codecText = (a.codec || 'AAC').toUpperCase();

        return `
          <div id="ingest-audio-row-${itemIdx}-${rawIdx}" class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 bg-neutral-900/90 border ${isDef ? 'border-amber-500/40 bg-amber-950/10' : 'border-neutral-800'} rounded-xl hover:border-neutral-700 transition">
            <div class="flex items-center gap-3 min-w-0">
              <input type="checkbox" id="ingest-audio-cb-${itemIdx}-${rawIdx}" data-item-idx="${itemIdx}" data-stream-idx="${rawIdx}" ${isChecked ? 'checked' : ''} onchange="onIngestAudioCheckChange(${itemIdx}, ${rawIdx})" class="w-4 h-4 rounded accent-red-600 cursor-pointer flex-shrink-0">
              <div class="space-y-0.5 min-w-0">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-[11px] text-neutral-400 font-bold">Stream #${rawIdx}</span>
                  <span class="px-2 py-0.5 bg-neutral-800 text-neutral-300 rounded text-[10px] font-mono font-semibold">${escapeHtml(codecText)} ${escapeHtml(channelsText)}</span>
                  <span id="ingest-audio-def-badge-${itemIdx}-${rawIdx}" class="${isDef ? '' : 'hidden'} px-2 py-0.5 bg-amber-500/20 text-amber-300 border border-amber-500/30 rounded text-[10px] font-bold">Default</span>
                </div>
                ${a.raw_title ? `<div class="text-[11px] text-neutral-500 truncate max-w-sm" title="${escapeHtml(a.raw_title)}">Tag: ${escapeHtml(a.raw_title)}</div>` : ''}
              </div>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">
              ${renderCustomLangDropdown({
                id: `ingest-audio-lang-${itemIdx}-${rawIdx}`,
                className: '',
                dataIdx: `${itemIdx}_${rawIdx}`,
                currentCode: a.lang || 'en',
                langList: ALL_AUDIO_LANGS,
                onChangeFnName: null
              })}
              <input type="text" id="ingest-audio-label-${itemIdx}-${rawIdx}" value="${escapeHtml(cleanLabel)}" placeholder="Display Label" class="w-40 bg-black border border-neutral-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-red-500">
              <button type="button" onclick="setIngestDefaultAudio(${itemIdx}, ${rawIdx})" id="ingest-audio-def-btn-${itemIdx}-${rawIdx}" data-default="${isDef ? 'true' : 'false'}" class="px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-1 cursor-pointer ${isDef ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40' : 'bg-neutral-800 text-neutral-400 hover:text-white border border-transparent'}">
                <span>${isDef ? '★ Default' : '☆ Set Default'}</span>
              </button>
            </div>
          </div>
        `;
      }).join('');
    }

    function setIngestDefaultAudio(itemIdx, targetRawIdx) {
      const item = ingestModalItems[itemIdx];
      if (!item) return;

      const targetCb = document.getElementById(`ingest-audio-cb-${itemIdx}-${targetRawIdx}`);
      if (targetCb) targetCb.checked = true;

      item.embedded_audio.forEach(a => {
        const isTarget = (a.raw_stream_idx === targetRawIdx);
        if (isTarget) {
          a.is_default = true;
          a.included = true;
        } else {
          a.is_default = false;
        }

        const rawIdx = a.raw_stream_idx;
        const row = document.getElementById(`ingest-audio-row-${itemIdx}-${rawIdx}`);
        const btn = document.getElementById(`ingest-audio-def-btn-${itemIdx}-${rawIdx}`);
        const badge = document.getElementById(`ingest-audio-def-badge-${itemIdx}-${rawIdx}`);

        if (btn) btn.setAttribute('data-default', isTarget ? 'true' : 'false');
        if (isTarget) {
          if (row) row.className = 'flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 bg-neutral-900/90 border border-amber-500/40 bg-amber-950/10 rounded-xl transition';
          if (btn) {
            btn.innerHTML = '<span>★ Default</span>';
            btn.className = 'px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-1 cursor-pointer bg-amber-500/20 text-amber-400 border border-amber-500/40';
          }
          if (badge) badge.classList.remove('hidden');
        } else {
          if (row) row.className = 'flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 bg-neutral-900/90 border border-neutral-800 rounded-xl hover:border-neutral-700 transition';
          if (btn) {
            btn.innerHTML = '<span>☆ Set Default</span>';
            btn.className = 'px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-1 cursor-pointer bg-neutral-800 text-neutral-400 hover:text-white border border-transparent';
          }
          if (badge) badge.classList.add('hidden');
        }
      });
    }

    function onIngestAudioCheckChange(itemIdx, rawIdx) {
      const item = ingestModalItems[itemIdx];
      if (!item) return;
      const cb = document.getElementById(`ingest-audio-cb-${itemIdx}-${rawIdx}`);
      const isDef = document.getElementById(`ingest-audio-def-btn-${itemIdx}-${rawIdx}`)?.getAttribute('data-default') === 'true';

      const track = item.embedded_audio.find(a => a.raw_stream_idx === rawIdx);
      if (track && cb) {
        track.included = cb.checked;
      }

      if (cb && !cb.checked && isDef) {
        const remainingChecked = item.embedded_audio.find(a => {
          const c = document.getElementById(`ingest-audio-cb-${itemIdx}-${a.raw_stream_idx}`);
          return c && c.checked;
        });
        if (remainingChecked) {
          setIngestDefaultAudio(itemIdx, remainingChecked.raw_stream_idx);
        }
      }
    }

    function renderItemSubtitleRows(itemIdx) {
      const item = ingestModalItems[itemIdx];
      if (!item || !item.subtitles) return '';

      if (item.subtitles.length === 0) {
        return `
          <div class="p-4 bg-neutral-900/60 border border-neutral-800 rounded-xl text-center space-y-1">
            <div class="text-xs text-neutral-400 font-semibold">No active subtitle tracks.</div>
            <div class="text-[11px] text-neutral-500">Subtitles will be stripped upon publish, or you can add a subtitle file below.</div>
          </div>
        `;
      }

      return item.subtitles.map((s, subIdx) => {
        const rawIdx = s.raw_stream_idx;
        const isSaved = s.source_type === 'saved';
        const isExternal = s.source_type === 'external';
        const badgeText = s.source_badge || (isSaved ? 'SAVED IN LIBRARY' : (isExternal ? 'EXTERNAL .SRT' : `STREAM #${rawIdx} (SRT)`));
        const badgeClass = isSaved 
          ? 'bg-amber-950/80 text-amber-300 border border-amber-500/40' 
          : (isExternal 
            ? 'bg-cyan-950/80 text-cyan-300 border border-cyan-500/40' 
            : 'bg-neutral-800 text-neutral-300 border border-neutral-700');
        const cleanLabel = cleanTrackWatermarks(s.label || `Subtitle ${subIdx + 1}`);

        return `
          <div id="ingest-sub-row-${itemIdx}-${subIdx}" class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 bg-neutral-900/90 border border-neutral-800 rounded-xl hover:border-neutral-700 transition">
            <div class="flex items-center gap-3 min-w-0">
              <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold flex-shrink-0 ${badgeClass}">${escapeHtml(badgeText)}</span>
              <div class="space-y-0.5 min-w-0">
                <div class="text-xs font-semibold text-white truncate max-w-xs" title="${escapeHtml(cleanLabel)}">${escapeHtml(cleanLabel)}</div>
                ${s.filename ? `<div class="text-[11px] text-neutral-500 truncate max-w-xs font-mono" title="${escapeHtml(s.filename)}">${escapeHtml(s.filename)}</div>` : ''}
              </div>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">
              ${renderCustomLangDropdown({
                id: `ingest-sub-lang-${itemIdx}-${subIdx}`,
                className: '',
                dataIdx: `${itemIdx}_${subIdx}`,
                currentCode: s.lang || 'en',
                langList: ALL_SUBTITLE_LANGS.filter(l => l !== 'None'),
                onChangeFnName: 'updateItemSubLangFromDropdown'
              })}
              <input type="text" id="ingest-sub-label-${itemIdx}-${subIdx}" value="${escapeHtml(cleanLabel)}" oninput="updateItemSubLabel(${itemIdx}, ${subIdx}, this.value)" placeholder="Display Label" class="w-44 bg-black border border-neutral-700 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-red-500">
              <button type="button" onclick="removeIngestSubtitle(${itemIdx}, ${subIdx})" class="px-3 py-1.5 bg-red-950/50 hover:bg-red-900 text-red-300 hover:text-white border border-red-500/40 rounded-lg text-xs font-bold transition cursor-pointer flex items-center gap-1 shadow">
                <span>🗑️ Remove</span>
              </button>
            </div>
          </div>
        `;
      }).join('');
    }

    function refreshItemSubtitlesUI(itemIdx) {
      const item = ingestModalItems[itemIdx];
      if (!item) return;

      const cont = document.getElementById(`ingest-subs-tracks-list-${itemIdx}`);
      const badge = document.getElementById(`ingest-subs-count-badge-${itemIdx}`);
      const restoreBtn = document.getElementById(`btn-restore-container-subs-${itemIdx}`);

      if (badge) badge.innerText = `(${item.subtitles.length} active subtitle${item.subtitles.length === 1 ? '' : 's'})`;
      if (cont) cont.innerHTML = renderItemSubtitleRows(itemIdx);

      const hasRemovedContainer = (item.initialContainerSubs || []).some(initSub => 
        !item.subtitles.some(s => s.raw_stream_idx === initSub.raw_stream_idx && s.raw_stream_idx >= 0)
      );
      if (restoreBtn) {
        if (hasRemovedContainer) {
          restoreBtn.classList.remove('hidden');
        } else {
          restoreBtn.classList.add('hidden');
        }
      }
    }

    function updateItemSubLangFromDropdown(compoundIdx, val) {
      const parts = String(compoundIdx).split('_');
      if (parts.length >= 2) {
        const itemIdx = parseInt(parts[0], 10);
        const subIdx = parseInt(parts[1], 10);
        updateItemSubLang(itemIdx, subIdx, val);
      }
    }
    window.updateItemSubLangFromDropdown = updateItemSubLangFromDropdown;

    function updateItemSubLang(itemIdx, subIdx, val) {
      const item = ingestModalItems[itemIdx];
      if (item && item.subtitles[subIdx]) {
        item.subtitles[subIdx].lang = val;
        const langName = getLangName(val) || val;
        const inp = document.getElementById(`ingest-sub-label-${itemIdx}-${subIdx}`);
        if (inp) {
          const curVal = inp.value.trim();
          const isGeneric = !curVal || ALL_SUBTITLE_LANGS.some(code => getLangName(code) === curVal || code === curVal) || curVal.startsWith('Sub ') || curVal.startsWith('Subtitle ');
          if (isGeneric) {
            inp.value = langName;
            item.subtitles[subIdx].label = langName;
          }
        }
      }
    }

    function updateItemSubLabel(itemIdx, subIdx, val) {
      const item = ingestModalItems[itemIdx];
      if (item && item.subtitles[subIdx]) {
        item.subtitles[subIdx].label = val;
      }
    }

    async function removeIngestSubtitle(itemIdx, subIdx) {
      const item = ingestModalItems[itemIdx];
      if (!item) return;
      const sub = item.subtitles[subIdx];
      if (!sub) return;

      if (sub.ext_id && item.filename) {
        try {
          await fetch(`/api/admin/incoming/external-subtitle?target_filename=${encodeURIComponent(item.filename)}&ext_id=${encodeURIComponent(sub.ext_id)}`, {
            method: 'DELETE'
          });
        } catch (e) {
          console.warn('Failed to delete external sub file:', e);
        }
      }

      item.subtitles.splice(subIdx, 1);
      refreshItemSubtitlesUI(itemIdx);
      showAdminToast(`Removed subtitle track from #${itemIdx + 1}`, 'info');
    }

    function removeAllIngestSubtitles(itemIdx) {
      const item = ingestModalItems[itemIdx];
      if (!item) return;
      item.subtitles.forEach(sub => {
        if (sub.ext_id && item.filename) {
          fetch(`/api/admin/incoming/external-subtitle?target_filename=${encodeURIComponent(item.filename)}&ext_id=${encodeURIComponent(sub.ext_id)}`, {
            method: 'DELETE'
          }).catch(() => {});
        }
      });
      item.subtitles = [];
      refreshItemSubtitlesUI(itemIdx);
      showAdminToast(`All subtitle tracks removed from #${itemIdx + 1}`, 'info');
    }

    function restoreContainerSubtitles(itemIdx) {
      const item = ingestModalItems[itemIdx];
      if (!item) return;
      (item.initialContainerSubs || []).forEach(initSub => {
        const exists = item.subtitles.some(s => s.raw_stream_idx === initSub.raw_stream_idx && s.raw_stream_idx >= 0);
        if (!exists) {
          item.subtitles.push({ ...initSub });
        }
      });
      refreshItemSubtitlesUI(itemIdx);
      showAdminToast(`Restored container subtitle tracks for #${itemIdx + 1}`, 'success');
    }

    async function attachExternalSubInModal(itemIdx) {
      const item = ingestModalItems[itemIdx];
      if (!item) return;

      const fileInput = document.getElementById(`ingest-modal-sub-file-${itemIdx}`);
      const langSelect = document.getElementById(`ingest-modal-sub-lang-${itemIdx}`);
      const labelInput = document.getElementById(`ingest-modal-sub-label-${itemIdx}`);
      const attachBtn = document.getElementById(`btn-ingest-attach-sub-${itemIdx}`);
      const msgEl = document.getElementById(`ingest-sub-upload-msg-${itemIdx}`);

      if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
        showAdminToast('Please select a subtitle file (.srt, .vtt, .ass) to attach.', 'warning');
        return;
      }

      const file = fileInput.files[0];
      const lang = langSelect ? langSelect.value : 'en';
      const label = labelInput ? labelInput.value.trim() : '';

      if (attachBtn) {
        attachBtn.disabled = true;
        attachBtn.innerText = 'Attaching...';
      }
      if (msgEl) {
        msgEl.innerText = `Uploading and converting "${file.name}"...`;
        msgEl.classList.remove('hidden');
      }

      const formData = new FormData();
      formData.append('file', file);
      formData.append('target_filename', item.filename || '');
      formData.append('lang', lang);
      if (label) formData.append('label', label);

      try {
        const res = await fetch('/api/admin/incoming/upload-subtitle', {
          method: 'POST',
          body: formData
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
          throw new Error(err.detail || 'Upload failed');
        }
        const data = await res.json();
        const extSubs = data.external_subtitles || [];
        const addedSub = extSubs[extSubs.length - 1];
        if (addedSub) {
          item.subtitles.push({
            id: addedSub.ext_id,
            ext_id: addedSub.ext_id,
            raw_stream_idx: -1,
            filename: addedSub.filename,
            path: addedSub.path,
            lang: addedSub.lang,
            label: label || addedSub.label || `English (web)`,
            source_type: 'external',
            source_badge: 'EXTERNAL .SRT',
            included: true
          });
          refreshItemSubtitlesUI(itemIdx);
        }

        fileInput.value = '';
        if (labelInput) labelInput.value = '';
        if (msgEl) {
          msgEl.innerText = `✓ Attached "${file.name}" successfully!`;
          setTimeout(() => { msgEl.classList.add('hidden'); }, 4000);
        }
        showAdminToast(`Successfully attached "${file.name}" to #${itemIdx + 1}`, 'success');
      } catch (e) {
        if (msgEl) msgEl.innerText = `Error: ${e.message}`;
        showAdminToast(`Failed to attach subtitle: ${e.message}`, 'error');
      } finally {
        if (attachBtn) {
          attachBtn.disabled = false;
          attachBtn.innerText = '+ Add';
        }
      }
    }

    function closeIngestConfigModal() {
      const modal = document.getElementById('ingest-config-modal');
      if (modal) modal.classList.add('hidden');
      ingestModalItems = [];
      updateModalOpenClass();
    }

    async function submitIngestConfig() {
      const submitBtn = document.getElementById('btn-submit-ingest-config');
      const cancelBtn = document.getElementById('btn-cancel-ingest-modal');
      const closeXBtn = document.getElementById('btn-close-x-ingest-modal');
      const bodyEl = document.getElementById('ingest-modal-body');
      const origHtml = submitBtn ? submitBtn.innerHTML : '';

      if (ingestModalItems.length === 0) {
        closeIngestConfigModal();
        return;
      }

      // 1. Validation: every item must have at least 1 audio track selected
      for (const item of ingestModalItems) {
        if (item.error) continue;
        let selectedAudioCount = 0;
        item.embedded_audio.forEach(a => {
          const cb = document.getElementById(`ingest-audio-cb-${item.itemIdx}-${a.raw_stream_idx}`);
          if (cb && cb.checked) selectedAudioCount++;
        });
        if (selectedAudioCount === 0) {
          showAdminToast(`Please select at least 1 audio track for "${item.filename}"`, 'warning');
          return;
        }
      }

      // 2. Lock modal UI immediately
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `
          <svg class="animate-spin h-3.5 w-3.5 text-white inline mr-1" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
          </svg>
          <span>⏳ Queuing ${ingestModalItems.length} media(s)...</span>
        `;
      }
      if (cancelBtn) cancelBtn.disabled = true;
      if (closeXBtn) closeXBtn.disabled = true;
      if (bodyEl) {
        bodyEl.classList.add('pointer-events-none', 'opacity-60');
      }

      // 3. Build payload items
      const payloadItems = ingestModalItems.filter(item => !item.error).map(item => {
        const audio_tracks_config = [];
        let foundDefault = false;

        item.embedded_audio.forEach(a => {
          const cb = document.getElementById(`ingest-audio-cb-${item.itemIdx}-${a.raw_stream_idx}`);
          if (cb && cb.checked) {
            const lang = document.getElementById(`ingest-audio-lang-${item.itemIdx}-${a.raw_stream_idx}`)?.value || a.lang || 'en';
            const label = document.getElementById(`ingest-audio-label-${item.itemIdx}-${a.raw_stream_idx}`)?.value.trim() || cleanTrackWatermarks(a.clean_label || `Audio ${a.raw_stream_idx}`);
            const isDef = document.getElementById(`ingest-audio-def-btn-${item.itemIdx}-${a.raw_stream_idx}`)?.getAttribute('data-default') === 'true';
            if (isDef) foundDefault = true;

            audio_tracks_config.push({
              raw_stream_idx: a.raw_stream_idx,
              index: audio_tracks_config.length,
              lang,
              label,
              is_default: isDef
            });
          }
        });

        if (!foundDefault && audio_tracks_config.length > 0) {
          audio_tracks_config[0].is_default = true;
        }

        const subtitle_tracks_config = item.subtitles.map((s, sIdx) => {
          const langEl = document.getElementById(`ingest-sub-lang-${item.itemIdx}-${sIdx}`);
          const labelEl = document.getElementById(`ingest-sub-label-${item.itemIdx}-${sIdx}`);
          const lang = langEl ? langEl.value : (s.lang || 'en');
          const label = labelEl ? labelEl.value.trim() : (s.label || `Subtitle ${sIdx + 1}`);

          return {
            id: s.id || `sub_${sIdx}`,
            index: sIdx,
            lang: lang,
            label: label,
            raw_stream_idx: s.raw_stream_idx !== undefined ? s.raw_stream_idx : -1,
            is_external: (s.source_type === 'external' || (s.raw_stream_idx !== undefined && s.raw_stream_idx < 0)),
            is_library: (s.source_type === 'saved'),
            vtt_path: s.vtt_path || undefined,
            path: s.path || undefined,
            ext_id: s.ext_id || undefined,
            included: true
          };
        });

        const audio_languages = audio_tracks_config.map(a => a.lang);
        const subtitle_languages = subtitle_tracks_config.map(s => s.lang);

        return {
          filename: item.filename,
          audio_languages,
          subtitle_languages,
          audio_tracks_config,
          subtitle_tracks_config
        };
      });

      // 4. Optimistically mark items as queued in UI
      window.currentQueuedJobs = window.currentQueuedJobs || [];
      payloadItems.forEach(pi => {
        if (!window.currentQueuedJobs.some(q => q.filename === pi.filename)) {
          window.currentQueuedJobs.push({ filename: pi.filename, progress_pct: 0 });
        }
      });
      renderIncomingFiles();

      try {
        if (payloadItems.length === 1) {
          const res = await fetch('/api/admin/auto-ingest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payloadItems[0])
          });
          const data = await res.json();
          if (res.ok) {
            showAdminToast(`Queued "${payloadItems[0].filename}" for processing!`, 'success');
            selectedIncoming.delete(payloadItems[0].filename);
            closeIngestConfigModal();
            updateSelectedIncomingUI();
            fetchIncomingFiles();
            loadCatalog();
            updateQueueStatus();
          } else {
            showAdminToast('Ingest error: ' + (data.detail || JSON.stringify(data)), 'error');
          }
        } else {
          const res = await fetch('/api/admin/bulk-ingest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items: payloadItems })
          });
          const data = await res.json();
          if (res.ok) {
            showAdminToast(`Successfully queued ${data.total || payloadItems.length} files for ingest!`, 'success');
            payloadItems.forEach(pi => selectedIncoming.delete(pi.filename));
            closeIngestConfigModal();
            updateSelectedIncomingUI();
            fetchIncomingFiles();
            loadCatalog();
            updateQueueStatus();
          } else {
            showAdminToast('Bulk ingest error: ' + (data.detail || JSON.stringify(data)), 'error');
          }
        }
      } catch (e) {
        showAdminToast('Ingest request failed: ' + e.message, 'error');
      } finally {
        if (submitBtn) { submitBtn.disabled = false; submitBtn.innerHTML = origHtml; }
        if (cancelBtn) cancelBtn.disabled = false;
        if (closeXBtn) closeXBtn.disabled = false;
        if (bodyEl) {
          bodyEl.classList.remove('pointer-events-none', 'opacity-60');
        }
      }
    }

    function ingestSingleFile(filename) {
      openIngestConfigModal(filename);
    }

"""
