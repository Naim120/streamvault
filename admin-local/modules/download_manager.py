import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
import httpx
from .common import MEDIA_SERVER_URL, ADMIN_SECRET

router = APIRouter()

@router.get("/api/admin/aria2/downloads")
async def get_aria2_downloads():
    url = f"{MEDIA_SERVER_URL}/api/aria2/downloads"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")

@router.post("/api/admin/aria2/add")
async def add_aria2_download(request: Request):
    payload = await request.json()
    url = f"{MEDIA_SERVER_URL}/api/aria2/add"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(url, headers={"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"}, json=payload)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")

@router.post("/api/admin/aria2/upload-torrent")
async def upload_torrent_file(
    torrent_file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    media_type: Optional[str] = Form("movie")
):
    url = f"{MEDIA_SERVER_URL}/api/aria2/upload-torrent"
    MAX_TORRENT_BYTES = 15 * 1024 * 1024
    content = await torrent_file.read(MAX_TORRENT_BYTES + 1)
    if len(content) > MAX_TORRENT_BYTES:
        raise HTTPException(status_code=413, detail="Torrent file exceeds maximum limit of 15 MB")
    files = {"torrent_file": (torrent_file.filename, content, torrent_file.content_type or "application/x-bittorrent")}
    data = {}
    if title:
        data["title"] = title
    if media_type:
        data["media_type"] = media_type

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers={"X-Admin-Secret": ADMIN_SECRET}, data=data, files=files)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")

@router.delete("/api/admin/aria2/remove/{gid}")
async def remove_aria2_download_proxy(gid: str):
    url = f"{MEDIA_SERVER_URL}/api/aria2/remove/{gid}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.delete(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")

@router.post("/api/admin/aria2/retry/{gid}")
async def retry_aria2_download_proxy(gid: str):
    url = f"{MEDIA_SERVER_URL}/api/aria2/retry/{gid}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")

@router.post("/api/admin/aria2/clear-completed")
async def clear_completed_aria2():
    url = f"{MEDIA_SERVER_URL}/api/aria2/clear-completed"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


DOWNLOAD_MANAGER_HTML = r"""      <section id="tab-aria2" class="space-y-6 hidden">
        <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-6 space-y-4">
          <div class="flex items-center justify-between">
            <h3 class="text-base font-bold text-white flex items-center gap-2">
              <span>Aria2c Media Downloader &amp; Auto-Ingest Hook</span>
            </h3>
            <span id="aria2-status-chip" class="px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider bg-neutral-800 text-neutral-400 border border-neutral-700">
              Checking Daemon...
            </span>
          </div>
          <div class="space-y-3">
            <input type="text" id="aria2-uri" oninput="clearAria2Error()" placeholder="magnet:?xt=urn:btih:... or https://example.com/media.mkv" class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-red-500 font-mono transition">
            
            <!-- Inline Diagnostic Error Banner -->
            <div id="aria2ErrorBanner" class="hidden rounded-xl border border-red-500/50 bg-red-950/40 p-4 text-xs text-red-200 space-y-1.5 transition-all shadow-lg">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2 font-bold text-red-300">
                  <span class="text-sm">⚠️</span>
                  <span id="aria2ErrorBannerTitle">Download Submission Failed</span>
                  <span id="aria2ErrorCode" class="text-[10px] px-2 py-0.5 rounded bg-red-900/60 font-mono text-red-300 border border-red-700/50"></span>
                </div>
                <button type="button" onclick="dismissAria2Banner()" class="text-neutral-400 hover:text-white text-xs cursor-pointer px-2 py-1">&times;</button>
              </div>
              <div id="aria2ErrorDetail" class="text-neutral-300 font-medium"></div>
              <div id="aria2ErrorSuggestion" class="text-[11px] text-amber-300 font-mono bg-black/40 p-2 rounded border border-amber-500/30"></div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <input type="text" id="aria2-title" placeholder="Search TMDB Title (Optional for Auto-Ingest)..." class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-red-500">
              <select id="aria2-type" class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none">
                <option value="movie">Movie</option>
                <option value="series">Series</option>
              </select>
            </div>

            <div class="flex flex-wrap items-center gap-3 pt-1">
              <button onclick="dispatchAria2()" id="btn-dispatch-aria2" class="px-5 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold transition cursor-pointer flex items-center gap-2 shadow-lg shadow-red-600/20">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
                <span>Dispatch Magnet / URL</span>
              </button>

              <label class="px-4 py-2.5 bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 hover:border-neutral-600 text-neutral-300 hover:text-white rounded-xl text-xs font-semibold transition cursor-pointer flex items-center gap-2">
                <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path></svg>
                <span>Upload .torrent File</span>
                <input type="file" id="aria2-torrent-file" accept=".torrent" onchange="handleTorrentFileUpload(event)" class="hidden">
              </label>
            </div>
          </div>
        </div>

        <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-6 space-y-3">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-bold text-neutral-400 uppercase tracking-wider">Active &amp; Completed Downloads</h4>
            <button type="button" onclick="clearCompletedAria2()" class="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 hover:text-white rounded-lg text-[10px] font-semibold transition cursor-pointer flex items-center gap-1.5 border border-neutral-700/60">
              <span>🧹</span>
              <span>Clear Completed / Failed</span>
            </button>
          </div>
          <div id="downloads-list" class="space-y-2 text-xs text-neutral-400">Loading downloads...</div>
        </div>
      </section>
"""

DOWNLOAD_MANAGER_JS = r"""    function clearAria2Error() {
      const input = document.getElementById('aria2-uri');
      if (input) input.classList.remove('border-red-500', 'bg-red-950/20');
      dismissAria2Banner();
    }

    function dismissAria2Banner() {
      const banner = document.getElementById('aria2ErrorBanner');
      if (banner) banner.classList.add('hidden');
    }

    function showAria2ErrorBanner(code, title, detail, suggestion) {
      const banner = document.getElementById('aria2ErrorBanner');
      const titleEl = document.getElementById('aria2ErrorBannerTitle');
      const codeEl = document.getElementById('aria2ErrorCode');
      const detailEl = document.getElementById('aria2ErrorDetail');
      const suggEl = document.getElementById('aria2ErrorSuggestion');

      if (titleEl) titleEl.innerText = title || 'Download Submission Failed';
      if (codeEl) {
        codeEl.innerText = code || 'ERROR';
        codeEl.style.display = code ? 'inline-block' : 'none';
      }
      if (detailEl) detailEl.innerText = detail || 'An unexpected error occurred while adding the download.';
      if (suggEl) {
        if (suggestion) {
          suggEl.innerText = '💡 Suggestion: ' + suggestion;
          suggEl.classList.remove('hidden');
        } else {
          suggEl.classList.add('hidden');
        }
      }
      if (banner) banner.classList.remove('hidden');
    }

    async function dispatchAria2() {
      clearAria2Error();
      const uriInput = document.getElementById('aria2-uri');
      const uri = uriInput ? uriInput.value.trim() : '';
      const title = (document.getElementById('aria2-title')?.value || '').trim();
      const type = document.getElementById('aria2-type')?.value || 'movie';

      if (!uri) {
        if (uriInput) uriInput.classList.add('border-red-500', 'bg-red-950/20');
        showAria2ErrorBanner(
          'EMPTY_INPUT',
          'Empty Download Input',
          'No download link or Magnet URI was entered.',
          'Paste a magnet link (starting with magnet:?xt=urn:btih:) or a direct video URL (http:// or https://).'
        );
        showAdminToast({
          type: 'error',
          title: 'Empty Download Input',
          message: 'Please provide a valid magnet link or direct media URL.',
          suggestion: 'Paste a link or click "Upload .torrent File" below.'
        });
        return;
      }

      const btn = document.getElementById('btn-dispatch-aria2');
      if (btn) btn.disabled = true;

      try {
        const res = await fetch('/api/admin/aria2/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ uri, title, type })
        });

        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
          if (uriInput) uriInput.classList.add('border-red-500', 'bg-red-950/20');
          const errPayload = (data.detail && typeof data.detail === 'object') ? data.detail : data;
          const errCode = errPayload.error_code || `HTTP_${res.status}`;
          const errTitle = errPayload.title || 'Download Request Failed';
          const errDetail = errPayload.detail || (typeof data.detail === 'string' ? data.detail : 'The download could not be added.');
          const errSuggestion = errPayload.suggestion || '';

          showAria2ErrorBanner(errCode, errTitle, errDetail, errSuggestion);
          showAdminToast({
            type: 'error',
            title: errTitle,
            message: errDetail,
            suggestion: errSuggestion
          });
        } else {
          if (uriInput) uriInput.value = '';
          const titleInput = document.getElementById('aria2-title');
          if (titleInput) titleInput.value = '';
          clearAria2Error();
          showAdminToast({
            type: 'success',
            title: 'Download Queued',
            message: `Task successfully dispatched to Aria2c daemon (GID: ${data.gid || 'queued'}).`
          });
          await fetchDownloads();
        }
      } catch (err) {
        if (uriInput) uriInput.classList.add('border-red-500', 'bg-red-950/20');
        showAria2ErrorBanner(
          'NETWORK_ERROR',
          'Connection Error',
          `Failed to contact Media Server: ${err.message}`,
          'Ensure the Media Server daemon is running on port 8000.'
        );
        showAdminToast({
          type: 'error',
          title: 'Network Error',
          message: `Could not reach server: ${err.message}`,
          suggestion: 'Check if ./start.sh is running.'
        });
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function handleTorrentFileUpload(event) {
      clearAria2Error();
      const fileInput = event.target;
      if (!fileInput.files || fileInput.files.length === 0) return;
      const file = fileInput.files[0];
      const title = (document.getElementById('aria2-title')?.value || '').trim();
      const type = document.getElementById('aria2-type')?.value || 'movie';

      const formData = new FormData();
      formData.append('torrent_file', file);
      if (title) formData.append('title', title);
      formData.append('media_type', type);

      showAdminToast({
        type: 'info',
        title: 'Uploading Torrent',
        message: `Parsing and validating "${file.name}"...`
      });

      try {
        const res = await fetch('/api/admin/aria2/upload-torrent', {
          method: 'POST',
          body: formData
        });
        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
          const errPayload = (data.detail && typeof data.detail === 'object') ? data.detail : data;
          const errCode = errPayload.error_code || 'TORRENT_UPLOAD_ERROR';
          const errTitle = errPayload.title || 'Torrent Upload Failed';
          const errDetail = errPayload.detail || (typeof data.detail === 'string' ? data.detail : 'Could not validate or add .torrent file.');
          const errSuggestion = errPayload.suggestion || 'Ensure file is a valid non-empty bencoded .torrent file.';

          showAria2ErrorBanner(errCode, errTitle, errDetail, errSuggestion);
          showAdminToast({
            type: 'error',
            title: errTitle,
            message: errDetail,
            suggestion: errSuggestion
          });
        } else {
          clearAria2Error();
          fileInput.value = '';
          showAdminToast({
            type: 'success',
            title: 'Torrent Added',
            message: `Successfully queued "${file.name}" in Aria2c (GID: ${data.gid || 'queued'}).`
          });
          await fetchDownloads();
        }
      } catch (err) {
        showAria2ErrorBanner('NETWORK_ERROR', 'Torrent Upload Failed', err.message, 'Check media server connection.');
        showAdminToast({ type: 'error', title: 'Upload Failed', message: err.message });
      }
    }

    async function removeAria2Download(gid) {
      const ok = await showAdminConfirm({
        title: 'Remove Download',
        message: 'Are you sure you want to remove this download from Aria2?',
        confirmText: 'Remove Download',
        isDanger: true
      });
      if (!ok) return;
      const el = document.getElementById(`aria2-card-${gid}`);
      if (el) {
        el.style.opacity = '0.3';
        el.style.pointerEvents = 'none';
      }
      try {
        const res = await fetch(`/api/admin/aria2/remove/${gid}`, {
          method: 'DELETE'
        });
        if (res.ok) {
          if (el) el.remove();
          showAdminToast({ type: 'info', title: 'Download Removed', message: `GID ${gid} removed from queue.` });
          await fetchDownloads();
        } else {
          if (el) {
            el.style.opacity = '1';
            el.style.pointerEvents = 'auto';
          }
          const err = await res.json().catch(() => ({}));
          showAdminToast({ type: 'error', title: 'Removal Failed', message: err.detail || 'Could not remove download.' });
        }
      } catch (e) {
        if (el) {
          el.style.opacity = '1';
          el.style.pointerEvents = 'auto';
        }
        showAdminToast({ type: 'error', title: 'Network Error', message: e.message });
      }
    }

    async function clearCompletedAria2() {
      try {
        const res = await fetch(`/api/admin/aria2/clear-completed`, {
          method: 'POST'
        });
        if (res.ok) {
          showAdminToast({ type: 'success', title: 'Queue Cleared', message: 'Completed and stopped downloads purged.' });
          await fetchDownloads();
        } else {
          showAdminToast({ type: 'error', title: 'Clear Failed', message: 'Could not clear completed downloads.' });
        }
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Network Error', message: e.message });
      }
    }

    async function retryAria2Download(gid, name) {
      showAdminToast({
        type: 'info',
        title: 'Retrying Download',
        message: `Attempting restart for "${name || gid}"...`
      });
      try {
        await fetch(`/api/admin/aria2/retry/${gid}`, {
          method: 'POST'
        });
        await fetchDownloads();
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Retry Failed', message: e.message });
      }
    }

    function renderDownloads(list) {
      const chip = document.getElementById('aria2-status-chip');
      if (chip) {
        chip.className = 'px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
        chip.innerText = 'Aria2 RPC Connected';
      }

      const cont = document.getElementById('downloads-list');
      if (!cont) return;
      if (!list || list.length === 0) {
        cont.innerHTML = '<div class="text-neutral-500 py-4 text-center italic">No active or historical downloads in Aria2 queue.</div>';
        return;
      }

      cont.innerHTML = list.map(d => {
        const isErr = d.status === 'error';
        const isComplete = d.status === 'complete' || (d.progress_pct === 100 && !isErr);
        const isPaused = d.status === 'paused';
        const isActive = d.status === 'active';
        const isMeta = Boolean(d.is_metadata);

        let statusBadge = '';
        if (isMeta) {
          statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse">Fetching Metadata</span>';
        } else if (isComplete) {
          statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">Completed</span>';
        } else if (isErr) {
          statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-red-600/20 text-red-400 border border-red-500/40">Failed</span>';
        } else if (isPaused) {
          statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-400 border border-amber-500/40">Paused</span>';
        } else {
          statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 animate-pulse">Downloading</span>';
        }

        const progressPct = d.progress_pct !== undefined ? d.progress_pct : (d.progress_percent !== undefined ? d.progress_percent : 0);
        const speedText = d.download_speed || d.speed_fmt || '0 B/s';
        const etaText = d.eta && d.eta !== 'Unknown' ? `&bull; ETA: ${escapeHtml(d.eta)}` : '';
        const sizeInfo = (d.completed_fmt && d.total_fmt) ? `${escapeHtml(d.completed_fmt)} / ${escapeHtml(d.total_fmt)}` : (d.total_fmt || '');

        let barColor = 'bg-gradient-to-r from-cyan-500 to-blue-500';
        if (isComplete) barColor = 'bg-emerald-500';
        else if (isErr) barColor = 'bg-red-500';
        else if (isPaused) barColor = 'bg-amber-500';

        return `
          <div id="aria2-card-${escapeHtml(d.gid)}" class="bg-neutral-900 border ${isErr ? 'border-red-900/60 bg-red-950/10' : 'border-neutral-800'} p-3.5 rounded-xl space-y-2 transition-all">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0 flex-1">
                <p class="font-bold text-white text-xs truncate" title="${escapeHtml(d.name || d.gid)}">${escapeHtml(d.name || d.gid)}</p>
                <div class="flex items-center gap-2 text-[10px] text-neutral-400 mt-0.5 flex-wrap">
                  <span class="font-mono text-neutral-300">GID: ${escapeHtml(d.gid)}</span>
                  <span>&bull;</span>
                  <span><strong>${progressPct}%</strong></span>
                  ${sizeInfo ? `<span>&bull; ${sizeInfo}</span>` : ''}
                  ${isActive ? `<span>&bull; Speed: <strong class="text-white">${escapeHtml(speedText)}</strong></span>` : ''}
                  ${d.seeders ? `<span>&bull; ${d.seeders} peers</span>` : ''}
                  ${etaText ? `<span>${etaText}</span>` : ''}
                </div>
              </div>
              <div class="flex items-center gap-2 flex-shrink-0">
                ${statusBadge}
                ${isErr ? `
                  <button type="button" data-gid="${escapeHtml(d.gid)}" data-name="${escapeHtml(d.name || '')}" onclick="retryAria2Download(this.dataset.gid, this.dataset.name)" class="px-2 py-1 bg-amber-600 hover:bg-amber-500 text-white rounded-lg text-[10px] font-bold cursor-pointer transition">
                    ↻ Retry
                  </button>
                ` : ''}
                <button type="button" data-gid="${escapeHtml(d.gid)}" onclick="removeAria2Download(this.dataset.gid)" class="px-2 py-1 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 hover:text-white rounded-lg text-[10px] font-semibold cursor-pointer transition">
                  ✕ Remove
                </button>
              </div>
            </div>

            ${isErr && (d.errorMessage || d.error_code) ? `
              <div class="bg-red-950/50 border border-red-500/30 p-2 rounded-lg text-[11px] text-red-300 flex items-center justify-between">
                <span>⚠️ ${escapeHtml(d.errorMessage || 'Download error encountered by Aria2c daemon.')}</span>
                ${d.error_code ? `<code class="text-[9px] font-mono bg-black/40 px-1.5 py-0.5 rounded text-red-400">${escapeHtml(d.error_code)}</code>` : ''}
              </div>
            ` : ''}

            ${isMeta ? `
              <div class="w-full bg-neutral-800 rounded-full h-2 overflow-hidden relative">
                <div class="bg-gradient-to-r from-amber-500 via-cyan-500 to-amber-500 h-2 rounded-full w-full animate-pulse"></div>
              </div>
              <p class="text-[10px] text-amber-300/80 italic font-medium">Fetching Magnet Metadata from BitTorrent DHT network...</p>
            ` : (isActive || isPaused || isComplete ? `
              <div class="w-full bg-neutral-800 rounded-full h-2 overflow-hidden">
                <div class="${barColor} h-2 rounded-full transition-all duration-300" style="width: ${progressPct}%"></div>
              </div>
            ` : '')}
          </div>
        `;
      }).join('');
    }

    async function fetchDownloads() {
      try {
        const list = await fetch('/api/admin/aria2/downloads').then(r => r.ok ? r.json() : null);
        renderDownloads(list);
      } catch (e) {
        const chip = document.getElementById('aria2-status-chip');
        if (chip) {
          chip.className = 'px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider bg-neutral-800 text-neutral-400 border border-neutral-700';
          chip.innerText = 'Aria2 RPC Standby';
        }
      }
    }
"""
