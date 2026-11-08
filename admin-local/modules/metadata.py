import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, File, UploadFile, Form
import httpx
from .common import db, settings, MEDIA_SERVER_URL, ADMIN_SECRET, MediaUpdatePayload, BillboardPayload

router = APIRouter()

@router.get("/api/catalog")
async def get_catalog():
    try:
        items = await db.select("media_items", {"order": "created_at.desc"})
        if not items:
            return []
        now_iso = datetime.now(timezone.utc).isoformat()
        for item in items:
            if item.get("badge_expires_at") and item.get("badge_expires_at") <= now_iso:
                item["badge"] = None
                item["badge_expires_at"] = None
        return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/catalog")
async def create_catalog_item(payload: MediaUpdatePayload):
    try:
        slug = payload.slug or payload.title.lower().replace(" ", "-")
        data = payload.model_dump()
        data["slug"] = slug
        if "trailer_youtube_key" in data:
            val = (data["trailer_youtube_key"] or "").strip()
            data["trailer_youtube_key"] = val if val else None
        if "poster_url" in data:
            val = (data["poster_url"] or "").strip()
            data["poster_url"] = val if val else None
        if "backdrop_url" in data:
            val = (data["backdrop_url"] or "").strip()
            data["backdrop_url"] = val if val else None

        created = await db.insert("media_items", data)
        if not created:
            raise HTTPException(status_code=400, detail="Failed to insert database record.")
        return created
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/catalog/{media_id}")
async def update_catalog_item(media_id: str, payload: MediaUpdatePayload):
    try:
        data = payload.model_dump(exclude_unset=True)
        # Crucial: If slug is None or empty, do not overwrite existing slug with null!
        if not data.get("slug"):
            data.pop("slug", None)
            
        # Normalize fields: empty strings should be None so PostgreSQL sets NULL
        if "trailer_youtube_key" in data:
            val = (data["trailer_youtube_key"] or "").strip()
            data["trailer_youtube_key"] = val if val else None
        if "poster_url" in data:
            val = (data["poster_url"] or "").strip()
            data["poster_url"] = val if val else None
        if "backdrop_url" in data:
            val = (data["backdrop_url"] or "").strip()
            data["backdrop_url"] = val if val else None
        if "audio_languages" in data:
            if not data["audio_languages"]:
                data["audio_languages"] = ["English"]

        # Badge & Expiration handling
        badge_val = (data.get("badge") or "").strip()
        badge_duration = data.pop("badge_duration_days", None)
        if not badge_val or badge_val.lower() in ("none", ""):
            data["badge"] = None
            data["badge_expires_at"] = None
            data["badge_type"] = "none"
        else:
            data["badge"] = badge_val
            if badge_duration is not None:
                if badge_duration > 0:
                    data["badge_expires_at"] = (datetime.now(timezone.utc) + timedelta(days=badge_duration)).isoformat()
                else:
                    data["badge_expires_at"] = None
            elif not data.get("badge_expires_at"):
                # Default to 10 days if not set
                data["badge_expires_at"] = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()

            legacy_type = "recently_added" if "recent" in badge_val.lower() else ("new_season" if "season" in badge_val.lower() else "none")
            data["badge_type"] = legacy_type

        updated = await db.update("media_items", "id", media_id, data)
        if not updated:
            raise HTTPException(status_code=400, detail="Failed to update database record.")
        try:
            from backend.app.api.metadata import bump_catalog_version, invalidate_catalog_cache
            invalidate_catalog_cache()
            bump_catalog_version()
        except Exception:
            pass
        return updated
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/catalog/{media_id}/billboard")
async def update_catalog_billboard(media_id: str, payload: BillboardPayload):
    """Proxy route to Media Server to set/toggle billboard status"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/{media_id}/billboard"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.put(url, headers={"X-Admin-Secret": ADMIN_SECRET}, json=payload.model_dump())
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.delete("/api/catalog/{media_id}")
async def delete_catalog_item(media_id: str):
    """Proxy route to Media Server for permanent movie/series deletion from disk and DB"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/media/{media_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.delete(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.delete("/api/admin/episodes/{episode_id}")
async def delete_episode_proxy(episode_id: str):
    """Proxy route to Media Server for single episode deletion from disk and DB"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/episodes/{episode_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.delete(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.get("/api/admin/series/{media_id}/episodes")
async def get_series_episodes_proxy(media_id: str):
    """Proxy route to Media Server for series episodes with X-Admin-Secret"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/series/{media_id}/episodes"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.put("/api/admin/episodes/{episode_id}")
async def update_episode_proxy(episode_id: str, payload: Dict[str, Any]):
    """Proxy route to Media Server for episode update with X-Admin-Secret"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/episodes/{episode_id}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.put(url, headers={"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"}, json=payload)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.get("/api/admin/streams/{target_id}/tracks")
async def get_stream_tracks_proxy(target_id: str):
    """Proxy route to Media Server for stream tracks with X-Admin-Secret"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/streams/{target_id}/tracks"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code == 404:
                return {"audio_tracks": [], "subtitle_tracks": [], "target_id": target_id}
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.put("/api/admin/streams/{target_id}/tracks")
async def update_stream_tracks_proxy(target_id: str, payload: Dict[str, Any]):
    """Proxy route to Media Server for stream track update with X-Admin-Secret"""
    url = f"{MEDIA_SERVER_URL}/api/metadata/streams/{target_id}/tracks"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.put(url, headers={"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"}, json=payload)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.post("/api/admin/streams/{target_id}/subtitles/upload")
async def upload_stream_subtitle_proxy(
    target_id: str,
    file: UploadFile = File(...),
    lang: Optional[str] = Form(None),
    label: Optional[str] = Form(None)
):
    url = f"{MEDIA_SERVER_URL}/api/metadata/streams/{target_id}/subtitles/upload"
    MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
    file_bytes = await file.read(MAX_SUBTITLE_BYTES + 1)
    if len(file_bytes) > MAX_SUBTITLE_BYTES:
        raise HTTPException(status_code=413, detail="Subtitle file exceeds maximum limit of 5 MB")
    files = {"file": (file.filename, file_bytes, file.content_type or "text/plain")}
    data = {}
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


@router.delete("/api/admin/streams/{target_id}/subtitles/{sub_id}")
async def delete_stream_subtitle_proxy(target_id: str, sub_id: str):
    url = f"{MEDIA_SERVER_URL}/api/metadata/streams/{target_id}/subtitles/{sub_id}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.delete(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")




@router.put("/api/admin/media/{media_id}/visibility")
async def toggle_media_visibility_proxy(media_id: str, payload: Dict[str, Any]):
    url = f"{MEDIA_SERVER_URL}/api/metadata/media/{media_id}/visibility"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.put(url, headers={"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"}, json=payload)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")

@router.put("/api/admin/episodes/{episode_id}/visibility")
async def toggle_episode_visibility_proxy(episode_id: str, payload: Dict[str, Any]):
    url = f"{MEDIA_SERVER_URL}/api/metadata/episodes/{episode_id}/visibility"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.put(url, headers={"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"}, json=payload)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


METADATA_HTML = r"""      <!-- TAB 4: PUBLISHED CATALOG & METADATA MANAGER -->
      <section id="tab-metadata" class="space-y-6 hidden">
        <div class="pt-2 space-y-4">
          <!-- Header and Billboard Selector -->
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-1">
            <div class="flex items-center gap-2">
              <span class="w-2 h-2 rounded-full bg-red-500"></span>
              <h3 class="text-xs font-bold text-white uppercase tracking-wider">Published Titles &amp; Metadata Manager</h3>
              <span id="catalog-count-badge" class="text-[11px] font-mono text-neutral-400 bg-neutral-800/80 px-2 py-0.5 rounded-md border border-neutral-700/60 font-semibold">0 Titles</span>
            </div>
            <!-- 🎬 Homepage Billboard Selector -->
            <div class="flex items-center gap-2 bg-[#141414] border border-amber-500/30 px-3 py-1.5 rounded-xl shadow-sm">
              <span class="text-amber-400 text-xs">🎬</span>
              <label for="billboard-select" class="text-[11px] font-bold text-amber-300 uppercase tracking-wider whitespace-nowrap">Featured Billboard:</label>
              <select id="billboard-select" onchange="onBillboardSelectorChange(this.value)" class="bg-[#181818] border border-amber-500/40 rounded-lg px-2.5 py-1 text-xs text-white focus:outline-none focus:border-amber-400 font-medium cursor-pointer">
                <option value="none">-- None (No Title Featured) --</option>
              </select>
            </div>
          </div>

          <!-- Search & Filter Controls -->
          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-3.5 sm:p-4 flex flex-col md:flex-row gap-2.5 items-stretch md:items-center justify-between shadow-lg">
            <div class="relative flex-1">
              <input type="text" id="media-search" oninput="metadataCurrentPage = 1; renderMediaTable()" placeholder="Search movie or series title..." class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2 text-xs text-white focus:outline-none focus:border-red-500">
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <select id="media-status-filter" onchange="metadataCurrentPage = 1; renderMediaTable()" class="bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none">
                <option value="all">All Statuses</option>
                <option value="visible">Published (Visible)</option>
                <option value="hidden">Hidden from App</option>
              </select>
              <select id="media-type-filter" onchange="metadataCurrentPage = 1; renderMediaTable()" class="bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none">
                <option value="all">All Types</option>
                <option value="movie">Movies</option>
                <option value="series">Series</option>
              </select>
              <select id="media-genre-filter" onchange="metadataCurrentPage = 1; renderMediaTable()" class="bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none">
                <option value="all">All Genres</option>
                <option value="Action">Action</option>
                <option value="Sci-Fi">Sci-Fi</option>
                <option value="Crime">Crime</option>
                <option value="Drama">Drama</option>
                <option value="Animation">Animation</option>
                <option value="Comedy">Comedy</option>
              </select>
              <select id="metadata-page-size" onchange="onMetadataPageSizeChange(this.value)" class="bg-[#181818] border border-neutral-700 text-neutral-300 text-xs rounded-xl px-2.5 py-2 focus:outline-none focus:border-red-500 font-mono cursor-pointer">
                <option value="10" selected>10 / page</option>
                <option value="20">20 / page</option>
                <option value="50">50 / page</option>
                <option value="100">100 / page</option>
              </select>
            </div>
          </div>

          <!-- Published Catalog Cards -->
          <div class="bg-[#141414] border border-neutral-800 rounded-2xl overflow-hidden shadow-xl">
            <div id="media-tbody" class="divide-y divide-neutral-800/60">
              <!-- Dynamic Media Items -->
            </div>
          </div>

          <!-- Metadata Pagination Footer (PART 4) -->
          <div id="metadata-pagination-footer" class="bg-[#141414] border border-neutral-800 rounded-2xl px-4 py-3 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-neutral-400">
            <div id="metadata-pagination-info" class="font-mono text-[11px]">Showing 0 to 0 of 0 titles</div>
            <div class="flex items-center gap-1.5 font-mono text-xs">
              <button type="button" id="btn-meta-first" onclick="changeMetadataPage('first')" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800 cursor-pointer">&laquo;</button>
              <button type="button" id="btn-meta-prev" onclick="changeMetadataPage(-1)" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800 cursor-pointer">&lsaquo; Prev</button>
              <span id="metadata-page-display" class="px-3 py-1.5 rounded-lg bg-black border border-neutral-800 text-white font-bold text-[11px]">Page 1 of 1</span>
              <button type="button" id="btn-meta-next" onclick="changeMetadataPage(1)" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800 cursor-pointer">Next &rsaquo;</button>
              <button type="button" id="btn-meta-last" onclick="changeMetadataPage('last')" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800 cursor-pointer">&raquo;</button>
            </div>
          </div>
        </div>
      </section>
"""

METADATA_MODALS_HTML = r"""  <!-- MEDIA MODAL (Edit Metadata & Manual Trailer Link - Titles only created via ingestion) -->
  <div id="media-modal" class="fixed inset-0 z-50 bg-black/85 hidden items-center justify-center p-4 overflow-y-auto overflow-x-hidden">
    <div class="bg-[#141414] border border-neutral-800 rounded-3xl w-full max-w-2xl p-6 sm:p-8 space-y-5 shadow-2xl my-auto max-h-[90vh] overflow-y-auto overflow-x-hidden">
      <div class="flex items-center justify-between pb-3 border-b border-neutral-800">
        <h3 id="modal-title" class="text-lg font-bold text-white">Edit Title Metadata</h3>
        <button onclick="closeMediaModal()" class="text-neutral-400 hover:text-white cursor-pointer">&times;</button>
      </div>

      <!-- Lock Notice -->
      <div class="p-3 bg-neutral-900 border border-neutral-800 rounded-xl text-xs text-neutral-400 flex items-center gap-2">
        <span class="text-amber-400 font-bold">🔒 Source Locked:</span>
        <span>Title name, type, and release year are locked to match the ingested video stream. Customize trailer, synopsis, and posters below.</span>
      </div>

      <form onsubmit="saveMedia(event)" class="space-y-4">
        <input type="hidden" id="edit-id">
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div class="space-y-1">
            <label class="text-xs text-neutral-400 font-medium">Title (Locked)</label>
            <input type="text" id="edit-title" disabled class="w-full bg-[#181818]/60 border border-neutral-800 rounded-xl px-3.5 py-2 text-xs text-neutral-400 cursor-not-allowed">
          </div>
          <div class="space-y-1">
            <label class="text-xs text-neutral-400 font-medium">Type (Locked)</label>
            <select id="edit-type" disabled class="w-full bg-[#181818]/60 border border-neutral-800 rounded-xl px-3.5 py-2 text-xs text-neutral-400 cursor-not-allowed">
              <option value="movie">Movie</option>
              <option value="series">Series</option>
            </select>
          </div>
        </div>

        <div class="space-y-1">
          <label class="text-xs text-neutral-400 font-medium">Storyline / Synopsis</label>
          <textarea id="edit-synopsis" rows="3" required class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2 text-xs text-white leading-relaxed"></textarea>
        </div>

        <div class="space-y-1 bg-[#181818] p-3.5 rounded-xl border border-neutral-800">
          <label class="text-xs text-white font-bold">Trailer Link or YouTube Video Key</label>
          <input type="text" id="edit-trailer" placeholder="e.g. EXeTwQWrcwY or https://www.youtube.com/watch?v=..." class="w-full bg-black border border-neutral-700 rounded-lg px-3 py-2 text-xs text-white font-mono mt-1">
          <p class="text-[11px] text-neutral-500 mt-1">Clearing this field removes the trailer and falls back to the backdrop image.</p>
        </div>

        <div class="grid grid-cols-3 gap-3">
          <div>
            <label class="text-xs text-neutral-400">Year (Locked)</label>
            <input type="number" id="edit-year" disabled class="w-full bg-[#181818]/60 border border-neutral-800 rounded-xl px-3 py-2 text-xs text-neutral-400 cursor-not-allowed">
          </div>
          <div>
            <label class="text-xs text-neutral-400">Rating</label>
            <input type="text" id="edit-rating" class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white">
          </div>
          <div>
            <label class="text-xs text-neutral-400">Runtime (min)</label>
            <input type="number" id="edit-runtime" class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white">
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <!-- Genres Dropdown with Checkbox Multi-Select -->
          <div class="space-y-1 relative" id="genres-dropdown-container">
            <label class="text-xs text-neutral-400 font-medium">Genres</label>
            <div 
              id="genres-dropdown-trigger" 
              onclick="toggleDropdown('genres', event)"
              class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2 text-xs text-white flex items-center justify-between cursor-pointer hover:border-neutral-500 transition select-none"
            >
              <span id="genres-display-text" class="truncate text-neutral-200">Select genres...</span>
              <svg class="w-3.5 h-3.5 text-neutral-400 ml-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
            </div>

            <!-- Dropdown Menu -->
            <div 
              id="genres-dropdown-menu" 
              class="hidden absolute z-50 left-0 right-0 mt-1 bg-[#1a1a1a] border border-neutral-700 rounded-xl shadow-2xl p-2.5 max-h-56 overflow-y-auto space-y-1 backdrop-blur-lg"
            >
              <!-- Checkbox items rendered via JS -->
            </div>
          </div>

          <!-- Audio Languages Dropdown with Checkbox Multi-Select -->
          <div class="space-y-1 relative" id="audio-dropdown-container">
            <label class="text-xs text-neutral-400 font-medium">Audio Languages</label>
            <div 
              id="audio-dropdown-trigger" 
              onclick="toggleDropdown('audio', event)"
              class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2 text-xs text-white flex items-center justify-between cursor-pointer hover:border-neutral-500 transition select-none"
            >
              <span id="audio-display-text" class="truncate text-neutral-200">Select audio languages...</span>
              <svg class="w-3.5 h-3.5 text-neutral-400 ml-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
            </div>

            <!-- Dropdown Menu -->
            <div 
              id="audio-dropdown-menu" 
              class="hidden absolute z-50 left-0 right-0 mt-1 bg-[#1a1a1a] border border-neutral-700 rounded-xl shadow-2xl p-2.5 max-h-56 overflow-y-auto space-y-1 backdrop-blur-lg"
            >
              <!-- Checkbox items rendered via JS -->
            </div>
          </div>

          <!-- Subtitle Languages Dropdown with Checkbox Multi-Select -->
          <div class="space-y-1 relative" id="sub-dropdown-container">
            <label class="text-xs text-neutral-400 font-medium">Subtitle Languages</label>
            <div 
              id="sub-dropdown-trigger" 
              onclick="toggleDropdown('sub', event)"
              class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2 text-xs text-white flex items-center justify-between cursor-pointer hover:border-neutral-500 transition select-none"
            >
              <span id="sub-display-text" class="truncate text-neutral-200">Select subtitle languages...</span>
              <svg class="w-3.5 h-3.5 text-neutral-400 ml-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
            </div>

            <!-- Dropdown Menu -->
            <div 
              id="sub-dropdown-menu" 
              class="hidden absolute z-50 left-0 right-0 mt-1 bg-[#1a1a1a] border border-neutral-700 rounded-xl shadow-2xl p-2.5 max-h-56 overflow-y-auto space-y-1 backdrop-blur-lg"
            >
              <!-- Checkbox items rendered via JS -->
            </div>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-xs text-neutral-400">Poster URL</label>
            <input type="url" id="edit-poster" class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white">
          </div>
          <div>
            <label class="text-xs text-neutral-400">Backdrop URL</label>
            <input type="url" id="edit-backdrop" class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white">
          </div>
        </div>

        <!-- Promotional Poster Badge & Expiration Section -->
        <div class="bg-[#181818] p-3.5 rounded-xl border border-neutral-800 space-y-3">
          <div class="flex items-center justify-between">
            <label class="text-xs text-white font-bold flex items-center gap-1.5">
              <span>🏷️</span> <span>Promotional Poster Badge &amp; Expiration Timer</span>
            </label>
            <span id="badge-status-pill" class="text-[10px] font-mono px-2 py-0.5 rounded bg-neutral-800 text-neutral-400 border border-neutral-700">No Badge</span>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div class="space-y-1">
              <label class="text-[11px] text-neutral-400 font-medium">Badge Tag</label>
              <select id="edit-badge-preset" onchange="onBadgePresetChange(this.value)" class="w-full bg-black border border-neutral-700 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-red-500">
                <option value="none">None (No Badge)</option>
                <option value="Recently Added">Recently Added</option>
                <option value="New Season">New Season</option>
                <option value="New Episode">New Episode</option>
                <option value="Most Liked">Most Liked</option>
                <option value="Top 10">Top 10</option>
                <option value="Trending Now">Trending Now</option>
                <option value="Leaving Soon">Leaving Soon</option>
                <option value="custom">Custom Tag...</option>
              </select>
              <input type="text" id="edit-badge-custom" placeholder="Enter custom badge text" class="w-full bg-black border border-neutral-700 rounded-lg px-3 py-1.5 text-xs text-white mt-1 hidden" maxlength="32">
            </div>

            <div class="space-y-1">
              <label class="text-[11px] text-neutral-400 font-medium">Expiration Timer</label>
              <select id="edit-badge-timer" onchange="onBadgeTimerChange(this.value)" class="w-full bg-black border border-neutral-700 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-red-500">
                <option value="10">10 Days (Default)</option>
                <option value="7">7 Days</option>
                <option value="3">3 Days</option>
                <option value="1">1 Day</option>
                <option value="14">14 Days</option>
                <option value="30">30 Days</option>
                <option value="custom">Custom Days...</option>
                <option value="0">Never Expire (Permanent)</option>
              </select>
              <input type="number" id="edit-badge-custom-days" min="1" max="365" placeholder="Number of days (e.g. 5)" class="w-full bg-black border border-neutral-700 rounded-lg px-3 py-1.5 text-xs text-white mt-1 hidden">
            </div>
          </div>
        </div>

        <!-- Movie Stream Audio & Subtitle Track Display Names Section -->
        <div id="movie-tracks-section" class="hidden space-y-3 pt-3 border-t border-neutral-800">
          <div class="flex items-center justify-between">
            <label class="text-xs text-white font-bold flex items-center gap-1.5">
              <span>🔊</span> <span>Audio &amp; Subtitle Track Display Names (Player UI)</span>
            </label>
            <span class="text-[10px] text-neutral-400 font-mono">Instant Update (No Re-encode)</span>
          </div>
          <div id="movie-tracks-loading" class="text-xs text-neutral-500 py-2 hidden flex items-center gap-2">
            <div class="w-3.5 h-3.5 border-2 border-red-500 border-t-transparent rounded-full animate-spin"></div>
            <span>Loading stream track info...</span>
          </div>
          <div id="movie-audio-tracks-list" class="space-y-2"></div>
          <div id="movie-sub-tracks-list" class="space-y-2"></div>
          <div class="flex justify-end pt-1">
            <button type="button" onclick="saveMovieTracks()" id="btn-save-movie-tracks" class="px-3.5 py-1.5 bg-neutral-800 hover:bg-neutral-700 text-white rounded-xl text-xs font-semibold cursor-pointer border border-neutral-700 transition flex items-center gap-1.5">
              <span>💾 Save Track Names</span>
            </button>
          </div>
        </div>

        <!-- Series Episodes Jump Banner (Only for series) -->
        <div id="series-banner-indicator" class="hidden p-3 bg-red-950/40 border border-red-500/30 rounded-xl flex items-center justify-between text-xs text-red-200">
          <div class="flex items-center gap-2">
            <span class="text-base">📺</span>
            <span><strong>Series Ingested Tracks:</strong> Episode video, audio &amp; subtitle tracks are configured in the <strong>Edit Uploaded Episode Metadata</strong> section below.</span>
          </div>
          <button type="button" onclick="document.getElementById('series-episodes-section')?.scrollIntoView({ behavior: 'smooth' })" class="px-3 py-1 bg-red-600 hover:bg-red-700 text-white rounded-lg text-[11px] font-bold cursor-pointer whitespace-nowrap ml-3">Jump to Episodes ↓</button>
        </div>

        <!-- Public Catalog Visibility Toggle -->
        <div class="p-3.5 bg-neutral-900 border border-neutral-700/80 rounded-2xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-md">
          <div class="flex items-center gap-2.5">
            <span class="text-lg">👁️</span>
            <div>
              <h5 class="text-xs font-bold text-white">Public Catalog Visibility</h5>
              <p class="text-[11px] text-neutral-400">Make this title visible to all streaming clients or hide it instantly</p>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <label class="flex items-center gap-2 cursor-pointer select-none">
              <input type="checkbox" id="edit-visibility-toggle" class="w-4 h-4 accent-emerald-500 rounded cursor-pointer">
              <span class="text-xs font-bold text-neutral-200">Visible in Public Catalog</span>
            </label>
          </div>
        </div>

        <!-- Billboard Feature Toggle -->
        <div class="p-3.5 bg-amber-950/20 border border-amber-500/30 rounded-2xl flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div class="flex items-center gap-2.5">
            <span class="text-lg">🎬</span>
            <div>
              <h5 class="text-xs font-bold text-amber-300">Homepage Billboard (Hero Banner)</h5>
              <p class="text-[11px] text-neutral-400">Feature this title as the prominent hero billboard on the client homepage</p>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <label class="flex items-center gap-2 cursor-pointer select-none">
              <input type="checkbox" id="edit-billboard-toggle" class="w-4 h-4 accent-amber-500 rounded cursor-pointer">
              <span class="text-xs font-bold text-amber-200">Active on Billboard</span>
            </label>
          </div>
        </div>

        <div class="flex justify-end gap-3 pt-4 border-t border-neutral-800">
          <button type="button" onclick="closeMediaModal()" class="px-4 py-2 bg-neutral-800 text-neutral-300 rounded-xl text-xs font-semibold cursor-pointer">Cancel</button>
          <button type="submit" class="px-6 py-2 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold cursor-pointer">Save Changes</button>
        </div>
      </form>

      <!-- Conditional Episode Metadata Section (Rendered below series form only when type === 'series') -->
      <div id="series-episodes-section" class="hidden border-t border-neutral-800 pt-5 space-y-4">
        <div class="flex items-center justify-between pb-1">
          <div class="flex items-center gap-2">
            <span class="text-base">📺</span>
            <h4 class="text-sm font-bold text-white tracking-wide">Edit Uploaded Episode Metadata</h4>
          </div>
          <span id="episodes-count-badge" class="px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-neutral-800 text-neutral-400 border border-neutral-700">0 Episodes</span>
        </div>

        <div id="episodes-loading" class="text-xs text-neutral-400 py-3 flex items-center gap-2">
          <div class="w-3.5 h-3.5 border-2 border-red-500 border-t-transparent rounded-full animate-spin"></div>
          <span>Loading uploaded episodes...</span>
        </div>

        <div id="episodes-empty" class="hidden p-3.5 bg-neutral-900/60 border border-neutral-800 rounded-xl text-xs text-neutral-500">
          No uploaded episodes found in catalog for this series. Ingest video files to populate episodes.
        </div>

        <div id="episodes-editor" class="hidden space-y-4">
          <!-- Dropdowns Row -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 bg-[#181818] p-3.5 rounded-2xl border border-neutral-800">
            <div class="space-y-1">
              <label class="text-[11px] font-bold uppercase tracking-wider text-neutral-400">1. Select Season</label>
              <select id="ep-season-select" onchange="onSeasonChange()" class="w-full bg-black border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-red-500 cursor-pointer">
              </select>
            </div>
            <div class="space-y-1">
              <label class="text-[11px] font-bold uppercase tracking-wider text-neutral-400">2. Select Episode</label>
              <select id="ep-select" onchange="onEpisodeChange()" class="w-full bg-black border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-red-500 cursor-pointer">
              </select>
            </div>
          </div>

          <!-- Single-Episode Form Inputs -->
          <div class="bg-neutral-900/80 border border-neutral-800 rounded-2xl p-4 space-y-3.5">
            <input type="hidden" id="ep-edit-id">

            <div class="space-y-1">
              <label class="text-xs text-neutral-400 font-medium">Episode Title</label>
              <input type="text" id="ep-edit-title" required class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2 text-xs text-white focus:outline-none focus:border-red-500">
            </div>

            <div class="grid grid-cols-3 gap-3">
              <div class="space-y-1">
                <label class="text-xs text-neutral-400 font-medium">Episode #</label>
                <input type="number" id="ep-edit-number" min="1" max="2000" class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white">
              </div>
              <div class="space-y-1">
                <label class="text-xs text-neutral-400 font-medium">Duration (sec)</label>
                <input type="number" id="ep-edit-duration" min="0" class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white">
              </div>
              <div class="space-y-1 flex flex-col justify-end pb-1">
                <label class="flex items-center gap-2 cursor-pointer select-none">
                  <input type="checkbox" id="ep-edit-published" class="w-4 h-4 accent-red-600 rounded cursor-pointer">
                  <span class="text-xs text-neutral-300 font-medium">Published in Catalog</span>
                </label>
              </div>
            </div>

            <!-- Thumbnail URL + Preview -->
            <div class="space-y-1.5">
              <label class="text-xs text-neutral-400 font-medium">Episode Still / Thumbnail URL</label>
              <div class="flex items-start gap-3">
                <div class="flex-1">
                  <input type="url" id="ep-edit-still" oninput="updateEpisodeStillPreview()" placeholder="https://... or /media/..." class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2 text-xs text-white focus:outline-none focus:border-red-500">
                </div>
                <div class="w-24 h-14 bg-black border border-neutral-800 rounded-lg overflow-hidden flex-shrink-0 flex items-center justify-center">
                  <img id="ep-edit-still-preview" src="" alt="" class="w-full h-full object-cover hidden">
                  <span id="ep-edit-still-placeholder" class="text-[10px] text-neutral-600 text-center px-1">16:9 Preview</span>
                </div>
              </div>
            </div>

            <!-- Synopsis -->
            <div class="space-y-1">
              <label class="text-xs text-neutral-400 font-medium">Episode Synopsis</label>
              <textarea id="ep-edit-synopsis" rows="2" class="w-full bg-[#181818] border border-neutral-700 rounded-xl px-3.5 py-2 text-xs text-white focus:outline-none focus:border-red-500 leading-relaxed"></textarea>
            </div>

            <!-- Ingested Episode Audio & Subtitle Track Display Names -->
            <div id="ep-tracks-section" class="space-y-3 pt-3 border-t border-neutral-800">
              <div class="flex items-center justify-between">
                <label class="text-xs text-white font-bold flex items-center gap-1.5">
                  <span>🔊</span> <span>Audio &amp; Subtitle Track Display Names (Player UI)</span>
                </label>
                <span class="text-[10px] text-neutral-400 font-mono">Instant Update (No Re-encode)</span>
              </div>
              <div id="ep-audio-tracks-list" class="space-y-2"></div>
              <div id="ep-sub-tracks-list" class="space-y-2"></div>
            </div>

            <!-- Save / Delete Episode Buttons -->
            <div class="flex items-center justify-between pt-1">
              <button type="button" id="delete-episode-btn" onclick="deleteSelectedEpisodeFromModal()" class="px-4 py-2 bg-red-600/20 hover:bg-red-600 text-red-400 hover:text-white border border-red-500/40 hover:border-red-600 rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer">
                <span>🗑️ Delete Selected Episode</span>
              </button>
              <button type="button" id="save-episode-btn" onclick="saveSelectedEpisode()" class="px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow-lg shadow-emerald-600/20">
                <span id="save-episode-text">💾 Save Selected Episode</span>
                <span id="save-episode-code" class="text-emerald-200"></span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- PERMANENT DELETION CONFIRMATION MODAL -->
  <div id="delete-modal" class="fixed inset-0 z-50 bg-black/85 hidden items-center justify-center p-4 overflow-y-auto">
    <div class="bg-[#141414] border border-red-500/30 rounded-3xl w-full max-w-lg p-6 sm:p-7 space-y-5 shadow-2xl my-auto">
      <div class="flex items-center justify-between pb-3 border-b border-neutral-800">
        <h3 id="delete-modal-title" class="text-base font-bold text-white flex items-center gap-2">
          <span class="text-red-500">🗑️</span> <span id="delete-modal-heading">Permanently Delete</span>
        </h3>
        <button onclick="closeDeleteModal()" class="text-neutral-400 hover:text-white cursor-pointer">&times;</button>
      </div>

      <!-- Content for Movie -->
      <div id="delete-movie-view" class="space-y-4 hidden">
        <div class="p-3.5 bg-red-950/20 border border-red-500/30 rounded-xl space-y-1">
          <p class="text-xs text-red-200 font-semibold">
            Permanently delete <span class="font-bold text-white" id="delete-movie-title">Title</span>?
          </p>
          <p class="text-[11px] text-neutral-400">
            This will remove the title from the catalog, database, user watch lists, and permanently delete all HLS video files from <code class="font-mono text-neutral-300">/media/library/</code>.
          </p>
        </div>
        <div class="flex items-center justify-end gap-2.5 pt-2">
          <button type="button" id="btn-cancel-delete-movie" onclick="closeDeleteModal()" class="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 rounded-xl text-xs font-semibold cursor-pointer">Cancel</button>
          <button type="button" id="btn-confirm-delete-movie" onclick="confirmDeleteMovie()" class="px-5 py-2 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow-lg shadow-red-600/30">
            <span>🗑️ Permanently Delete Movie</span>
          </button>
        </div>
      </div>

      <!-- Content for Series (2 Choices: Specific Episode vs Entire Series) -->
      <div id="delete-series-view" class="space-y-5 hidden">
        <div class="p-3 bg-neutral-900 border border-neutral-800 rounded-xl text-xs text-neutral-300">
          Managing deletion for series: <strong class="text-white" id="delete-series-title">Series Name</strong>
        </div>

        <!-- Choice 1: Delete Specific Episode -->
        <div class="bg-[#181818] border border-neutral-800 rounded-2xl p-4 space-y-3">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-bold text-white uppercase tracking-wider flex items-center gap-1.5">
              <span>Option 1:</span> <span>Delete Specific Episode</span>
            </h4>
            <span class="text-[10px] text-neutral-500 font-mono">Reclaims episode HLS</span>
          </div>
          <div class="space-y-1.5">
            <label class="text-[11px] text-neutral-400">Select Episode to Delete:</label>
            <select id="delete-series-episode-select" class="w-full bg-black border border-neutral-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-red-500 cursor-pointer">
              <option value="">Loading episodes...</option>
            </select>
          </div>
          <div class="flex justify-end pt-1">
            <button type="button" id="btn-confirm-delete-series-ep" onclick="confirmDeleteEpisodeFromSeriesModal()" class="px-4 py-2 bg-red-600/20 hover:bg-red-600 text-red-400 hover:text-white border border-red-500/40 hover:border-red-600 rounded-xl text-xs font-bold transition cursor-pointer">
              <span>🗑️ Delete Selected Episode &amp; HLS Files</span>
            </button>
          </div>
        </div>

        <!-- Choice 2: Delete Entire Series & All Episodes -->
        <div class="bg-red-950/20 border border-red-500/30 rounded-2xl p-4 space-y-3">
          <div class="flex items-center justify-between">
            <h4 class="text-xs font-bold text-red-400 uppercase tracking-wider flex items-center gap-1.5">
              <span>Option 2:</span> <span>Delete Entire Series &amp; All Episodes</span>
            </h4>
            <span class="text-[10px] text-red-400/80 font-bold">Wipes All</span>
          </div>
          <p class="text-[11px] text-neutral-400">
            Permanently removes the entire series and wipes all seasons and episode HLS directories from disk and database.
          </p>
          <div class="flex justify-end pt-1">
            <button type="button" id="btn-confirm-delete-series" onclick="confirmDeleteEntireSeries()" class="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold transition cursor-pointer shadow-lg shadow-red-600/30">
              <span>🗑️ Delete Entire Series &amp; All HLS Files</span>
            </button>
          </div>
        </div>

        <div class="flex justify-end pt-2 border-t border-neutral-800">
          <button type="button" id="btn-close-delete-modal-series" onclick="closeDeleteModal()" class="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 rounded-xl text-xs font-semibold cursor-pointer">Close</button>
        </div>
      </div>
    </div>
  </div>

"""

METADATA_JS = r"""    async function loadCatalog() {
      try {
        catalog = await fetch('/api/catalog').then(r => r.json());
        renderMediaTable();
        populateBillboardSelector();
      } catch (e) {}
    }

    function populateBillboardSelector() {
      const sel = document.getElementById('billboard-select') || document.getElementById('billboard-selector');
      if (!sel) return;
      const pinned = catalog.find(m => Boolean(m.is_featured_billboard));

      let html = '<option value="auto">Auto / None (Latest Trending Title)</option>';
      catalog.forEach(m => {
        const isSel = Boolean(m.is_featured_billboard) ? 'selected' : '';
        html += `<option value="${escapeHtml(m.id)}" ${isSel}>★ ${escapeHtml(m.title)} (${escapeHtml((m.type || 'movie').toUpperCase())})</option>`;
      });
      sel.innerHTML = html;
      sel.value = pinned ? pinned.id : 'auto';
    }

    async function onBillboardSelectorChange(val) {
      try {
        if (val === 'auto' || val === 'none') {
          await fetch('/api/catalog/auto/billboard', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_featured_billboard: false, exclusive: true })
          });
          showAdminToast({ type: 'success', title: 'Billboard Reset', message: 'Homepage Billboard set to Auto / None (Latest Trending Title)' });
        } else {
          await fetch(`/api/catalog/${val}/billboard`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_featured_billboard: true, exclusive: true, billboard_priority: 100 })
          });
          const item = catalog.find(m => m.id === val);
          showAdminToast({ type: 'success', title: 'Billboard Pinned', message: `Pinned "${item ? item.title : 'Title'}" as the Active Homepage Billboard!` });
        }
        await loadCatalog();
        populateBillboardSelector();
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Billboard Update Failed', message: e.message });
      }
    }

    async function toggleBillboard(mediaId) {
      try {
        const item = catalog.find(m => m.id === mediaId);
        if (!item) return;
        const willBeActive = !item.is_featured_billboard;
        await fetch(`/api/catalog/${mediaId}/billboard`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_featured_billboard: willBeActive, exclusive: true, billboard_priority: willBeActive ? 100 : 0 })
        });
        showAdminToast({
          type: 'success',
          title: willBeActive ? 'Billboard Activated' : 'Billboard Deactivated',
          message: willBeActive ? `"${item.title}" is now the Active Homepage Billboard!` : `"${item.title}" removed from Billboard.`
        });
        await loadCatalog();
        populateBillboardSelector();
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Toggle Billboard Failed', message: e.message });
      }
    }

    async function togglePublished(mediaId) {
      try {
        const item = catalog.find(m => m.id === mediaId);
        if (!item) return;
        const willBePub = !item.is_published;
        await fetch(`/api/admin/media/${mediaId}/visibility`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_published: willBePub })
        });
        showAdminToast({
          type: 'success',
          title: willBePub ? 'Title Published' : 'Title Hidden',
          message: `"${item.title}" is now ${willBePub ? 'visible on public catalog' : 'hidden from public view'}.`
        });
        await loadCatalog();
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Visibility Update Failed', message: e.message });
      }
    }

    let metadataCurrentPage = 1;
    let metadataPageSize = 10;

    function onMetadataPageSizeChange(val) {
      metadataPageSize = parseInt(val, 10) || 10;
      metadataCurrentPage = 1;
      renderMediaTable();
    }

    function changeMetadataPage(target) {
      const q = (document.getElementById('media-search')?.value || '').toLowerCase();
      const type = document.getElementById('media-type-filter')?.value || 'all';
      const genre = document.getElementById('media-genre-filter')?.value || 'all';
      const statusFilter = document.getElementById('media-status-filter')?.value || 'all';

      const filtered = catalog.filter(m => {
        const matchQ = !q || m.title.toLowerCase().includes(q) || (m.synopsis && m.synopsis.toLowerCase().includes(q));
        const matchT = type === 'all' || m.type === type;
        const matchG = genre === 'all' || (m.genres && m.genres.some(g => g.toLowerCase().includes(genre.toLowerCase())));
        const matchS = statusFilter === 'all' || (statusFilter === 'visible' && m.is_published) || (statusFilter === 'hidden' && !m.is_published);
        return matchQ && matchT && matchG && matchS;
      });

      const total = filtered.length;
      const totalPages = Math.max(1, Math.ceil(total / metadataPageSize));

      if (target === 'first') {
        metadataCurrentPage = 1;
      } else if (target === 'last') {
        metadataCurrentPage = totalPages;
      } else if (typeof target === 'number') {
        metadataCurrentPage += target;
      }
      metadataCurrentPage = Math.max(1, Math.min(metadataCurrentPage, totalPages));
      renderMediaTable();
    }

    function renderMediaTable() {
      const q = (document.getElementById('media-search')?.value || '').toLowerCase();
      const type = document.getElementById('media-type-filter')?.value || 'all';
      const genre = document.getElementById('media-genre-filter')?.value || 'all';
      const statusFilter = document.getElementById('media-status-filter')?.value || 'all';

      const filtered = catalog.filter(m => {
        const matchQ = !q || m.title.toLowerCase().includes(q) || (m.synopsis && m.synopsis.toLowerCase().includes(q));
        const matchT = type === 'all' || m.type === type;
        const matchG = genre === 'all' || (m.genres && m.genres.some(g => g.toLowerCase().includes(genre.toLowerCase())));
        const matchS = statusFilter === 'all' || (statusFilter === 'visible' && m.is_published) || (statusFilter === 'hidden' && !m.is_published);
        return matchQ && matchT && matchG && matchS;
      });

      const total = filtered.length;
      const totalPages = Math.max(1, Math.ceil(total / metadataPageSize));
      if (metadataCurrentPage > totalPages) metadataCurrentPage = totalPages;
      if (metadataCurrentPage < 1) metadataCurrentPage = 1;

      const countBadge = document.getElementById('catalog-count-badge');
      if (countBadge) {
        countBadge.innerText = `${total} Title${total === 1 ? '' : 's'}`;
      }

      const pInfo = document.getElementById('metadata-pagination-info');
      const pDisplay = document.getElementById('metadata-page-display');
      const btnFirst = document.getElementById('btn-meta-first');
      const btnPrev = document.getElementById('btn-meta-prev');
      const btnNext = document.getElementById('btn-meta-next');
      const btnLast = document.getElementById('btn-meta-last');

      const cont = document.getElementById('media-tbody');
      if (total === 0) {
        cont.innerHTML = '<div class="p-8 text-center text-neutral-500 text-xs">No media titles found matching your filter.</div>';
        if (pInfo) pInfo.innerText = 'Showing 0 to 0 of 0 titles';
        if (pDisplay) pDisplay.innerText = 'Page 1 of 1';
        if (btnFirst) btnFirst.disabled = true;
        if (btnPrev) btnPrev.disabled = true;
        if (btnNext) btnNext.disabled = true;
        if (btnLast) btnLast.disabled = true;
        return;
      }

      const pageItems = filtered.slice((metadataCurrentPage - 1) * metadataPageSize, metadataCurrentPage * metadataPageSize);

      cont.innerHTML = pageItems.map(m => `
        <div class="p-4 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-[#181818] transition ${m.is_published ? '' : 'bg-neutral-950/40'}">
          <div class="flex items-center gap-4 min-w-0">
            <img src="${escapeHtml(m.poster_url || m.backdrop_url || '')}" class="w-12 h-16 object-cover rounded-lg bg-neutral-900 border border-neutral-800 flex-shrink-0 transition-opacity ${m.is_published ? '' : 'opacity-40 grayscale-[60%]'}" alt="">
            <div class="space-y-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-bold text-sm text-white truncate">${escapeHtml(m.title)}</span>
                <span class="px-2 py-0.5 bg-neutral-800 rounded text-[10px] uppercase font-bold text-neutral-400">${escapeHtml(m.type)}</span>
                ${m.is_published ? '<span class="px-2 py-0.5 bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 rounded text-[10px] font-bold">👁️ Visible</span>' : '<span class="px-2 py-0.5 bg-neutral-800 text-neutral-400 border border-neutral-700 rounded text-[10px] font-bold">🚫 Hidden</span>'}
                ${m.is_featured_billboard ? '<span class="px-2 py-0.5 bg-amber-500/20 text-amber-300 border border-amber-500/40 rounded text-[10px] font-bold">★ Billboard #1</span>' : ''}
                ${m.trailer_youtube_key ? '<span class="px-2 py-0.5 bg-emerald-500/10 text-emerald-400 rounded text-[10px] font-bold">Trailer Active</span>' : '<span class="px-2 py-0.5 bg-amber-500/10 text-amber-400 rounded text-[10px] font-bold">Missing Trailer</span>'}
                ${(() => {
                  const bName = m.badge || (m.badge_type && m.badge_type !== 'none' ? (m.badge_type === 'recently_added' ? 'Recently Added' : 'New Season') : '');
                  if (!bName || bName.toLowerCase() === 'none') return '';
                  let remaining = '';
                  if (m.badge_expires_at) {
                    const diffDays = Math.ceil((new Date(m.badge_expires_at).getTime() - Date.now()) / (1000 * 60 * 60 * 24));
                    if (diffDays <= 0) return '';
                    remaining = ` (${diffDays}d left)`;
                  }
                  return `<span class="px-2 py-0.5 bg-red-600/20 text-red-400 border border-red-500/30 rounded text-[10px] font-bold">🏷️ ${escapeHtml(bName)}${remaining}</span>`;
                })()}
              </div>
              <p class="text-neutral-400 text-xs line-clamp-1">${escapeHtml(m.synopsis || 'No storyline set yet.')}</p>
              <div class="text-[11px] text-neutral-500 flex gap-3 flex-wrap">
                <span>Year: ${escapeHtml(m.release_year)}</span>
                <span>Rating: ${escapeHtml(m.maturity_rating || 'U/A 16+')}</span>
                <span>Genres: ${escapeHtml((m.genres || []).join(', '))}</span>
              </div>
            </div>
          </div>
          <div class="flex items-center gap-2 flex-shrink-0 flex-wrap">
            <button type="button" data-id="${escapeHtml(m.id)}" onclick="togglePublished(this.dataset.id)" class="px-2.5 py-1.5 rounded-xl text-xs font-semibold cursor-pointer transition flex items-center gap-1.5 ${m.is_published ? 'bg-neutral-800 hover:bg-neutral-700 text-neutral-300 hover:text-white' : 'bg-emerald-600/20 hover:bg-emerald-600 text-emerald-400 hover:text-white border border-emerald-500/30'}">
              <span>${m.is_published ? '🚫 Hide' : '👁️ Publish'}</span>
            </button>
            <button type="button" data-id="${escapeHtml(m.id)}" onclick="toggleBillboard(this.dataset.id)" class="px-3 py-1.5 rounded-xl text-xs font-semibold cursor-pointer transition flex items-center gap-1.5 ${m.is_featured_billboard ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40 hover:bg-amber-500/30' : 'bg-neutral-800/80 text-neutral-400 hover:text-white hover:bg-neutral-800 border border-transparent'}">
              <span>${m.is_featured_billboard ? '★ Active on Billboard' : '☆ Set as Billboard'}</span>
            </button>
            <button type="button" data-id="${escapeHtml(m.id)}" onclick="editMedia(this.dataset.id)" class="px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700 text-white rounded-xl text-xs font-semibold cursor-pointer transition">Edit Metadata</button>
            <button type="button" data-id="${escapeHtml(m.id)}" onclick="openDeleteModal(this.dataset.id)" class="px-3 py-1.5 bg-red-600/20 hover:bg-red-600 text-red-400 hover:text-white border border-red-500/30 hover:border-red-600 rounded-xl text-xs font-bold transition flex items-center gap-1 cursor-pointer" title="Permanently delete from DB and /media/library/">
              <span>🗑️ Delete</span>
            </button>
          </div>
        </div>
      `).join('');

      if (pInfo) {
        const start = (metadataCurrentPage - 1) * metadataPageSize + 1;
        const end = Math.min(metadataCurrentPage * metadataPageSize, total);
        pInfo.innerText = `Showing ${start} to ${end} of ${total} titles`;
      }
      if (pDisplay) {
        pDisplay.innerText = `Page ${metadataCurrentPage} of ${totalPages}`;
      }
      if (btnFirst) btnFirst.disabled = (metadataCurrentPage <= 1);
      if (btnPrev) btnPrev.disabled = (metadataCurrentPage <= 1);
      if (btnNext) btnNext.disabled = (metadataCurrentPage >= totalPages);
      if (btnLast) btnLast.disabled = (metadataCurrentPage >= totalPages);
    }

    const AVAILABLE_GENRES = [
      "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary",
      "Drama", "Family", "Fantasy", "History", "Horror", "Music",
      "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western"
    ];

    const ALL_AUDIO_LANGS = [
      "English", "Hindi", "Tamil", "Telugu", "Spanish", "French", "German",
      "Japanese", "Korean", "Italian", "Portuguese", "Mandarin", "Russian",
      "Arabic", "Bengali", "Chinese", "Indonesian", "Romanian"
    ];

    const ALL_SUBTITLE_LANGS = [
      "English", "Hindi", "Tamil", "Telugu", "Spanish", "French", "German",
      "Japanese", "Korean", "Italian", "Portuguese", "Mandarin", "Russian",
      "Arabic", "Bengali", "Chinese", "Indonesian", "Romanian", "None"
    ];

    const AVAILABLE_AUDIO = ALL_AUDIO_LANGS;
    const AVAILABLE_SUBTITLES = ALL_SUBTITLE_LANGS;

    const LANG_CODE_MAP = {
      'English': 'en',
      'Hindi': 'hi',
      'Tamil': 'ta',
      'Telugu': 'te',
      'Japanese': 'ja',
      'Korean': 'ko',
      'Spanish': 'es',
      'French': 'fr',
      'German': 'de',
      'Italian': 'it',
      'Portuguese': 'pt',
      'Mandarin': 'zh',
      'Russian': 'ru',
      'Arabic': 'ar',
      'Bengali': 'bn',
      'Chinese': 'zh',
      'Indonesian': 'id',
      'Romanian': 'ro'
    };

    const THREE_TO_TWO_LANG_MAP = {
      'eng': 'en', 'hin': 'hi', 'tam': 'ta', 'tel': 'te',
      'jpn': 'ja', 'kor': 'ko', 'spa': 'es', 'fre': 'fr', 'fra': 'fr',
      'ger': 'de', 'deu': 'de', 'ita': 'it', 'por': 'pt',
      'zho': 'zh', 'chi': 'zh', 'cmn': 'zh',
      'rus': 'ru', 'ara': 'ar', 'ben': 'bn',
      'ind': 'id', 'msa': 'id', 'may': 'id',
      'ron': 'ro', 'rum': 'ro'
    };

    function getLangCode(lang) {
      if (!lang) return 'und';
      return LANG_CODE_MAP[lang] || THREE_TO_TWO_LANG_MAP[lang.toLowerCase()] || lang.slice(0, 2).toLowerCase();
    }

    const CODE_TO_LANG_MAP = Object.fromEntries(Object.entries(LANG_CODE_MAP).map(([k, v]) => [v, k]));
    // Map 3-letter codes to full language names as well
    Object.entries(THREE_TO_TWO_LANG_MAP).forEach(([three, two]) => {
      if (CODE_TO_LANG_MAP[two] && !CODE_TO_LANG_MAP[three]) {
        CODE_TO_LANG_MAP[three] = CODE_TO_LANG_MAP[two];
      }
    });

    function getLangName(code) {
      if (!code) return '';
      const c = code.toLowerCase().trim();
      return CODE_TO_LANG_MAP[c] || (THREE_TO_TWO_LANG_MAP[c] && CODE_TO_LANG_MAP[THREE_TO_TWO_LANG_MAP[c]]) || code;
    }

    function cleanTrackWatermarks(raw) {
      if (raw === null || raw === undefined) return '';
      return String(raw).replace(/<[^>]*>/g, '').trim();
    }

    function renderCustomLangDropdown({ id, className, dataIdx, currentCode, langList, onChangeFnName }) {
      const norm = (currentCode || 'en').toLowerCase().trim();
      let selectedName = 'English';
      let selectedCode = norm;
      for (const name of langList) {
        const code = (LANG_CODE_MAP[name] || name.slice(0, 2)).toLowerCase();
        if (norm === code || norm === name.toLowerCase()) {
          selectedName = name;
          selectedCode = code;
          break;
        }
      }
      const menuId = `custom-lang-menu-${id}`;
      const labelId = `custom-lang-label-${id}`;

      const safeDataIdx = dataIdx !== undefined && dataIdx !== null ? (typeof dataIdx === 'string' ? `'${dataIdx}'` : dataIdx) : 'null';
      const optionsHtml = langList.map(name => {
        const code = (LANG_CODE_MAP[name] || name.slice(0, 2)).toLowerCase();
        const isSel = (code === selectedCode);
        return `
          <button
            type="button"
            onclick="event.stopPropagation(); selectCustomLangOption('${id}', '${code}', '${escapeHtml(name)} (${code})', '${onChangeFnName || ''}', ${safeDataIdx})"
            class="w-full text-left px-3 py-1.5 text-xs rounded-lg transition flex items-center justify-between cursor-pointer ${
              isSel ? 'bg-red-600/20 text-red-400 font-bold' : 'text-neutral-200 hover:bg-neutral-800'
            }"
          >
            <span>${escapeHtml(name)} (${code})</span>
            ${isSel ? '<span class="text-red-500 font-bold">✓</span>' : ''}
          </button>
        `;
      }).join('');

      return `
        <div class="relative inline-block w-40 flex-shrink-0 custom-lang-dropdown-wrap" onclick="event.stopPropagation()">
          <input type="hidden" id="${id}" data-idx="${dataIdx !== undefined && dataIdx !== null ? dataIdx : ''}" class="${className || ''}" value="${escapeHtml(selectedCode)}">
          <button
            type="button"
            onclick="event.stopPropagation(); toggleCustomLangMenu('${menuId}')"
            class="w-full bg-black border border-neutral-700 hover:border-neutral-500 focus:border-red-500 rounded-lg px-2.5 py-1.5 text-xs text-white flex items-center justify-between cursor-pointer transition"
          >
            <span id="${labelId}" class="truncate">${escapeHtml(selectedName)} (${escapeHtml(selectedCode)})</span>
            <svg class="w-3.5 h-3.5 text-neutral-400 ml-1.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
          </button>
          <div
            id="${menuId}"
            class="custom-lang-menu hidden absolute left-0 z-[100] mt-1 w-48 max-h-52 overflow-y-auto bg-[#161616] border border-neutral-700 rounded-xl shadow-2xl p-1.5 space-y-0.5"
          >
            ${optionsHtml}
          </div>
        </div>
      `;
    }

    function toggleCustomLangMenu(menuId) {
      const targetMenu = document.getElementById(menuId);
      const isCurrentlyOpen = targetMenu && !targetMenu.classList.contains('hidden');
      document.querySelectorAll('.custom-lang-menu').forEach(m => m.classList.add('hidden'));
      if (targetMenu && !isCurrentlyOpen) {
        targetMenu.classList.remove('hidden');
      }
    }

    function selectCustomLangOption(inputId, code, displayText, onChangeFnName, dataIdx) {
      const inputEl = document.getElementById(inputId);
      if (inputEl) {
        inputEl.value = code;
      }
      const labelEl = document.getElementById(`custom-lang-label-${inputId}`);
      if (labelEl) {
        labelEl.innerText = displayText;
      }
      const menuEl = document.getElementById(`custom-lang-menu-${inputId}`);
      if (menuEl) {
        menuEl.classList.add('hidden');
        menuEl.querySelectorAll('button').forEach(btn => {
          const isSelected = btn.textContent.includes(`(${code})`);
          btn.className = `w-full text-left px-3 py-1.5 text-xs rounded-lg transition flex items-center justify-between cursor-pointer ${
            isSelected ? 'bg-red-600/20 text-red-400 font-bold' : 'text-neutral-200 hover:bg-neutral-800'
          }`;
          const checkSpan = btn.querySelector('span.text-red-500');
          if (isSelected && !checkSpan) {
            btn.insertAdjacentHTML('beforeend', '<span class="text-red-500 font-bold">✓</span>');
          } else if (!isSelected && checkSpan) {
            checkSpan.remove();
          }
        });
      }

      if (onChangeFnName && typeof window[onChangeFnName] === 'function') {
        if (dataIdx !== null && dataIdx !== undefined) {
          window[onChangeFnName](dataIdx, code);
        } else {
          window[onChangeFnName](code);
        }
      }
    }

    function updateModalOpenClass() {
      const mediaModal = document.getElementById('media-modal');
      const ingestModal = document.getElementById('ingest-config-modal');
      const deleteModal = document.getElementById('delete-modal');
      const isAnyOpen = (mediaModal && !mediaModal.classList.contains('hidden')) ||
                        (ingestModal && !ingestModal.classList.contains('hidden')) ||
                        (deleteModal && !deleteModal.classList.contains('hidden'));
      if (isAnyOpen) {
        document.body.classList.add('modal-open');
      } else {
        document.body.classList.remove('modal-open');
      }
    }

    window.toggleCustomLangMenu = toggleCustomLangMenu;
    window.selectCustomLangOption = selectCustomLangOption;
    window.updateModalOpenClass = updateModalOpenClass;

    document.addEventListener('click', (e) => {
      if (!e.target.closest('.custom-lang-dropdown-wrap')) {
        document.querySelectorAll('.custom-lang-menu').forEach(m => m.classList.add('hidden'));
      }
    });

    function renderLangSelectOptions(currentCode, langList) {
      const norm = (currentCode || '').toLowerCase().trim();
      let matched = false;
      let html = langList.map(name => {
        const code = (LANG_CODE_MAP[name] || name.slice(0, 2)).toLowerCase();
        const isSelected = norm === code || norm === name.toLowerCase();
        if (isSelected) matched = true;
        return `<option value="${code}" ${isSelected ? 'selected' : ''}>${escapeHtml(name)} (${code})</option>`;
      }).join('');
      if (!matched && norm && norm !== 'und') {
        html = `<option value="${escapeHtml(norm)}" selected>${escapeHtml(norm.toUpperCase())}</option>` + html;
      }
      return html;
    }

    let selectedGenres = new Set();
    let selectedAudio = new Set();
    let selectedSubs = new Set();

    function initMultiSelects() {
      const genresMenu = document.getElementById('genres-dropdown-menu');
      if (genresMenu) {
        genresMenu.innerHTML = AVAILABLE_GENRES.map(g => `
          <label class="flex items-center gap-2.5 px-2 py-1.5 hover:bg-neutral-800 rounded-lg cursor-pointer text-xs text-neutral-200 select-none">
            <input type="checkbox" value="${escapeHtml(g)}" onchange="toggleGenreSelection('${escapeHtml(g)}', this.checked)" class="genre-checkbox w-3.5 h-3.5 rounded bg-neutral-900 border-neutral-600 text-red-600 focus:ring-0 cursor-pointer">
            <span>${escapeHtml(g)}</span>
          </label>
        `).join('');
      }

      const audioMenu = document.getElementById('audio-dropdown-menu');
      if (audioMenu) {
        audioMenu.innerHTML = AVAILABLE_AUDIO.map(a => `
          <label class="flex items-center gap-2.5 px-2 py-1.5 hover:bg-neutral-800 rounded-lg cursor-pointer text-xs text-neutral-200 select-none">
            <input type="checkbox" value="${escapeHtml(a)}" onchange="toggleAudioSelection('${escapeHtml(a)}', this.checked)" class="audio-checkbox w-3.5 h-3.5 rounded bg-neutral-900 border-neutral-600 text-red-600 focus:ring-0 cursor-pointer">
            <span>${escapeHtml(a)}</span>
          </label>
        `).join('');
      }

      const subMenu = document.getElementById('sub-dropdown-menu');
      if (subMenu) {
        subMenu.innerHTML = AVAILABLE_SUBTITLES.map(s => `
          <label class="flex items-center gap-2.5 px-2 py-1.5 hover:bg-neutral-800 rounded-lg cursor-pointer text-xs text-neutral-200 select-none">
            <input type="checkbox" value="${escapeHtml(s)}" onchange="toggleSubSelection('${escapeHtml(s)}', this.checked)" class="modal-sub-checkbox w-3.5 h-3.5 rounded bg-neutral-900 border-neutral-600 text-red-600 focus:ring-0 cursor-pointer">
            <span>${s === 'None' ? '🚫 None (No Subtitles)' : escapeHtml(s)}</span>
          </label>
        `).join('');
      }

      if (typeof initBulkDropdowns === 'function') {
        initBulkDropdowns();
      }
    }

    function toggleDropdown(type, e) {
      if (e) e.stopPropagation();
      const gMenu = document.getElementById('genres-dropdown-menu');
      const aMenu = document.getElementById('audio-dropdown-menu');
      const sMenu = document.getElementById('sub-dropdown-menu');
      if (type === 'genres') {
        gMenu?.classList.toggle('hidden');
        if (aMenu) aMenu.classList.add('hidden');
        if (sMenu) sMenu.classList.add('hidden');
      } else if (type === 'audio') {
        aMenu?.classList.toggle('hidden');
        if (gMenu) gMenu.classList.add('hidden');
        if (sMenu) sMenu.classList.add('hidden');
      } else if (type === 'sub') {
        sMenu?.classList.toggle('hidden');
        if (gMenu) gMenu.classList.add('hidden');
        if (aMenu) aMenu.classList.add('hidden');
      }
    }

    document.addEventListener('click', (e) => {
      const gCont = document.getElementById('genres-dropdown-container');
      const aCont = document.getElementById('audio-dropdown-container');
      const sCont = document.getElementById('sub-dropdown-container');
      if (gCont && !gCont.contains(e.target)) {
        document.getElementById('genres-dropdown-menu')?.classList.add('hidden');
      }
      if (aCont && !aCont.contains(e.target)) {
        document.getElementById('audio-dropdown-menu')?.classList.add('hidden');
      }
      if (sCont && !sCont.contains(e.target)) {
        document.getElementById('sub-dropdown-menu')?.classList.add('hidden');
      }
    });

    function toggleGenreSelection(g, checked) {
      if (checked) selectedGenres.add(g);
      else selectedGenres.delete(g);
      updateGenresDisplay();
    }

    function updateGenresDisplay() {
      const disp = document.getElementById('genres-display-text');
      if (!disp) return;
      const arr = Array.from(selectedGenres);
      if (arr.length === 0) {
        disp.textContent = 'Select genres...';
        disp.className = 'truncate text-neutral-400';
      } else {
        disp.textContent = arr.join(', ');
        disp.className = 'truncate text-white font-medium';
      }
    }

    function toggleAudioSelection(a, checked) {
      if (checked) selectedAudio.add(a);
      else selectedAudio.delete(a);
      updateAudioDisplay();
    }

    function updateAudioDisplay() {
      const disp = document.getElementById('audio-display-text');
      if (!disp) return;
      const arr = Array.from(selectedAudio);
      if (arr.length === 0) {
        disp.textContent = 'Select audio languages...';
        disp.className = 'truncate text-neutral-400';
      } else {
        disp.textContent = arr.join(', ');
        disp.className = 'truncate text-white font-medium';
      }
    }

    function toggleSubSelection(s, checked) {
      if (s === 'None') {
        selectedSubs.clear();
        if (checked) selectedSubs.add('None');
      } else {
        selectedSubs.delete('None');
        if (checked) selectedSubs.add(s);
        else selectedSubs.delete(s);
        if (selectedSubs.size === 0) selectedSubs.add('None');
      }
      document.querySelectorAll('.modal-sub-checkbox').forEach(cb => {
        cb.checked = selectedSubs.has(cb.value);
      });
      updateSubDisplay();
    }

    function updateSubDisplay() {
      const disp = document.getElementById('sub-display-text');
      if (!disp) return;
      const arr = Array.from(selectedSubs);
      if (arr.length === 0 || (arr.length === 1 && arr[0] === 'None')) {
        disp.textContent = 'None (No Subtitles)';
        disp.className = 'truncate text-neutral-400';
      } else {
        disp.textContent = arr.filter(s => s !== 'None').join(', ');
        disp.className = 'truncate text-white font-medium';
      }
    }

    function setGenresInModal(genresArray) {
      selectedGenres.clear();
      (genresArray || []).forEach(g => {
        const trimmed = (g || '').trim();
        if (trimmed) selectedGenres.add(trimmed);
      });
      document.querySelectorAll('.genre-checkbox').forEach(cb => {
        cb.checked = selectedGenres.has(cb.value);
      });
      updateGenresDisplay();
    }

    function setAudioInModal(audioArray) {
      selectedAudio.clear();
      (audioArray || []).forEach(a => {
        const trimmed = (a || '').trim();
        if (trimmed) selectedAudio.add(trimmed);
      });
      if (selectedAudio.size === 0) {
        selectedAudio.add('English');
      }
      document.querySelectorAll('.audio-checkbox').forEach(cb => {
        cb.checked = selectedAudio.has(cb.value);
      });
      updateAudioDisplay();
    }

    function setSubsInModal(subArray) {
      selectedSubs.clear();
      (subArray || []).forEach(s => {
        const trimmed = (s || '').trim();
        if (trimmed) selectedSubs.add(trimmed);
      });
      if (selectedSubs.size === 0) {
        selectedSubs.add('None');
      }
      document.querySelectorAll('.modal-sub-checkbox').forEach(cb => {
        cb.checked = selectedSubs.has(cb.value);
      });
      updateSubDisplay();
    }

    function updateBadgeStatusPill(badge, expiresAt) {
      const pill = document.getElementById('badge-status-pill');
      if (!pill) return;
      if (!badge || badge.toLowerCase() === 'none') {
        pill.innerText = 'No Badge';
        pill.className = 'text-[10px] font-mono px-2 py-0.5 rounded bg-neutral-800 text-neutral-400 border border-neutral-700';
        return;
      }
      if (!expiresAt) {
        pill.innerText = `🏷️ ${badge} (Never expires)`;
        pill.className = 'text-[10px] font-mono px-2 py-0.5 rounded bg-red-600/20 text-red-400 border border-red-500/30 font-bold';
        return;
      }
      const expDate = new Date(expiresAt);
      const diffMs = expDate.getTime() - Date.now();
      const diffDays = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
      if (diffDays <= 0) {
        pill.innerText = `Expired (${badge})`;
        pill.className = 'text-[10px] font-mono px-2 py-0.5 rounded bg-neutral-800 text-neutral-500 border border-neutral-700';
      } else {
        pill.innerText = `🏷️ ${badge} (${diffDays}d left)`;
        pill.className = 'text-[10px] font-mono px-2 py-0.5 rounded bg-red-600/20 text-red-400 border border-red-500/30 font-bold';
      }
    }

    function onBadgePresetChange(val) {
      const customInput = document.getElementById('edit-badge-custom');
      if (val === 'custom') {
        customInput?.classList.remove('hidden');
        customInput?.focus();
      } else {
        customInput?.classList.add('hidden');
      }
      const badgeText = val === 'custom' ? (customInput?.value || '') : (val === 'none' ? '' : val);
      const item = catalog.find(m => m.id === document.getElementById('edit-id')?.value);
      updateBadgeStatusPill(badgeText, item?.badge_expires_at);
    }

    function onBadgeTimerChange(val) {
      const customDays = document.getElementById('edit-badge-custom-days');
      if (val === 'custom') {
        customDays?.classList.remove('hidden');
        customDays?.focus();
      } else {
        customDays?.classList.add('hidden');
      }
    }

    function openMediaModal(item) {
      if (!item) {
        showAdminToast({ type: 'warning', title: 'Action Disabled', message: 'Manual title creation is disabled. Media must be ingested from incoming video files.' });
        return;
      }
      if (!document.getElementById('audio-dropdown-menu')?.children?.length) {
        initMultiSelects();
      }
      document.getElementById('media-modal').classList.remove('hidden');
      document.getElementById('media-modal').classList.add('flex');
      updateModalOpenClass();
      document.getElementById('modal-title').innerText = `Edit "${item.title}" Metadata`;
      document.getElementById('edit-id').value = item.id;
      
      const titleInput = document.getElementById('edit-title');
      titleInput.value = item.title;
      titleInput.disabled = true;

      const typeInput = document.getElementById('edit-type');
      typeInput.value = item.type;
      typeInput.disabled = true;

      const yearInput = document.getElementById('edit-year');
      yearInput.value = item.release_year;
      yearInput.disabled = true;

      document.getElementById('edit-synopsis').value = item.synopsis || '';
      document.getElementById('edit-trailer').value = item.trailer_youtube_key || '';
      document.getElementById('edit-rating').value = item.maturity_rating || 'U/A 16+';
      document.getElementById('edit-runtime').value = item.runtime_minutes || 120;
      setGenresInModal(item.genres || []);
      setAudioInModal(item.audio_languages || ['English']);

      const billboardToggle = document.getElementById('edit-billboard-toggle');
      if (billboardToggle) {
        billboardToggle.checked = Boolean(item.is_featured_billboard);
      }

      const visibilityToggle = document.getElementById('edit-visibility-toggle');
      if (visibilityToggle) {
        visibilityToggle.checked = Boolean(item.is_published);
      }

      // Promotional Badge & Expiration UI
      const badgeVal = item.badge || (item.badge_type && item.badge_type !== 'none' ? (item.badge_type === 'recently_added' ? 'Recently Added' : 'New Season') : '');
      const presetSelect = document.getElementById('edit-badge-preset');
      const customInput = document.getElementById('edit-badge-custom');
      const timerSelect = document.getElementById('edit-badge-timer');
      const customDaysInput = document.getElementById('edit-badge-custom-days');

      const knownPresets = ['Recently Added', 'New Season', 'New Episode', 'Most Liked', 'Top 10', 'Trending Now', 'Leaving Soon'];
      if (!badgeVal) {
        if (presetSelect) presetSelect.value = 'none';
        if (customInput) { customInput.value = ''; customInput.classList.add('hidden'); }
      } else if (knownPresets.includes(badgeVal)) {
        if (presetSelect) presetSelect.value = badgeVal;
        if (customInput) { customInput.value = ''; customInput.classList.add('hidden'); }
      } else {
        if (presetSelect) presetSelect.value = 'custom';
        if (customInput) { customInput.value = badgeVal; customInput.classList.remove('hidden'); }
      }

      if (timerSelect) timerSelect.value = '10';
      if (customDaysInput) { customDaysInput.value = ''; customDaysInput.classList.add('hidden'); }
      updateBadgeStatusPill(badgeVal, item.badge_expires_at);

      // Subtitles in modal (for movies only, series subtitles are managed per episode)
      if (item.type !== 'series') {
        fetch(`/api/admin/streams/${item.id}/tracks`)
          .then(r => r.ok ? r.json() : null)
          .then(data => {
            const subs = (data?.subtitle_tracks || []).map(t => getLangName(t.lang) || t.label).filter(Boolean);
            setSubsInModal(subs.length > 0 ? subs : ['None']);
          })
          .catch(() => setSubsInModal(['None']));
      } else {
        setSubsInModal(['None']);
      }

      document.getElementById('edit-poster').value = item.poster_url || '';
      document.getElementById('edit-backdrop').value = item.backdrop_url || '';

      const banner = document.getElementById('series-banner-indicator');
      if (item.type === 'series') {
        if (banner) banner.classList.remove('hidden');
        document.getElementById('movie-tracks-section')?.classList.add('hidden');
        loadSeriesEpisodes(item.id);
      } else {
        if (banner) banner.classList.add('hidden');
        document.getElementById('series-episodes-section')?.classList.add('hidden');
        currentModalSeriesEpisodes = [];
        loadMovieTracks(item.id);
      }
    }

    function closeMediaModal() {
      document.getElementById('media-modal').classList.remove('flex');
      document.getElementById('media-modal').classList.add('hidden');
      document.getElementById('genres-dropdown-menu')?.classList.add('hidden');
      document.getElementById('audio-dropdown-menu')?.classList.add('hidden');
      document.getElementById('sub-dropdown-menu')?.classList.add('hidden');
      document.getElementById('series-episodes-section')?.classList.add('hidden');
      document.getElementById('movie-tracks-section')?.classList.add('hidden');
      document.getElementById('series-banner-indicator')?.classList.add('hidden');
      currentModalSeriesEpisodes = [];
      updateModalOpenClass();
    }

    let currentModalSeriesEpisodes = [];

    async function loadSeriesEpisodes(mediaId) {
      const section = document.getElementById('series-episodes-section');
      const loading = document.getElementById('episodes-loading');
      const empty = document.getElementById('episodes-empty');
      const editor = document.getElementById('episodes-editor');
      const countBadge = document.getElementById('episodes-count-badge');

      section.classList.remove('hidden');
      loading.classList.remove('hidden');
      empty.classList.add('hidden');
      editor.classList.add('hidden');
      currentModalSeriesEpisodes = [];

      try {
        const res = await fetch(`/api/admin/series/${mediaId}/episodes`);
        if (!res.ok) {
          throw new Error('Failed to load episodes');
        }
        const data = await res.json();
        currentModalSeriesEpisodes = data.episodes || [];
        loading.classList.add('hidden');

        if (currentModalSeriesEpisodes.length === 0) {
          empty.classList.remove('hidden');
          countBadge.innerText = '0 Episodes';
          return;
        }

        countBadge.innerText = `${currentModalSeriesEpisodes.length} Episodes`;
        editor.classList.remove('hidden');

        // Populate subtitle languages in modal if available from episodes
        if (currentModalSeriesEpisodes.length > 0) {
          const epSubs = new Set();
          currentModalSeriesEpisodes.forEach(e => {
            (e.subtitle_tracks || []).forEach(st => {
              const name = getLangName(st.lang) || st.label;
              if (name && name !== 'None') epSubs.add(name);
            });
          });
          if (epSubs.size > 0 && (selectedSubs.size === 0 || selectedSubs.has('None'))) {
            setSubsInModal(Array.from(epSubs));
          }
        }

        // Populate Season Dropdown with distinct season numbers
        const seasonSelect = document.getElementById('ep-season-select');
        seasonSelect.innerHTML = '';
        const seasons = data.seasons && data.seasons.length > 0
          ? data.seasons
          : Array.from(new Set(currentModalSeriesEpisodes.map(e => e.season_number))).sort((a,b) => a-b);

        seasons.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s;
          opt.textContent = `Season ${s}`;
          seasonSelect.appendChild(opt);
        });

        // Trigger Season Change to populate episodes
        onSeasonChange();
      } catch (err) {
        loading.classList.add('hidden');
        empty.classList.remove('hidden');
        empty.innerText = 'Error loading episodes: ' + err.message;
      }
    }

    function onSeasonChange() {
      const seasonSelect = document.getElementById('ep-season-select');
      const selectedSeason = Number(seasonSelect.value);
      const epSelect = document.getElementById('ep-select');
      epSelect.innerHTML = '';

      const filtered = currentModalSeriesEpisodes.filter(e => Number(e.season_number) === selectedSeason);
      filtered.forEach(ep => {
        const opt = document.createElement('option');
        opt.value = ep.id;
        opt.textContent = `${ep.code || ('S' + ep.season_number + ':E' + ep.episode_number)} — ${ep.title}`;
        epSelect.appendChild(opt);
      });

      if (filtered.length > 0) {
        onEpisodeChange();
      }
    }

    function onEpisodeChange() {
      const epSelect = document.getElementById('ep-select');
      const epId = epSelect.value;
      const ep = currentModalSeriesEpisodes.find(e => e.id === epId);
      if (!ep) return;

      document.getElementById('ep-edit-id').value = ep.id;
      document.getElementById('ep-edit-title').value = ep.title || '';
      document.getElementById('ep-edit-number').value = ep.episode_number || 1;
      document.getElementById('ep-edit-duration').value = ep.duration_seconds || 0;
      document.getElementById('ep-edit-published').checked = ep.is_published !== false;
      document.getElementById('ep-edit-still').value = ep.still_url || '';
      document.getElementById('ep-edit-synopsis').value = ep.synopsis || '';
      document.getElementById('save-episode-code').innerText = `(${ep.code || ('S' + ep.season_number + ':E' + ep.episode_number)})`;
      updateEpisodeStillPreview();
      renderEpisodeTracks(ep);
    }

    function renderEpisodeTracks(ep) {
      const audioList = document.getElementById('ep-audio-tracks-list');
      const subList = document.getElementById('ep-sub-tracks-list');
      if (!audioList || !subList) return;

      const audioTracks = ep.audio_tracks || [];
      const subTracks = ep.subtitle_tracks || [];

      if (audioTracks.length === 0 && subTracks.length === 0) {
        audioList.innerHTML = `<div class="text-[11px] text-neutral-500 italic py-1">Loading episode stream tracks...</div>`;
        subList.innerHTML = '';
        fetch(`/api/admin/streams/${ep.id}/tracks`)
          .then(r => r.ok ? r.json() : null)
          .then(data => {
            if (data && (data.audio_tracks?.length > 0 || data.subtitle_tracks?.length > 0)) {
              const loadedAudio = data.audio_tracks || [];
              if (loadedAudio.length > 0 && !loadedAudio.some(a => a.is_default)) {
                loadedAudio[0].is_default = true;
              }
              ep.audio_tracks = loadedAudio;
              ep.subtitle_tracks = data.subtitle_tracks || [];
              renderEpisodeTracks(ep);
            } else {
              audioList.innerHTML = `<div class="text-[11px] text-neutral-500 italic py-1">No stream tracks found for this episode.</div>`;
              subList.innerHTML = '';
            }
          })
          .catch(() => {
            audioList.innerHTML = `<div class="text-[11px] text-neutral-500 italic py-1">No stream tracks found.</div>`;
            subList.innerHTML = '';
          });
        return;
      }

      const hasAnyEpDef = audioTracks.some(x => x.is_default);

      audioList.innerHTML = `
        <div class="text-[11px] font-bold text-neutral-400 uppercase tracking-wider">Audio Tracks</div>
        <div class="space-y-2.5 w-full">
          ${audioTracks.map((t, idx) => {
            const isDef = hasAnyEpDef ? Boolean(t.is_default) : (idx === 0);
            return `
            <div class="flex items-center gap-2 bg-[#141414] border border-neutral-800 rounded-lg px-2.5 py-1.5 text-xs w-full">
              <span class="text-neutral-500 font-mono text-[10px] w-16 flex-shrink-0">Audio #${idx+1}</span>
              ${renderCustomLangDropdown({
                id: 'ep-audio-lang-' + idx,
                className: 'ep-audio-lang-select',
                dataIdx: idx,
                currentCode: t.lang || 'en',
                langList: ALL_AUDIO_LANGS,
                onChangeFnName: 'onEpAudioLangChange'
              })}
              <input 
                type="text" 
                data-idx="${idx}" 
                onclick="event.stopPropagation()" 
                onmousedown="event.stopPropagation()" 
                value="${escapeHtml(cleanTrackWatermarks(t.label || 'Audio ' + (idx+1)))}" 
                class="ep-audio-label-input flex-1 bg-black border border-neutral-700 focus:border-red-500 rounded px-2 py-0.5 text-xs text-white" 
                placeholder="Display Name"
              >
              <button
                type="button"
                onclick="setEpisodeDefaultAudio(${idx})"
                id="ep-audio-default-btn-${idx}"
                data-idx="${idx}"
                data-default="${isDef ? 'true' : 'false'}"
                title="Set as Default Startup Audio"
                class="${isDef 
                  ? 'px-2 py-1 rounded text-[10px] cursor-pointer transition border border-amber-500/50 bg-amber-500/20 text-amber-400 font-bold flex items-center gap-1 flex-shrink-0' 
                  : 'px-2 py-1 rounded text-[10px] cursor-pointer transition border border-neutral-700 bg-neutral-800 text-neutral-400 hover:text-white flex items-center gap-1 flex-shrink-0'}"
              >
                ${isDef ? '★ Default' : '☆ Set Default'}
              </button>
            </div>
          `;
          }).join('')}
        </div>
      `;

      subList.innerHTML = `
        <div class="text-[11px] font-bold text-neutral-400 uppercase tracking-wider pt-1">Subtitle Tracks</div>
        <div class="space-y-2.5 w-full">
          ${subTracks.map((t, idx) => `
            <div class="flex items-center gap-2 bg-[#141414] border border-neutral-800 rounded-lg px-2.5 py-1.5 text-xs w-full">
              <span class="text-neutral-500 font-mono text-[10px] w-16 flex-shrink-0">Sub #${idx+1}</span>
              ${renderCustomLangDropdown({
                id: 'ep-sub-lang-' + idx,
                className: 'ep-sub-lang-select',
                dataIdx: idx,
                currentCode: t.lang || 'en',
                langList: ALL_SUBTITLE_LANGS.filter(l => l !== 'None'),
                onChangeFnName: 'onEpSubLangChange'
              })}
              <input 
                type="text" 
                id="ep-sub-input-${idx}"
                data-idx="${idx}" 
                onclick="event.stopPropagation()" 
                onmousedown="event.stopPropagation()" 
                value="${escapeHtml(cleanTrackWatermarks(t.label || 'Sub ' + (idx+1)))}" 
                class="ep-sub-label-input flex-1 bg-black border border-neutral-700 focus:border-red-500 rounded px-2 py-0.5 text-xs text-white" 
                placeholder="Display Name"
              >
              <button
                type="button"
                data-epid="${escapeHtml(ep.id)}"
                data-subid="${escapeHtml(t.id || idx)}"
                onclick="deleteEpisodeSubtitle(this.dataset.epid, this.dataset.subid, this)"
                class="px-2 py-1 rounded text-[10px] cursor-pointer transition border border-red-500/40 bg-red-600/20 text-red-300 hover:bg-red-600 hover:text-white flex items-center gap-1 flex-shrink-0"
                title="Remove this subtitle track"
              >
                🗑️
              </button>
            </div>
          `).join('')}
        </div>
      `;
    }

    function updateEpisodeStillPreview() {
      const url = document.getElementById('ep-edit-still').value.trim();
      const img = document.getElementById('ep-edit-still-preview');
      const placeholder = document.getElementById('ep-edit-still-placeholder');

      if (url) {
        img.src = url;
        img.onload = () => {
          img.classList.remove('hidden');
          placeholder.classList.add('hidden');
        };
        img.onerror = () => {
          img.classList.add('hidden');
          placeholder.classList.remove('hidden');
          placeholder.innerText = 'Invalid Image';
        };
      } else {
        img.src = '';
        img.classList.add('hidden');
        placeholder.classList.remove('hidden');
        placeholder.innerText = '16:9 Preview';
      }
    }

    function setEpisodeDefaultAudio(selectedIdx) {
      const epId = document.getElementById('ep-edit-id')?.value;
      const ep = currentModalSeriesEpisodes.find(e => e.id === epId);
      const audioTracks = ep?.audio_tracks || [];
      audioTracks.forEach((t, i) => {
        t.is_default = (i === selectedIdx);
        const btn = document.getElementById(`ep-audio-default-btn-${i}`);
        if (btn) {
          const isThisDefault = (i === selectedIdx);
          btn.setAttribute('data-default', isThisDefault ? 'true' : 'false');
          btn.className = isThisDefault
            ? 'px-2 py-1 rounded text-[10px] cursor-pointer transition border border-amber-500/50 bg-amber-500/20 text-amber-400 font-bold flex items-center gap-1 flex-shrink-0'
            : 'px-2 py-1 rounded text-[10px] cursor-pointer transition border border-neutral-700 bg-neutral-800 text-neutral-400 hover:text-white flex items-center gap-1 flex-shrink-0';
          btn.innerHTML = isThisDefault ? '★ Default' : '☆ Set Default';
        }
      });
    }

    async function saveSelectedEpisode() {
      const epId = document.getElementById('ep-edit-id').value;
      if (!epId) {
        showAdminToast('No episode selected to save.', 'warning');
        return;
      }

      const title = document.getElementById('ep-edit-title').value.trim();
      if (!title) {
        showAdminToast('Episode title is required.', 'warning');
        return;
      }

      const epNumber = Number(document.getElementById('ep-edit-number').value) || 1;
      const duration = Number(document.getElementById('ep-edit-duration').value) || 0;
      const isPublished = document.getElementById('ep-edit-published').checked;
      const stillUrl = document.getElementById('ep-edit-still').value.trim() || null;
      const synopsis = document.getElementById('ep-edit-synopsis').value.trim() || null;

      const payload = {
        title: title,
        episode_number: epNumber,
        duration_seconds: duration,
        is_published: isPublished,
        still_url: stillUrl,
        synopsis: synopsis
      };

      const btn = document.getElementById('save-episode-btn');
      const textSpan = document.getElementById('save-episode-text');
      const codeSpan = document.getElementById('save-episode-code');
      btn.disabled = true;
      if (textSpan) textSpan.innerText = '⏳ Saving...';

      let ep = currentModalSeriesEpisodes.find(e => e.id === epId);

      try {
        const res = await fetch(`/api/admin/episodes/${epId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || 'Failed to update episode');
        }

        const updated = await res.json();

        // Update in-memory cached episode array
        if (ep) {
          ep.title = updated.title || title;
          ep.episode_number = updated.episode_number || epNumber;
          ep.duration_seconds = updated.duration_seconds ?? duration;
          ep.is_published = updated.is_published ?? isPublished;
          ep.still_url = updated.still_url ?? stillUrl;
          ep.synopsis = updated.synopsis ?? synopsis;
          ep.code = `S${ep.season_number}:E${ep.episode_number}`;

          // Update option text in dropdown
          const epSelect = document.getElementById('ep-select');
          if (epSelect) {
            for (let i = 0; i < epSelect.options.length; i++) {
              if (epSelect.options[i].value === epId) {
                epSelect.options[i].textContent = `${ep.code} — ${ep.title}`;
                break;
              }
            }
          }
        }

        // Also update track display names if present
        const epAudioInputs = Array.from(document.querySelectorAll('.ep-audio-label-input'));
        const epAudioLangSelects = Array.from(document.querySelectorAll('.ep-audio-lang-select'));
        const epSubInputs = Array.from(document.querySelectorAll('.ep-sub-label-input'));
        const epSubLangSelects = Array.from(document.querySelectorAll('.ep-sub-lang-select'));
        if (epAudioInputs.length > 0 || epSubInputs.length > 0) {
          const newAudio = (ep?.audio_tracks || []).map((t, idx) => {
            const inp = epAudioInputs.find(i => Number(i.dataset.idx) === idx);
            const sel = epAudioLangSelects.find(s => Number(s.dataset.idx) === idx);
            const btn = document.getElementById(`ep-audio-default-btn-${idx}`);
            const isDef = btn ? btn.getAttribute('data-default') === 'true' : Boolean(t.is_default);
            return {
              ...t,
              index: idx,
              lang: sel ? sel.value : (t.lang || 'en'),
              label: cleanTrackWatermarks(inp ? inp.value.trim() : (t.label || 'Audio')),
              is_default: isDef
            };
          });
          if (newAudio.length > 0 && !newAudio.some(t => t.is_default)) {
            newAudio[0].is_default = true;
          }
          const newSubs = (ep?.subtitle_tracks || []).map((t, idx) => {
            const inp = epSubInputs.find(i => Number(i.dataset.idx) === idx);
            const sel = epSubLangSelects.find(s => Number(s.dataset.idx) === idx);
            return {
              ...t,
              index: idx,
              lang: sel ? sel.value : (t.lang || 'en'),
              label: cleanTrackWatermarks(inp ? inp.value.trim() : (t.label || 'Subtitle'))
            };
          });

          try {
            const trackRes = await fetch(`/api/admin/streams/${epId}/tracks`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ audio_tracks: newAudio, subtitle_tracks: newSubs })
            });
            if (trackRes.ok) {
              const trackData = await trackRes.json();
              if (ep) {
                ep.audio_tracks = trackData.audio_tracks || newAudio;
                ep.subtitle_tracks = trackData.subtitle_tracks || newSubs;
              }
            }
          } catch (tErr) {
            console.warn('Could not update episode stream track names:', tErr);
          }
        }

        showAdminToast(`✓ Saved metadata for S${ep ? ep.season_number : 1}:E${ep ? ep.episode_number : 1} — ${title}`, 'success');
      } catch (err) {
        showAdminToast(`Failed to save episode: ${err.message}`, 'error');
      } finally {
        btn.disabled = false;
        if (textSpan) textSpan.innerText = '💾 Save Selected Episode';
        if (codeSpan && ep) codeSpan.innerText = `(${ep.code || ('S' + ep.season_number + ':E' + ep.episode_number)})`;
      }
    }

    let currentMovieStreamTracks = { audio_tracks: [], subtitle_tracks: [] };

    async function loadMovieTracks(mediaId) {
      const section = document.getElementById('movie-tracks-section');
      const loading = document.getElementById('movie-tracks-loading');
      const audioList = document.getElementById('movie-audio-tracks-list');
      const subList = document.getElementById('movie-sub-tracks-list');
      if (!section) return;

      section.classList.remove('hidden');
      if (loading) loading.classList.remove('hidden');
      if (audioList) audioList.innerHTML = '';
      if (subList) subList.innerHTML = '';

      try {
        const res = await fetch(`/api/admin/streams/${mediaId}/tracks`);
        if (!res.ok) throw new Error('Failed to load stream tracks');
        const data = await res.json();
        const loadedAudio = data.audio_tracks || [];
        if (loadedAudio.length > 0 && !loadedAudio.some(a => a.is_default)) {
          loadedAudio[0].is_default = true;
        }
        currentMovieStreamTracks = {
          audio_tracks: loadedAudio,
          subtitle_tracks: data.subtitle_tracks || []
        };
        renderMovieTracksUI();
      } catch (e) {
        if (audioList) audioList.innerHTML = `<div class="text-[11px] text-neutral-500 italic py-1">No stream track data available for this movie.</div>`;
      } finally {
        if (loading) loading.classList.add('hidden');
      }
    }

    function setMovieDefaultAudio(selectedIdx) {
      currentMovieStreamTracks.audio_tracks.forEach((t, i) => {
        t.is_default = (i === selectedIdx);
        const btn = document.getElementById(`movie-audio-default-btn-${i}`);
        if (btn) {
          const isThisDefault = (i === selectedIdx);
          btn.setAttribute('data-default', isThisDefault ? 'true' : 'false');
          btn.className = isThisDefault
            ? 'px-2 py-1 rounded text-[10px] cursor-pointer transition border border-amber-500/50 bg-amber-500/20 text-amber-400 font-bold flex items-center gap-1 flex-shrink-0'
            : 'px-2 py-1 rounded text-[10px] cursor-pointer transition border border-neutral-700 bg-neutral-800 text-neutral-400 hover:text-white flex items-center gap-1 flex-shrink-0';
          btn.innerHTML = isThisDefault ? '★ Default' : '☆ Set Default';
        }
      });
    }

    function onMovieSubLangChange(idx, langCode) {
      if (currentMovieStreamTracks?.subtitle_tracks?.[idx]) {
        currentMovieStreamTracks.subtitle_tracks[idx].lang = langCode;
        const langName = getLangName(langCode) || langCode;
        const inp = document.getElementById(`movie-sub-input-${idx}`);
        if (inp) {
          const curVal = inp.value.trim();
          const isGeneric = !curVal || ALL_SUBTITLE_LANGS.some(code => getLangName(code) === curVal || code === curVal) || curVal.startsWith('Sub ') || curVal.startsWith('Subtitle ');
          if (isGeneric) {
            inp.value = langName;
            currentMovieStreamTracks.subtitle_tracks[idx].label = langName;
          }
        }
      }
    }

    function onMovieAudioLangChange(idx, langCode) {
      if (currentMovieStreamTracks?.audio_tracks?.[idx]) {
        currentMovieStreamTracks.audio_tracks[idx].lang = langCode;
        const langName = getLangName(langCode) || langCode;
        const inp = document.querySelector(`.movie-track-label-input[data-type="audio"][data-idx="${idx}"]`);
        if (inp) {
          const curVal = inp.value.trim();
          const isGeneric = !curVal || ALL_AUDIO_LANGS.some(code => getLangName(code) === curVal || code === curVal) || curVal.startsWith('Audio ');
          if (isGeneric) {
            inp.value = langName;
            currentMovieStreamTracks.audio_tracks[idx].label = langName;
          }
        }
      }
    }

    function onEpSubLangChange(idx, langCode) {
      const ep = currentModalSeriesEpisodes.find(e => e.id === document.getElementById('ep-select')?.value);
      if (ep?.subtitle_tracks?.[idx]) {
        ep.subtitle_tracks[idx].lang = langCode;
        const langName = getLangName(langCode) || langCode;
        const inp = document.getElementById(`ep-sub-input-${idx}`);
        if (inp) {
          const curVal = inp.value.trim();
          const isGeneric = !curVal || ALL_SUBTITLE_LANGS.some(code => getLangName(code) === curVal || code === curVal) || curVal.startsWith('Sub ') || curVal.startsWith('Subtitle ');
          if (isGeneric) {
            inp.value = langName;
            ep.subtitle_tracks[idx].label = langName;
          }
        }
      }
    }

    function onEpAudioLangChange(idx, langCode) {
      const ep = currentModalSeriesEpisodes.find(e => e.id === document.getElementById('ep-select')?.value);
      if (ep?.audio_tracks?.[idx]) {
        ep.audio_tracks[idx].lang = langCode;
        const langName = getLangName(langCode) || langCode;
        const inp = document.querySelector(`.ep-audio-label-input[data-idx="${idx}"]`);
        if (inp) {
          const curVal = inp.value.trim();
          const isGeneric = !curVal || ALL_AUDIO_LANGS.some(code => getLangName(code) === curVal || code === curVal) || curVal.startsWith('Audio ');
          if (isGeneric) {
            inp.value = langName;
            ep.audio_tracks[idx].label = langName;
          }
        }
      }
    }

    window.onMovieSubLangChange = onMovieSubLangChange;
    window.onMovieAudioLangChange = onMovieAudioLangChange;
    window.onEpSubLangChange = onEpSubLangChange;
    window.onEpAudioLangChange = onEpAudioLangChange;

    function renderMovieTracksUI() {
      const audioList = document.getElementById('movie-audio-tracks-list');
      const subList = document.getElementById('movie-sub-tracks-list');
      if (!audioList || !subList) return;

      if (currentMovieStreamTracks.audio_tracks.length === 0 && currentMovieStreamTracks.subtitle_tracks.length === 0) {
        audioList.innerHTML = `<div class="text-[11px] text-neutral-500 italic py-1">No audio or subtitle tracks found in media_streams.</div>`;
        subList.innerHTML = '';
        return;
      }

      const hasAnyMovieDef = currentMovieStreamTracks.audio_tracks.some(x => x.is_default);

      audioList.innerHTML = `
        <div class="text-[11px] font-bold text-neutral-400 uppercase tracking-wider">Audio Tracks</div>
        <div class="space-y-2.5 w-full">
          ${currentMovieStreamTracks.audio_tracks.map((t, idx) => {
            const isDef = hasAnyMovieDef ? Boolean(t.is_default) : (idx === 0);
            return `
            <div class="flex items-center gap-2 bg-[#141414] border border-neutral-800 rounded-lg px-2.5 py-1.5 text-xs w-full">
              <span class="text-neutral-500 font-mono text-[10px] w-16 flex-shrink-0">Audio #${idx+1}</span>
              ${renderCustomLangDropdown({
                id: 'movie-audio-lang-' + idx,
                className: 'movie-audio-lang-select',
                dataIdx: idx,
                currentCode: t.lang || 'en',
                langList: ALL_AUDIO_LANGS,
                onChangeFnName: 'onMovieAudioLangChange'
              })}
              <input 
                type="text" 
                data-idx="${idx}" 
                data-type="audio" 
                onclick="event.stopPropagation()" 
                onmousedown="event.stopPropagation()" 
                value="${escapeHtml(cleanTrackWatermarks(t.label || 'Audio ' + (idx+1)))}" 
                class="movie-track-label-input flex-1 bg-black border border-neutral-700 focus:border-red-500 rounded px-2 py-0.5 text-xs text-white" 
                placeholder="Display Name"
              >
              <button
                type="button"
                onclick="setMovieDefaultAudio(${idx})"
                id="movie-audio-default-btn-${idx}"
                data-idx="${idx}"
                data-default="${isDef ? 'true' : 'false'}"
                title="Set as Default Startup Audio"
                class="${isDef 
                  ? 'px-2 py-1 rounded text-[10px] cursor-pointer transition border border-amber-500/50 bg-amber-500/20 text-amber-400 font-bold flex items-center gap-1 flex-shrink-0' 
                  : 'px-2 py-1 rounded text-[10px] cursor-pointer transition border border-neutral-700 bg-neutral-800 text-neutral-400 hover:text-white flex items-center gap-1 flex-shrink-0'}"
              >
                ${isDef ? '★ Default' : '☆ Set Default'}
              </button>
            </div>
          `;
          }).join('')}
        </div>
      `;

      if (currentMovieStreamTracks.subtitle_tracks.length > 0) {
        subList.innerHTML = `
          <div class="text-[11px] font-bold text-neutral-400 uppercase tracking-wider pt-1">Subtitle Tracks</div>
          <div class="space-y-2.5 w-full">
            ${currentMovieStreamTracks.subtitle_tracks.map((t, idx) => `
              <div class="flex items-center gap-2 bg-[#141414] border border-neutral-800 rounded-lg px-2.5 py-1.5 text-xs w-full">
                <span class="text-neutral-500 font-mono text-[10px] w-16 flex-shrink-0">Sub #${idx+1}</span>
                ${renderCustomLangDropdown({
                  id: 'movie-sub-lang-' + idx,
                  className: 'movie-sub-lang-select',
                  dataIdx: idx,
                  currentCode: t.lang || 'en',
                  langList: ALL_SUBTITLE_LANGS.filter(l => l !== 'None'),
                  onChangeFnName: 'onMovieSubLangChange'
                })}
                <input 
                  type="text" 
                  id="movie-sub-input-${idx}"
                  data-idx="${idx}" 
                  data-type="sub" 
                  onclick="event.stopPropagation()" 
                  onmousedown="event.stopPropagation()" 
                  value="${escapeHtml(cleanTrackWatermarks(t.label || 'Sub ' + (idx+1)))}" 
                  class="movie-track-label-input flex-1 bg-black border border-neutral-700 focus:border-red-500 rounded px-2 py-0.5 text-xs text-white" 
                  placeholder="Display Name"
                >
                <button
                  type="button"
                  data-idx="${idx}"
                  data-subid="${escapeHtml(t.id || idx)}"
                  onclick="deleteMovieSubtitle(this.dataset.idx, this.dataset.subid, this)"
                  class="px-2 py-1 rounded text-[10px] cursor-pointer transition border border-red-500/40 bg-red-600/20 text-red-300 hover:bg-red-600 hover:text-white flex items-center gap-1 flex-shrink-0"
                  title="Remove this subtitle track"
                >
                  🗑️
                </button>
              </div>
            `).join('')}
          </div>
        `;
      } else {
        subList.innerHTML = '<div class="text-[11px] text-neutral-500 italic py-1">No subtitles attached yet. Upload below.</div>';
      }
    }

    async function deleteMovieSubtitle(idx, subId, btn = null) {
      const mediaId = document.getElementById('edit-id')?.value;
      if (!mediaId) return;
      const ok = await showAdminConfirm({
        title: 'Remove Subtitle Track',
        message: 'Are you sure you want to remove this subtitle track from this movie?',
        confirmText: 'Remove Subtitle',
        isDanger: true
      });
      if (!ok) return;

      if (btn) setButtonProcessing(btn, true, '⏳');

      try {
        const res = await fetch(`/api/admin/streams/${mediaId}/subtitles/${subId}`, {
          method: 'DELETE'
        });
        if (res.ok) {
          showAdminToast({ type: 'success', title: 'Subtitle Removed', message: 'Subtitle deleted from stream.' });
          await loadMovieTracks(mediaId);
        } else {
          const err = await res.json().catch(() => ({}));
          showAdminToast({ type: 'error', title: 'Deletion Failed', message: err.detail || 'Could not delete subtitle' });
        }
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Network Error', message: e.message });
      } finally {
        if (btn) setButtonProcessing(btn, false);
      }
    }

    async function uploadMovieCustomSubtitle() {
      const mediaId = document.getElementById('edit-id')?.value;
      const fileInput = document.getElementById('movie-custom-sub-file');
      const langSelect = document.getElementById('movie-custom-sub-lang');
      const labelInput = document.getElementById('movie-custom-sub-label');
      if (!mediaId || !fileInput?.files?.[0]) {
        showAdminToast({ type: 'warning', title: 'File Required', message: 'Please select a subtitle file (.srt, .vtt, .ass) to upload.' });
        return;
      }
      const file = fileInput.files[0];
      const formData = new FormData();
      formData.append('file', file);
      formData.append('lang', langSelect.value);
      if (labelInput.value.trim()) {
        formData.append('label', labelInput.value.trim());
      }

      try {
        const res = await fetch(`/api/admin/streams/${mediaId}/subtitles/upload`, {
          method: 'POST',
          body: formData
        });
        if (res.ok) {
          showAdminToast({ type: 'success', title: 'Subtitle Uploaded', message: `Uploaded "${file.name}" to movie stream.` });
          fileInput.value = '';
          labelInput.value = '';
          await loadMovieTracks(mediaId);
        } else {
          const err = await res.json().catch(() => ({}));
          showAdminToast({ type: 'error', title: 'Upload Failed', message: err.detail || 'Could not upload subtitle' });
        }
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Network Error', message: e.message });
      }
    }

    async function deleteEpisodeSubtitle(epId, subId, btn = null) {
      if (!epId) return;
      const ok = await showAdminConfirm({
        title: 'Remove Episode Subtitle',
        message: 'Are you sure you want to remove this subtitle track from this episode?',
        confirmText: 'Remove Subtitle',
        isDanger: true
      });
      if (!ok) return;

      if (btn) setButtonProcessing(btn, true, '⏳');

      try {
        const res = await fetch(`/api/admin/streams/${epId}/subtitles/${subId}`, {
          method: 'DELETE'
        });
        if (res.ok) {
          showAdminToast({ type: 'success', title: 'Subtitle Removed', message: 'Episode subtitle deleted.' });
          const ep = currentModalSeriesEpisodes.find(e => e.id === epId);
          if (ep) {
            ep.subtitle_tracks = (ep.subtitle_tracks || []).filter(s => String(s.id) !== String(subId) && String(s.index) !== String(subId));
            renderEpisodeTracks(ep);
          }
        } else {
          const err = await res.json().catch(() => ({}));
          showAdminToast({ type: 'error', title: 'Deletion Failed', message: err.detail || 'Could not delete subtitle' });
        }
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Network Error', message: e.message });
      } finally {
        if (btn) setButtonProcessing(btn, false);
      }
    }

    async function uploadEpisodeCustomSubtitle(epId) {
      if (!epId) return;
      const fileInput = document.getElementById(`ep-custom-sub-file-${epId}`);
      const langSelect = document.getElementById(`ep-custom-sub-lang-${epId}`);
      const labelInput = document.getElementById(`ep-custom-sub-label-${epId}`);
      if (!fileInput?.files?.[0]) {
        showAdminToast({ type: 'warning', title: 'File Required', message: 'Please select a subtitle file (.srt, .vtt, .ass) to upload.' });
        return;
      }
      const file = fileInput.files[0];
      const formData = new FormData();
      formData.append('file', file);
      formData.append('lang', langSelect.value);
      if (labelInput.value.trim()) {
        formData.append('label', labelInput.value.trim());
      }

      try {
        const res = await fetch(`/api/admin/streams/${epId}/subtitles/upload`, {
          method: 'POST',
          body: formData
        });
        if (res.ok) {
          showAdminToast({ type: 'success', title: 'Subtitle Uploaded', message: `Uploaded "${file.name}" for episode.` });
          fileInput.value = '';
          labelInput.value = '';
          const ep = currentModalSeriesEpisodes.find(e => e.id === epId);
          if (ep) {
            fetch(`/api/admin/streams/${epId}/tracks`)
              .then(r => r.ok ? r.json() : null)
              .then(data => {
                if (data) {
                  ep.subtitle_tracks = data.subtitle_tracks || [];
                  renderEpisodeTracks(ep);
                }
              });
          }
        } else {
          const err = await res.json().catch(() => ({}));
          showAdminToast({ type: 'error', title: 'Upload Failed', message: err.detail || 'Could not upload subtitle' });
        }
      } catch (e) {
        showAdminToast({ type: 'error', title: 'Network Error', message: e.message });
      }
    }

    async function saveMovieTracks(silent = false) {
      const mediaId = document.getElementById('edit-id')?.value;
      if (!mediaId) return;

      const audioInputs = Array.from(document.querySelectorAll('.movie-track-label-input[data-type="audio"]'));
      const audioLangSelects = Array.from(document.querySelectorAll('.movie-audio-lang-select'));
      const subInputs = Array.from(document.querySelectorAll('.movie-track-label-input[data-type="sub"]'));
      const subLangSelects = Array.from(document.querySelectorAll('.movie-sub-lang-select'));

      const newAudioTracks = currentMovieStreamTracks.audio_tracks.map((t, idx) => {
        const inp = audioInputs.find(i => Number(i.dataset.idx) === idx);
        const sel = audioLangSelects.find(s => Number(s.dataset.idx) === idx);
        const btn = document.getElementById(`movie-audio-default-btn-${idx}`);
        const isDef = btn ? btn.getAttribute('data-default') === 'true' : Boolean(t.is_default);
        return {
          ...t,
          index: idx,
          lang: sel ? sel.value : (t.lang || 'en'),
          label: cleanTrackWatermarks(inp ? inp.value.trim() : (t.label || 'Audio')),
          is_default: isDef
        };
      });
      if (newAudioTracks.length > 0 && !newAudioTracks.some(t => t.is_default)) {
        newAudioTracks[0].is_default = true;
      }

      const newSubTracks = currentMovieStreamTracks.subtitle_tracks.map((t, idx) => {
        const inp = subInputs.find(i => Number(i.dataset.idx) === idx);
        const sel = subLangSelects.find(s => Number(s.dataset.idx) === idx);
        return {
          ...t,
          index: idx,
          lang: sel ? sel.value : (t.lang || 'en'),
          label: cleanTrackWatermarks(inp ? inp.value.trim() : (t.label || 'Subtitle'))
        };
      });

      const btn = document.getElementById('btn-save-movie-tracks');
      if (btn) btn.disabled = true;

      try {
        const res = await fetch(`/api/admin/streams/${mediaId}/tracks`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            audio_tracks: newAudioTracks,
            subtitle_tracks: newSubTracks
          })
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || 'Failed to update track labels');
        }
        const updated = await res.json();
        currentMovieStreamTracks = {
          audio_tracks: updated.audio_tracks || newAudioTracks,
          subtitle_tracks: updated.subtitle_tracks || newSubTracks
        };
        if (!silent) {
          showAdminToast('✓ Successfully updated movie audio & subtitle track display names!', 'success');
        }
      } catch (err) {
        if (!silent) {
          showAdminToast('Failed to save movie tracks: ' + err.message, 'error');
        }
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    function editMedia(id) {
      const item = catalog.find(m => m.id === id);
      if (item) openMediaModal(item);
    }

    let pendingDeleteMediaId = null;
    let pendingDeleteSeriesEpisodes = [];

    async function openDeleteModal(id) {
      const item = catalog.find(m => m.id === id);
      if (!item) return;
      pendingDeleteMediaId = id;

      const modal = document.getElementById('delete-modal');
      const movieView = document.getElementById('delete-movie-view');
      const seriesView = document.getElementById('delete-series-view');

      if (item.type === 'movie') {
        document.getElementById('delete-modal-heading').innerText = 'Permanently Delete Movie';
        document.getElementById('delete-movie-title').innerText = item.title;
        movieView.classList.remove('hidden');
        seriesView.classList.add('hidden');
      } else {
        document.getElementById('delete-modal-heading').innerText = 'Delete Series & Episodes';
        document.getElementById('delete-series-title').innerText = item.title;
        movieView.classList.add('hidden');
        seriesView.classList.remove('hidden');

        // Fetch episodes for dropdown
        const sel = document.getElementById('delete-series-episode-select');
        sel.innerHTML = '<option value="">Loading episodes...</option>';
        try {
          const epData = await fetch(`/api/admin/series/${id}/episodes`).then(r => r.json());
          pendingDeleteSeriesEpisodes = epData.episodes || [];
          if (pendingDeleteSeriesEpisodes.length === 0) {
            sel.innerHTML = '<option value="">No uploaded episodes found</option>';
          } else {
            sel.innerHTML = pendingDeleteSeriesEpisodes.map(ep => `
              <option value="${escapeHtml(ep.id)}">S${ep.season_number}:E${ep.episode_number} — ${escapeHtml(ep.title || 'Episode ' + ep.episode_number)}</option>
            `).join('');
          }
        } catch (e) {
          sel.innerHTML = '<option value="">Failed to load episodes</option>';
        }
      }

      modal.classList.remove('hidden');
      modal.classList.add('flex');
      updateModalOpenClass();
    }

    function closeDeleteModal() {
      const modal = document.getElementById('delete-modal');
      if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
      }
      pendingDeleteMediaId = null;
      pendingDeleteSeriesEpisodes = [];
      updateModalOpenClass();
    }

    async function confirmDeleteMovie() {
      if (!pendingDeleteMediaId) return;
      const btn = document.getElementById('btn-confirm-delete-movie');
      const cancelBtn = document.getElementById('btn-cancel-delete-movie');
      if (cancelBtn) { cancelBtn.disabled = true; cancelBtn.classList.add('opacity-50', 'pointer-events-none'); }
      setButtonProcessing(btn, true, '⏳ Deleting...');

      try {
        const res = await fetch(`/api/catalog/${pendingDeleteMediaId}`, { method: 'DELETE' }).then(r => r.json());
        closeDeleteModal();
        showAdminToast({
          type: 'success',
          title: 'Movie Permanently Deleted',
          message: `Freed ${res.freed_mb || 0} MB of HLS files from /media/library/ and cleared database records.`
        });
        await loadCatalog();
        await fetchIncomingFiles();
      } catch (err) {
        showAdminToast({ type: 'error', title: 'Deletion Failed', message: err.message });
      } finally {
        if (cancelBtn) { cancelBtn.disabled = false; cancelBtn.classList.remove('opacity-50', 'pointer-events-none'); }
        setButtonProcessing(btn, false);
      }
    }

    async function confirmDeleteEntireSeries() {
      if (!pendingDeleteMediaId) return;
      const btn = document.getElementById('btn-confirm-delete-series');
      const closeBtn = document.getElementById('btn-close-delete-modal-series');
      if (closeBtn) { closeBtn.disabled = true; closeBtn.classList.add('opacity-50', 'pointer-events-none'); }
      setButtonProcessing(btn, true, '⏳ Deleting...');

      try {
        const res = await fetch(`/api/catalog/${pendingDeleteMediaId}`, { method: 'DELETE' }).then(r => r.json());
        closeDeleteModal();
        showAdminToast({
          type: 'success',
          title: 'Entire Series Deleted',
          message: `Deleted series and ${res.episodes_count || 0} episode(s), freeing ${res.freed_mb || 0} MB.`
        });
        await loadCatalog();
        await fetchIncomingFiles();
      } catch (err) {
        showAdminToast({ type: 'error', title: 'Deletion Failed', message: err.message });
      } finally {
        if (closeBtn) { closeBtn.disabled = false; closeBtn.classList.remove('opacity-50', 'pointer-events-none'); }
        setButtonProcessing(btn, false);
      }
    }

    async function confirmDeleteEpisodeFromSeriesModal() {
      const sel = document.getElementById('delete-series-episode-select');
      const epId = sel ? sel.value : null;
      if (!epId) {
        showAdminToast({ type: 'warning', title: 'Selection Required', message: 'Please select an episode to delete.' });
        return;
      }

      const btn = document.getElementById('btn-confirm-delete-series-ep');
      const closeBtn = document.getElementById('btn-close-delete-modal-series');
      if (closeBtn) { closeBtn.disabled = true; closeBtn.classList.add('opacity-50', 'pointer-events-none'); }
      setButtonProcessing(btn, true, '⏳ Deleting...');

      try {
        const res = await fetch(`/api/admin/episodes/${epId}`, { method: 'DELETE' }).then(r => r.json());
        if (res.parent_series_deleted) {
          closeDeleteModal();
          showAdminToast({
            type: 'warning',
            title: 'Series Removed',
            message: `Last episode was deleted. The parent series shell was also automatically cleaned up.`
          });
        } else {
          showAdminToast({
            type: 'success',
            title: 'Episode Deleted',
            message: `Freed ${res.freed_mb || 0} MB from disk and removed episode record.`
          });
          // Refresh episode list in modal
          if (pendingDeleteMediaId) {
            openDeleteModal(pendingDeleteMediaId);
          }
        }
        await loadCatalog();
        await fetchIncomingFiles();
      } catch (err) {
        showAdminToast({ type: 'error', title: 'Episode Deletion Failed', message: err.message });
      } finally {
        if (closeBtn) { closeBtn.disabled = false; closeBtn.classList.remove('opacity-50', 'pointer-events-none'); }
        setButtonProcessing(btn, false);
      }
    }

    async function deleteSelectedEpisodeFromModal() {
      const epId = document.getElementById('ep-edit-id')?.value;
      if (!epId) return;
      const ok = await showAdminConfirm({
        title: 'Delete Episode',
        message: 'Permanently delete this episode and wipe all its HLS video files from /media/library/?',
        confirmText: 'Delete Episode',
        isDanger: true
      });
      if (!ok) return;

      const btn = document.getElementById('delete-episode-btn');
      setButtonProcessing(btn, true, '⏳ Deleting...');

      try {
        const res = await fetch(`/api/admin/episodes/${epId}`, { method: 'DELETE' }).then(r => r.json());
        if (res.parent_series_deleted) {
          closeMediaModal();
          showAdminToast({
            type: 'warning',
            title: 'Series Removed',
            message: `0 episodes remain. Parent series was also removed from the library.`
          });
        } else {
          showAdminToast({
            type: 'success',
            title: 'Episode Deleted',
            message: `Freed ${res.freed_mb || 0} MB from /media/library/.`
          });
          // Re-load episodes for the current open media
          const mediaId = document.getElementById('edit-id')?.value;
          if (mediaId) {
            loadSeriesEpisodes(mediaId);
          }
        }
        await loadCatalog();
        await fetchIncomingFiles();
      } catch (err) {
        showAdminToast({ type: 'error', title: 'Episode Deletion Failed', message: err.message });
      } finally {
        setButtonProcessing(btn, false);
      }
    }

    async function deleteMedia(id, title) {
      openDeleteModal(id);
    }

    async function saveMedia(e) {
      e.preventDefault();
      const id = document.getElementById('edit-id').value;
      if (!id) {
        showAdminToast({ type: 'warning', title: 'Action Disabled', message: 'Manual creation of titles is disabled. All titles must originate from video files.' });
        return;
      }
      const item = catalog.find(m => m.id === id);
      if (!item) {
        showAdminToast({ type: 'error', title: 'Not Found', message: 'Title not found in catalog.' });
        return;
      }

      const trailerRaw = document.getElementById('edit-trailer').value.trim();
      const posterRaw = document.getElementById('edit-poster').value.trim();
      const backdropRaw = document.getElementById('edit-backdrop').value.trim();
      const synopsisRaw = document.getElementById('edit-synopsis').value.trim();

      // Promotional Badge & Expiration
      const presetVal = document.getElementById('edit-badge-preset')?.value || 'none';
      let badge = null;
      if (presetVal === 'custom') {
        badge = document.getElementById('edit-badge-custom')?.value.trim() || null;
      } else if (presetVal !== 'none') {
        badge = presetVal;
      }

      const timerVal = document.getElementById('edit-badge-timer')?.value || '10';
      let durationDays = 10;
      if (timerVal === 'custom') {
        durationDays = parseInt(document.getElementById('edit-badge-custom-days')?.value) || 10;
      } else {
        durationDays = parseInt(timerVal);
      }

      const isBillboard = Boolean(document.getElementById('edit-billboard-toggle')?.checked);
      const isVisible = Boolean(document.getElementById('edit-visibility-toggle')?.checked);

      // Title, type, and release_year are strictly locked to ingested source values
      const payload = {
        title: item.title,
        type: item.type,
        release_year: item.release_year,
        synopsis: synopsisRaw || '',
        trailer_youtube_key: trailerRaw ? trailerRaw : null,
        maturity_rating: document.getElementById('edit-rating').value.trim(),
        runtime_minutes: Number(document.getElementById('edit-runtime').value),
        genres: Array.from(selectedGenres),
        audio_languages: Array.from(selectedAudio).length > 0 ? Array.from(selectedAudio) : ['English'],
        poster_url: posterRaw ? posterRaw : null,
        backdrop_url: backdropRaw ? backdropRaw : null,
        badge: badge,
        badge_duration_days: durationDays,
        is_published: isVisible,
        is_featured_billboard: isBillboard,
        billboard_priority: isBillboard ? 100 : 0
      };

      try {
        const res = await fetch(`/api/catalog/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          showAdminToast({ type: 'error', title: 'Update Failed', message: err.detail || 'Could not update record' });
          return;
        }

        // If billboard state changed, sync exclusive billboard endpoint
        if (isBillboard !== Boolean(item.is_featured_billboard)) {
          await fetch(`/api/catalog/${id}/billboard`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_featured_billboard: isBillboard, exclusive: isBillboard, billboard_priority: isBillboard ? 100 : 0 })
          });
        }

        if (item.type !== 'series') {
          await saveMovieTracks(true);
        }
        closeMediaModal();
        await loadCatalog();
        populateBillboardSelector();
      } catch (err) {
        showAdminToast({ type: 'error', title: 'Network Error', message: err.message });
      }
    }

"""
