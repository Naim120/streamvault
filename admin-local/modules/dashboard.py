import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Request, HTTPException
import httpx
from .common import db, settings, MEDIA_SERVER_URL, ADMIN_SECRET

router = APIRouter()

@router.get("/api/admin/dashboard-summary")
async def get_dashboard_summary_proxy():
    """Proxy route to Media Server for consolidated dashboard summary with X-Admin-Secret, with fallback to local telemetry"""
    url = f"{MEDIA_SERVER_URL}/api/system/dashboard-summary"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code == 200:
                return resp.json()
            logging.getLogger("admin-proxy").warning(f"Media server returned {resp.status_code}: {resp.text}")
    except Exception as e:
        logging.getLogger("admin-proxy").warning(f"Media server at {MEDIA_SERVER_URL} unavailable ({e}), returning local telemetry fallback...")

    # Resilient fallback: return direct host metrics and settings so dashboard UI functions seamlessly
    from backend.app.hardware import get_system_capabilities
    import psutil
    caps = get_system_capabilities()
    try:
        vm = psutil.virtual_memory()
        ram_used_mb = round((vm.total - vm.available) / (1024 * 1024), 1)
        ram_total_mb = round(vm.total / (1024 * 1024), 1)
    except Exception:
        ram_used_mb = 0.0
        ram_total_mb = 0.0

    return {
        "hardware": {
            "cpu_percent": caps.get("cpu_percent", 0.0),
            "ram_used_mb": ram_used_mb,
            "ram_total_mb": ram_total_mb,
            "ram_percent": caps.get("ram_percent", 0.0),
            "free_disk_gb": caps.get("free_disk_gb", 0.0),
            "total_disk_gb": caps.get("total_disk_gb", 0.0),
            "disk_percent": caps.get("disk_percent", 0.0),
            "preferred_encoder": caps.get("preferred_encoder", "libx264")
        },
        "transcoder": {
            "active_job": None,
            "queue": [],
            "history": [],
            "queue_length": 0
        },
        "settings": {
            "transcoding_enabled": False,
            "cpu_thread_limit": "auto",
            "allocated_cpu_cores": 1,
            "allocated_ram_mb": 2048,
            "gpu_enabled": False
        },
        "downloads": {"active": [], "count": 0, "total_download_speed": "0 B/s"},
        "platform_kpis": {
            "total_media": 0,
            "total_movies": 0,
            "total_series": 0,
            "total_episodes": 0,
            "ready_media": 0,
            "processing_media": 0,
            "queued_media": 0,
            "failed_media": 0,
            "total_storage_bytes": 0,
            "total_storage_formatted": "0.00 GB",
            "total_registered_users": 0,
            "total_logins_all_time": 0,
            "active_users_24h": 0
        }
    }


@router.get("/api/metrics")
async def get_metrics():
    try:
        users = await db.select("profiles", {"select": "id,email,display_name,last_seen_at"})
        # Query public.user_login_logs using count="exact" (via select_with_count)
        logs, total_logins = await db.select_with_count("user_login_logs", {"order": "logged_in_at.desc", "limit": "50"})
        media = await db.select("media_items", {"select": "id"})

        email_map = {u["id"]: u.get("email") for u in (users or []) if "id" in u}

        # Calculate active unique users in the last 24 hours
        now = datetime.now(timezone.utc)
        one_day_ago = now - timedelta(hours=24)
        active_today_set = set()

        # Query all logs active in the last 24 hours
        recent_logs = await db.select("user_login_logs", {
            "logged_in_at": f"gte.{one_day_ago.isoformat()}",
            "select": "user_id"
        })
        for r in (recent_logs or []):
            if r.get("user_id"):
                active_today_set.add(r["user_id"])

        recent_heartbeats = await db.select("user_login_logs", {
            "last_heartbeat_at": f"gte.{one_day_ago.isoformat()}",
            "select": "user_id"
        })
        for r in (recent_heartbeats or []):
            if r.get("user_id"):
                active_today_set.add(r["user_id"])

        for u in (users or []):
            if u.get("id") and u.get("last_seen_at"):
                try:
                    dt = datetime.fromisoformat(u["last_seen_at"].replace("Z", "+00:00"))
                    if dt >= one_day_ago:
                        active_today_set.add(u["id"])
                except Exception:
                    pass

        active_count = len(active_today_set) if active_today_set else (1 if users else 0)

        enriched_logs = []
        for l in (logs or []):
            uid = l.get("user_id")
            email = email_map.get(uid) or uid or "Anonymous User"
            l_copy = dict(l)
            l_copy["email"] = email
            enriched_logs.append(l_copy)

        return {
            "total_users": len(users) if users else 0,
            "total_visits": total_logins,
            "active_today": active_count,
            "total_media": len(media) if media else 0,
            "logs": enriched_logs
        }
    except Exception as e:
        logging.getLogger("admin-server").error(f"Error computing metrics: {e}")
        return {"total_users": 0, "total_visits": 0, "active_today": 0, "total_media": 0, "logs": []}


@router.get("/api/system/capabilities")
async def get_system_capabilities_endpoint():
    from backend.app.hardware import get_system_capabilities
    return get_system_capabilities()


@router.get("/api/system/settings")
async def get_system_settings_endpoint():
    try:
        headers = {}
        if ADMIN_SECRET:
            headers["X-Admin-Secret"] = ADMIN_SECRET
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{MEDIA_SERVER_URL}/api/system/settings", headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass

    try:
        from backend.app.hardware import get_system_capabilities
        caps = get_system_capabilities()
        total_cores = int(caps.get("cpu_cores") or 4)
        has_gpu = bool(caps.get("has_nvenc") or caps.get("has_vaapi") or caps.get("has_qsv"))

        settings_rows = await db.select("system_settings", {"id": "eq.1"})
        res = dict(settings_rows[0]) if settings_rows else {
            "id": 1,
            "transcoding_enabled": False,
            "max_transcode_threads": 4,
            "hw_accel_type": "auto",
            "cpu_thread_limit": "auto",
            "allocated_cpu_cores": max(1, total_cores // 2),
            "allocated_ram_mb": 2048,
            "gpu_enabled": False
        }
        res["transcode_mode"] = "abr_transcode" if res.get("transcoding_enabled") else "lossless_remux"
        res["max_concurrent_transcodes"] = res.get("max_transcode_threads", 4)
        if "allocated_cpu_cores" not in res or res["allocated_cpu_cores"] is None:
            res["allocated_cpu_cores"] = max(1, total_cores // 2)
        if "allocated_ram_mb" not in res or res["allocated_ram_mb"] is None:
            res["allocated_ram_mb"] = 2048
        if "gpu_enabled" not in res or res["gpu_enabled"] is None:
            res["gpu_enabled"] = has_gpu
        elif not has_gpu:
            res["gpu_enabled"] = False
        res["has_gpu"] = has_gpu
        res["gpu_active"] = bool(has_gpu and res.get("gpu_enabled", False))
        return res
    except Exception:
        return {
            "id": 1,
            "transcoding_enabled": False,
            "max_transcode_threads": 4,
            "transcode_mode": "lossless_remux",
            "max_concurrent_transcodes": 4,
            "cpu_thread_limit": "auto",
            "allocated_cpu_cores": 2,
            "allocated_ram_mb": 2048,
            "gpu_enabled": False,
            "has_gpu": False,
            "gpu_active": False
        }


@router.post("/api/system/settings")
async def update_system_settings_proxy(request: Request):
    body = await request.json()
    try:
        headers = {}
        if ADMIN_SECRET:
            headers["X-Admin-Secret"] = ADMIN_SECRET
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{MEDIA_SERVER_URL}/api/system/settings", json=body, headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    # Fallback to direct DB update
    await db.update("system_settings", "id", 1, body)
    return {"message": "System settings updated successfully", "settings": body}


@router.get("/api/system/status")
async def get_system_status_endpoint():
    # Attempt to query backend worker status if running
    try:
        headers = {}
        if ADMIN_SECRET:
            headers["X-Admin-Secret"] = ADMIN_SECRET
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{MEDIA_SERVER_URL}/api/system/status", headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass

    from backend.app.hardware import get_system_capabilities
    caps = get_system_capabilities()
    return {
        "queue_length": 0,
        "is_busy": False,
        "active_job": None,
        "system": {
            "cpu_percent": caps.get("cpu_percent", 0.0),
            "cpu_cores": caps.get("cpu_cores", 1),
            "available_ram_gb": caps.get("available_ram_gb", 0.0),
            "total_ram_gb": caps.get("total_ram_gb", 0.0),
            "ram_percent": caps.get("ram_percent", 0.0),
            "free_disk_gb": caps.get("free_disk_gb", 0.0),
            "total_disk_gb": caps.get("total_disk_gb", 0.0),
            "disk_percent": caps.get("disk_percent", 0.0),
            "preferred_encoder": caps.get("preferred_encoder", "libx264"),
            "ffmpeg_available": caps.get("ffmpeg_available", False),
            "is_low_spec": caps.get("is_low_spec", False),
        }
    }




DASHBOARD_HTML = r"""      <section id="tab-dashboard" class="space-y-6">
        <!-- 4 Packed Metrics Cards -->
        <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 sm:gap-4 w-full">
          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5">
            <span class="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">Registered Accounts</span>
            <div id="metric-users" class="text-2xl sm:text-3xl font-black text-white mt-1">--</div>
            <span class="text-[10px] text-neutral-500">Verified Supabase Members</span>
          </div>

          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5">
            <span class="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">User Visits &amp; Sessions</span>
            <div id="metric-visits" class="text-2xl sm:text-3xl font-black text-emerald-400 mt-1">--</div>
            <span class="text-[10px] text-neutral-500">Logged Security Audits</span>
          </div>

          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5">
            <span class="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">Active Today</span>
            <div id="metric-active" class="text-2xl sm:text-3xl font-black text-cyan-400 mt-1">--</div>
            <span class="text-[10px] text-neutral-500">Authenticated Devices</span>
          </div>

          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 sm:p-5">
            <span class="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">Catalog Titles</span>
            <div id="metric-media" class="text-2xl sm:text-3xl font-black text-red-500 mt-1">--</div>
            <span class="text-[10px] text-neutral-500">Active Movies &amp; Series</span>
          </div>
        </div>

        <!-- Transcoding Engine Governor -->
        <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-6 space-y-5">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-neutral-800/80">
            <div>
              <h3 class="text-base font-bold text-white flex items-center gap-2">
                <span>Transcoding Engine Governor</span>
                <span id="transcode-badge" class="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-purple-500/20 text-purple-400 border border-purple-500/30">
                  Detecting...
                </span>
              </h3>
              <p class="text-xs text-neutral-400 mt-1" id="transcode-desc">
                Hardware-agnostic pipeline with zero-CPU stream copy, CPU-safe HEVC conversion, and optional GPU offload.
              </p>
            </div>
            <div class="flex flex-wrap items-center gap-3">
              <!-- CPU Thread Limiter Selector -->
              <div class="flex flex-col">
                <label class="text-[10px] font-bold text-neutral-400 uppercase tracking-wider mb-1">CPU Thread Limiter</label>
                <select id="cpu-thread-limiter" onchange="updateCpuThreadLimit(this.value)" class="bg-[#1c1c1c] border border-neutral-700 hover:border-neutral-500 rounded-xl px-3 py-1.5 text-xs text-white focus:outline-none focus:border-red-500 transition cursor-pointer">
                  <option value="1">1 Thread — Ultra Low Spec (~20% CPU)</option>
                  <option value="2">2 Threads — Balanced (~40% CPU)</option>
                  <option value="auto">Auto — Fast</option>
                </select>
              </div>
              <div class="flex flex-col">
                <label class="text-[10px] font-bold text-neutral-400 uppercase tracking-wider mb-1">Engine Mode</label>
                <button onclick="toggleTranscoding()" id="transcode-btn" class="px-4 py-1.5 rounded-xl text-xs font-bold transition bg-red-600 hover:bg-red-700 text-white cursor-pointer shadow-lg shadow-red-600/20">
                  Toggle Mode
                </button>
              </div>
            </div>
          </div>

          <!-- Active Pipeline Processing Strategies -->
          <div class="space-y-2">
            <span class="text-[11px] font-bold text-neutral-400 uppercase tracking-wider">Active Ingest Strategies:</span>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
              <div class="bg-[#181818] p-3 rounded-xl border border-neutral-800 flex items-start gap-2.5">
                <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 mt-0.5 flex-shrink-0"></span>
                <div>
                  <div class="font-bold text-white">Direct Stream Copy (0% Video CPU)</div>
                  <div class="text-[11px] text-neutral-400 mt-0.5">Strict <code class="font-mono text-emerald-400">-c:v copy</code> for 8-bit H.264 files (~10s packaging at disk speed).</div>
                </div>
              </div>
              <div class="bg-[#181818] p-3 rounded-xl border border-neutral-800 flex items-start gap-2.5">
                <span class="w-2.5 h-2.5 rounded-full bg-amber-500 mt-0.5 flex-shrink-0"></span>
                <div>
                  <div class="font-bold text-white">CPU-Safe Compatibility Convert</div>
                  <div class="text-[11px] text-neutral-400 mt-0.5">Capped ≤ 2 Threads / ~40% CPU for 10-bit HEVC, AV1, VP9 on CPU servers.</div>
                </div>
              </div>
              <div id="gpu-mode-card" class="bg-[#181818] p-3 rounded-xl border border-neutral-800 flex items-start gap-2.5">
                <span id="gpu-dot" class="w-2.5 h-2.5 rounded-full bg-purple-500 mt-0.5 flex-shrink-0"></span>
                <div>
                  <div id="gpu-title" class="font-bold text-white">GPU Accelerated Convert</div>
                  <div id="gpu-subtitle" class="text-[11px] text-neutral-400 mt-0.5">Optional GPU acceleration with Automatic Fallback Guarantee to CPU-Safe mode.</div>
                </div>
              </div>
            </div>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
            <div class="bg-[#181818] p-3 rounded-xl border border-neutral-800">
              <span class="text-neutral-500">Worker Status:</span>
              <p id="worker-status" class="text-neutral-200 font-semibold mt-0.5">Detecting...</p>
            </div>
            <div class="bg-[#181818] p-3 rounded-xl border border-neutral-800">
              <span class="text-neutral-500">Sequential Concurrency:</span>
              <p id="worker-concurrency" class="text-neutral-200 font-semibold mt-0.5">Detecting...</p>
            </div>
            <div class="bg-[#181818] p-3 rounded-xl border border-neutral-800">
              <span class="text-neutral-500">CPU Architecture &amp; Priority:</span>
              <p id="worker-priority" class="text-neutral-200 font-semibold mt-0.5">Detecting...</p>
            </div>
          </div>
        </div>
      </section>
"""

DASHBOARD_JS = r"""    function renderSystem(caps, sett, stat, serverConnected = true) {
      // 1. Sidebar Connection Indicator
      const connInd = document.getElementById('connection-indicator');
      const connStatus = document.getElementById('connection-status');
      if (connInd && connStatus) {
        if (serverConnected) {
          connInd.className = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse';
          connStatus.innerText = 'Production Server Connected';
        } else {
          connInd.className = 'w-2 h-2 rounded-full bg-red-500';
          connStatus.innerText = 'Server Offline / Retrying';
        }
      }

      // 2. Top Bar Telemetry Chips
      if (caps) {
        const cpuPct = (caps.cpu_percent !== undefined) ? caps.cpu_percent : (caps.cpu?.usage_percent ?? 0);
        const cores = caps.cpu_cores || 1;
        const ramAvail = (caps.available_ram_gb !== undefined) ? caps.available_ram_gb : (caps.ram?.available_gb ?? 0);
        const ramTotal = caps.total_ram_gb || 0;
        const diskFree = (caps.free_disk_gb !== undefined) ? caps.free_disk_gb : (caps.disk?.free_gb ?? 0);
        const diskTotal = caps.total_disk_gb || 0;
        const encoder = (caps.preferred_encoder || caps.ffmpeg?.preferred_encoder || 'LIBX264').toUpperCase();

        const cpuEl = document.getElementById('top-cpu');
        if (cpuEl) {
          cpuEl.innerText = `${cpuPct.toFixed(1)}% (${cores} cores)`;
          cpuEl.className = cpuPct > 85 ? 'font-bold text-red-400' : (cpuPct > 65 ? 'font-bold text-amber-400' : 'font-bold text-white');
        }

        const ramEl = document.getElementById('top-ram');
        if (ramEl) {
          ramEl.innerText = `${ramAvail.toFixed(1)} GB free${ramTotal ? ' / ' + ramTotal.toFixed(1) + ' GB' : ''}`;
          ramEl.className = ramAvail < 1.0 ? 'font-bold text-red-400' : (ramAvail < 2.0 ? 'font-bold text-amber-400' : 'font-bold text-emerald-400');
        }

        const diskEl = document.getElementById('top-disk');
        if (diskEl) {
          diskEl.innerText = `${diskFree.toFixed(0)} GB free${diskTotal ? ' / ' + diskTotal.toFixed(0) + ' GB' : ''}`;
        }

        const encEl = document.getElementById('top-encoder');
        if (encEl) {
          encEl.innerText = encoder;
          if (encoder.includes('NVENC')) {
            encEl.className = 'bg-purple-500/10 border border-purple-500/30 px-2.5 py-1 rounded-lg text-purple-400 font-mono font-bold uppercase text-[10px]';
          } else if (encoder.includes('VAAPI') || encoder.includes('QSV')) {
            encEl.className = 'bg-cyan-500/10 border border-cyan-500/30 px-2.5 py-1 rounded-lg text-cyan-400 font-mono font-bold uppercase text-[10px]';
          } else {
            encEl.className = 'bg-neutral-800 border border-neutral-700 px-2.5 py-1 rounded-lg text-neutral-300 font-mono font-bold uppercase text-[10px]';
          }
        }
      }

      // 3. Transcoding Engine Governor Mode
      if (sett) {
        const isAbr = Boolean(sett.transcoding_enabled || sett.transcode_mode === 'abr_transcode');
        currentMode = sett.transcode_mode || (isAbr ? 'abr_transcode' : 'lossless_remux');
        const badge = document.getElementById('transcode-badge');
        const btn = document.getElementById('transcode-btn');
        const desc = document.getElementById('transcode-desc');
        const limiter = document.getElementById('cpu-thread-limiter');

        if (limiter && sett.cpu_thread_limit) {
          limiter.value = sett.cpu_thread_limit;
        }

        if (currentMode === 'abr_transcode') {
          badge.innerText = 'Multi-Bitrate ABR';
          badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-purple-500/20 text-purple-400 border border-purple-500/30';
          btn.innerText = 'Switch to Lossless Remux';
          desc.innerText = 'Active: Transcodes videos into 1080p, 720p, 480p adaptive ladders with hardware acceleration.';
        } else {
          badge.innerText = 'Lossless Remux (Mode B)';
          badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
          btn.innerText = 'Enable ABR Transcoding';
          desc.innerText = 'Active: Mode B Hardware-Agnostic Remuxing (Zero-CPU copy for H.264, CPU-safe capped libx264 conversion, stereo AAC audio).';
        }
      }

      // Check GPU availability for GPU mode card
      const gpuCard = document.getElementById('gpu-mode-card');
      if (gpuCard) {
        const hasGpu = Boolean(caps && (caps.has_nvenc || caps.has_vaapi || caps.has_qsv));
        const gpuActive = Boolean(hasGpu && sett && sett.hw_accel_type !== 'none');
        gpuCard.style.display = gpuActive ? 'flex' : 'none';
      }

      // 4. Update Right Sidebar Hardware Allocator controls
      if (sett) {
        const isAbr = Boolean(sett.transcoding_enabled || sett.transcode_mode === 'abr_transcode');
        const modeRemuxBtn = document.getElementById('side-mode-remux');
        const modeAbrBtn = document.getElementById('side-mode-abr');
        const modeDesc = document.getElementById('side-mode-desc');
        if (modeRemuxBtn && modeAbrBtn) {
          if (isAbr) {
            modeRemuxBtn.className = 'py-1.5 px-2 rounded-lg text-[11px] font-semibold text-neutral-400 hover:text-white transition flex items-center justify-center gap-1 cursor-pointer';
            modeAbrBtn.className = 'py-1.5 px-2 rounded-lg text-[11px] font-bold transition flex items-center justify-center gap-1 cursor-pointer bg-red-600 text-white shadow';
            if (modeDesc) modeDesc.innerText = 'Mode A: Multi-Bitrate ABR ladders (1080p, 720p, 480p) with 1080p stream reuse.';
          } else {
            modeRemuxBtn.className = 'py-1.5 px-2 rounded-lg text-[11px] font-bold transition flex items-center justify-center gap-1 cursor-pointer bg-emerald-600 text-white shadow';
            modeAbrBtn.className = 'py-1.5 px-2 rounded-lg text-[11px] font-semibold text-neutral-400 hover:text-white transition flex items-center justify-center gap-1 cursor-pointer';
            if (modeDesc) modeDesc.innerText = 'Mode B: Zero-CPU stream copy for 8-bit H.264 (~10s packaging at disk speed).';
          }
        }

        // CPU Slider
        const totalCores = caps?.cpu_cores || 8;
        const slider = document.getElementById('side-cpu-slider');
        const maxLabel = document.getElementById('side-cpu-max-label');
        if (slider) {
          slider.max = totalCores;
          const curVal = sett.allocated_cpu_cores || Math.max(1, Math.floor(totalCores / 2));
          slider.value = curVal;
          onCpuSliderInput(curVal);
        }
        if (maxLabel) maxLabel.innerText = `${totalCores} Cores`;

        // RAM Buffer Limit selector
        const ramSelect = document.getElementById('side-ram-select');
        if (ramSelect && sett.allocated_ram_mb) {
          ramSelect.value = String(sett.allocated_ram_mb);
        }

        // GPU Toggle & Installation Assistant
        const gpuToggle = document.getElementById('side-gpu-toggle');
        const gpuBadge = document.getElementById('side-gpu-badge');
        const gpuNotice = document.getElementById('side-gpu-notice-box');
        const hasGpu = Boolean(caps && (caps.has_nvenc || caps.has_vaapi || caps.has_qsv || caps.has_videotoolbox || caps.has_amf));
        const gpuHw = caps?.gpu_hardware;

        if (gpuToggle) {
          if (!hasGpu) {
            gpuToggle.disabled = true;
            gpuToggle.checked = false;

            if (gpuHw && gpuHw.detected) {
              // Physical GPU exists on host, but hardware encoder driver not functional
              if (gpuBadge) {
                gpuBadge.innerText = `${gpuHw.name || 'Discrete GPU'} (Driver Needed)`;
                gpuBadge.className = 'px-1.5 py-0.2 rounded text-[9px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse';
              }
              if (gpuNotice) {
                gpuNotice.classList.remove('hidden');
                const titleEl = document.getElementById('side-gpu-notice-title');
                const hostEl = document.getElementById('side-gpu-os-host-badge');
                const descEl = document.getElementById('side-gpu-notice-desc');

                if (titleEl) titleEl.innerText = `${gpuHw.name || 'Discrete GPU'} Detected`;
                if (hostEl) hostEl.innerText = `Host OS: ${caps?.os_display_name || 'Linux'}`;
                if (descEl) {
                  const driverStr = gpuHw.driver_in_use || 'open-source driver';
                  descEl.innerHTML = `Hardware is present (driver: <code class="text-amber-300 font-mono">${driverStr}</code>). NVENC hardware encoding requires the proprietary vendor driver (<code class="text-amber-300 font-mono">libcuda.so.1</code>).`;
                }

                // Auto-select tab matching host OS (linux, windows, macos)
                if (!window._userSelectedGpuTab) {
                  const hostOs = (caps?.os_platform || 'linux').toLowerCase();
                  selectGpuGuideOs(hostOs === 'darwin' ? 'macos' : hostOs);
                }
              }
            } else {
              // No physical GPU detected on host
              if (gpuBadge) {
                gpuBadge.innerText = 'Not Available on Host';
                gpuBadge.className = 'px-1.5 py-0.2 rounded text-[9px] font-mono font-bold bg-neutral-800 text-neutral-500 border border-neutral-700';
              }
              if (gpuNotice) gpuNotice.classList.add('hidden');
            }
          } else {
            // Functional GPU encoder detected
            gpuToggle.disabled = false;
            gpuToggle.checked = Boolean(sett.gpu_enabled);
            if (gpuBadge) {
              const encName = caps?.preferred_encoder === 'h264_videotoolbox' ? 'Apple VideoToolbox' : (caps?.preferred_encoder === 'h264_nvenc' ? 'NVIDIA NVENC' : (caps?.preferred_encoder === 'h264_qsv' ? 'Intel QuickSync' : 'Hardware VAAPI'));
              gpuBadge.innerText = sett.gpu_enabled ? `${encName} (Active)` : 'Hardware Available (OFF)';
              gpuBadge.className = sett.gpu_enabled 
                ? 'px-1.5 py-0.2 rounded text-[9px] font-mono font-bold bg-purple-500/20 text-purple-400 border border-purple-500/40'
                : 'px-1.5 py-0.2 rounded text-[9px] font-mono font-bold bg-neutral-800 text-neutral-400 border border-neutral-700';
            }
            if (gpuNotice) gpuNotice.classList.add('hidden');
          }
        }
      }

      // 5. Live Worker Status Cards
      const workerStatusEl = document.getElementById('worker-status');
      const workerConcurrencyEl = document.getElementById('worker-concurrency');
      const workerPriorityEl = document.getElementById('worker-priority');

      if (workerStatusEl) {
        if (stat && stat.is_busy && stat.active_job) {
          workerStatusEl.innerHTML = `
            <span class="text-amber-400 font-bold animate-pulse">● Active: ${escapeHtml(stat.active_job.title)} (${escapeHtml(stat.active_job.progress_pct || 0)}%)</span>
            <span class="block text-[11px] text-neutral-400 truncate mt-0.5">${escapeHtml(stat.active_job.stage || 'Transcoding segments...')}</span>
          `;
        } else if (stat && stat.queue_length > 0) {
          workerStatusEl.innerHTML = `<span class="text-cyan-400 font-bold">● ${stat.queue_length} jobs queued</span> <span class="text-neutral-400 text-[11px]">(Processing in background)</span>`;
        } else {
          workerStatusEl.innerHTML = `<span class="text-emerald-400 font-bold">● Idle</span> <span class="text-neutral-400 text-[11px]">(Ready for stream jobs)</span>`;
        }
      }

      if (workerConcurrencyEl) {
        const limit = sett?.cpu_thread_limit || 'auto';
        const limitLabel = limit === '1' ? '1 Thread (Low-Spec)' : (limit === '2' ? '2 Threads (Balanced)' : 'Auto Governance');
        const cores = caps?.cpu_cores || 1;
        workerConcurrencyEl.innerText = `${limitLabel} (${cores} Host Cores Detected)`;
      }

      if (workerPriorityEl) {
        const isAbr = sett?.transcode_mode === 'abr_transcode';
        if (caps?.is_low_spec) {
          workerPriorityEl.innerHTML = `<span class="text-amber-400 font-semibold">Low-Spec Guarded (Nice 19 / Idle IO)</span>`;
        } else {
          workerPriorityEl.innerText = isAbr ? 'Nice 10 (Multi-Bitrate ABR)' : 'Nice 19 + ionice Idle (CPU-Safe Remux)';
        }
      }
    }

    async function fetchSystem() {
      try {
        const res = await fetch('/api/admin/dashboard-summary').then(r => r.ok ? r.json() : null).catch(() => null);
        if (res) {
          renderSystem(res.capabilities, res.settings, res.queue, true);
          return;
        }
      } catch (e) {}
      renderSystem(null, null, null, false);
    }

    function applyOptimisticTranscodeModeUI(mode) {
      currentMode = mode;
      const isAbr = (mode === 'abr_transcode');

      // 1. Dashboard controls
      const badge = document.getElementById('transcode-badge');
      const btn = document.getElementById('transcode-btn');
      const desc = document.getElementById('transcode-desc');
      if (badge) {
        badge.innerText = isAbr ? 'Multi-Bitrate ABR (Mode A)' : 'Lossless Remux (Mode B)';
        badge.className = isAbr
          ? 'px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-purple-500/20 text-purple-400 border border-purple-500/30'
          : 'px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
      }
      if (btn) {
        btn.innerText = isAbr ? 'Switch to Lossless Remux' : 'Enable ABR Transcoding';
      }
      if (desc) {
        desc.innerText = isAbr
          ? 'Active: Transcodes videos into 1080p, 720p, 480p adaptive ladders with hardware acceleration.'
          : 'Active: Mode B Hardware-Agnostic Remuxing (Zero-CPU copy for H.264, CPU-safe capped libx264 conversion, stereo AAC audio).';
      }

      // 2. Media Sidebar controls
      const modeRemuxBtn = document.getElementById('side-mode-remux');
      const modeAbrBtn = document.getElementById('side-mode-abr');
      const modeDesc = document.getElementById('side-mode-desc');
      const govBadge = document.getElementById('side-governor-badge');

      if (modeRemuxBtn && modeAbrBtn) {
        modeRemuxBtn.classList.add('transition-all', 'duration-200');
        modeAbrBtn.classList.add('transition-all', 'duration-200');
        if (isAbr) {
          modeRemuxBtn.className = 'py-1.5 px-2 rounded-lg text-[11px] font-semibold text-neutral-400 hover:text-white transition-all duration-200 flex items-center justify-center gap-1 cursor-pointer';
          modeAbrBtn.className = 'py-1.5 px-2 rounded-lg text-[11px] font-bold transition-all duration-200 flex items-center justify-center gap-1 cursor-pointer bg-red-600 text-white shadow';
          if (modeDesc) modeDesc.innerText = 'Mode A: Multi-Bitrate ABR ladders (1080p, 720p, 480p) with 1080p stream reuse.';
        } else {
          modeRemuxBtn.className = 'py-1.5 px-2 rounded-lg text-[11px] font-bold transition-all duration-200 flex items-center justify-center gap-1 cursor-pointer bg-emerald-600 text-white shadow';
          modeAbrBtn.className = 'py-1.5 px-2 rounded-lg text-[11px] font-semibold text-neutral-400 hover:text-white transition-all duration-200 flex items-center justify-center gap-1 cursor-pointer';
          if (modeDesc) modeDesc.innerText = 'Mode B: Zero-CPU stream copy for 8-bit H.264 (~10s packaging at disk speed).';
        }
      }

      if (govBadge) {
        govBadge.innerText = '⏳ Applying...';
        govBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse';
      }
      if (modeRemuxBtn) modeRemuxBtn.disabled = true;
      if (modeAbrBtn) modeAbrBtn.disabled = true;
      if (btn) btn.disabled = true;
    }

    function restoreGovernorBadge() {
      const govBadge = document.getElementById('side-governor-badge');
      if (govBadge) {
        govBadge.innerText = '✓ Active';
        govBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
      }
      const modeRemuxBtn = document.getElementById('side-mode-remux');
      const modeAbrBtn = document.getElementById('side-mode-abr');
      const btn = document.getElementById('transcode-btn');
      if (modeRemuxBtn) modeRemuxBtn.disabled = false;
      if (modeAbrBtn) modeAbrBtn.disabled = false;
      if (btn) btn.disabled = false;
    }

    let settingsMutationInFlight = false;
    let lastSettingsMutationTs = 0;

    async function setTranscodeMode(mode) {
      settingsMutationInFlight = true;
      lastSettingsMutationTs = Date.now();
      applyOptimisticTranscodeModeUI(mode);
      const isAbr = (mode === 'abr_transcode');
      try {
        await updateLiveSettings({ transcoding_enabled: isAbr, transcode_mode: mode });
      } finally {
        settingsMutationInFlight = false;
        lastSettingsMutationTs = Date.now();
        restoreGovernorBadge();
      }
    }

    function onCpuSliderInput(val) {
      const label = document.getElementById('side-cpu-val');
      const slider = document.getElementById('side-cpu-slider');
      const maxCores = parseInt((slider && slider.max) || '8');
      const pct = Math.round((parseInt(val) / maxCores) * 100);
      if (label) label.innerText = `${val} / ${maxCores} Cores (~${Math.min(95, Math.max(15, pct))}%)`;
    }

    async function applyCpuSliderChange(val) {
      await updateLiveSettings({ allocated_cpu_cores: parseInt(val) });
    }

    async function applyRamLimit(val) {
      await updateLiveSettings({ allocated_ram_mb: parseInt(val) });
    }

    async function applyGpuToggle(enabled) {
      await updateLiveSettings({ gpu_enabled: Boolean(enabled) });
    }

    let currentGpuOsTab = 'linux';
    function selectGpuGuideOs(osName) {
      window._userSelectedGpuTab = true;
      currentGpuOsTab = osName;
      ['linux', 'windows', 'macos'].forEach(os => {
        const tab = document.getElementById(`tab-os-${os}`);
        const content = document.getElementById(`guide-content-${os}`);
        if (tab) {
          if (os === osName) {
            tab.className = 'py-1 px-1.5 rounded text-[10px] font-bold transition flex items-center justify-center gap-1 cursor-pointer bg-neutral-800 text-white border border-amber-500/40';
          } else {
            tab.className = 'py-1 px-1.5 rounded text-[10px] font-semibold transition flex items-center justify-center gap-1 cursor-pointer text-neutral-400 hover:text-white';
          }
        }
        if (content) {
          if (os === osName) content.classList.remove('hidden');
          else content.classList.add('hidden');
        }
      });
    }

    const LINUX_DISTRO_CMDS = {
      ubuntu: 'sudo apt update && sudo apt install -y nvidia-driver-580 && sudo reboot',
      arch: 'sudo pacman -Sy --noconfirm nvidia nvidia-utils && sudo reboot',
      fedora: 'sudo dnf install -y akmod-nvidia xorg-x11-drv-nvidia-cuda && sudo reboot'
    };

    function selectLinuxDistro(distro) {
      ['ubuntu', 'arch', 'fedora'].forEach(d => {
        const btn = document.getElementById(`btn-distro-${d}`);
        if (btn) {
          if (d === distro) {
            btn.className = 'px-1.5 py-0.5 rounded font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 cursor-pointer';
          } else {
            btn.className = 'px-1.5 py-0.5 rounded font-mono text-neutral-400 hover:text-white bg-neutral-800 cursor-pointer';
          }
        }
      });
      const cmdEl = document.getElementById('side-gpu-linux-cmd');
      if (cmdEl && LINUX_DISTRO_CMDS[distro]) {
        cmdEl.innerText = LINUX_DISTRO_CMDS[distro];
      }
    }

    function copyTextFromElement(elementId) {
      const el = document.getElementById(elementId);
      if (el) {
        const text = el.innerText.trim();
        navigator.clipboard.writeText(text).then(() => {
          showAdminToast('✓ Copied install command to clipboard!', 'success');
        }).catch(() => {
          showAdminToast('Command: ' + text, 'info');
        });
      }
    }


    async function updateLiveSettings(partialPayload) {
      settingsMutationInFlight = true;
      lastSettingsMutationTs = Date.now();
      try {
        const res = await fetch('/api/system/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(partialPayload)
        }).catch(() => null);

        if (res && res.ok) {
          const data = await res.json().catch(() => ({}));
          showAdminToast('✓ Live resource limits updated', 'success');
          if (data && data.settings) {
            renderSystem(null, data.settings, null, true);
          }
        } else {
          const err = res ? await res.json().catch(() => ({})) : {};
          showAdminToast('Failed to update settings: ' + (err.detail || 'Error'), 'error');
        }
      } catch (e) {
        showAdminToast('Error updating settings: ' + e.message, 'error');
      } finally {
        settingsMutationInFlight = false;
        lastSettingsMutationTs = Date.now();
      }
    }

    async function updateCpuThreadLimit(val) {
      try {
        const res = await fetch('/api/system/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cpu_thread_limit: val })
        }).catch(() => null);

        if (res && res.ok) {
          const label = val === '1' ? '1 Thread — Ultra Low Spec (~20% CPU)' : (val === '2' ? '2 Threads — Balanced (~40% CPU)' : 'Auto — Fast Governance');
          showAdminToast(`Governor thread policy updated to ${label}`, 'success');
          fetchSystem().catch(() => {});
        } else {
          const err = res ? await res.json().catch(() => ({})) : {};
          showAdminToast('Failed to update CPU limit: ' + (err.detail || 'Error'), 'error');
        }
      } catch (e) {
        showAdminToast('Error updating CPU limit: ' + e.message, 'error');
      }
    }

    async function toggleTranscoding() {
      const next = currentMode === 'abr_transcode' ? 'lossless_remux' : 'abr_transcode';
      await setTranscodeMode(next);
    }


    async function loadMetrics() {
      try {
        const m = await fetch('/api/metrics').then(r => r.json());
        const elUsers = document.getElementById('metric-users');
        if (elUsers) elUsers.innerText = m.total_users;
        const elVisits = document.getElementById('metric-visits');
        if (elVisits) elVisits.innerText = m.total_visits;
        const elActive = document.getElementById('metric-active');
        if (elActive) elActive.innerText = m.active_today !== undefined ? m.active_today : 1;
        const elMedia = document.getElementById('metric-media');
        if (elMedia) elMedia.innerText = m.total_media;
      } catch (e) {}

      // If user telemetry tab is active, also refresh directory
      const tabUsers = document.getElementById('tab-users');
      if (tabUsers && !tabUsers.classList.contains('hidden')) {
        loadAnalyticsData();
      }
    }


    let isPollingInFlight = false;
    let pollRetryAfter = 4000;
    let pollTimer = null;

    function isAnyAdminModalOpen() {
      return ['media-modal', 'ingest-config-modal', 'delete-modal', 'user-detail-modal'].some(id => {
        const el = document.getElementById(id);
        return el && !el.classList.contains('hidden');
      });
    }

    async function pollAdminState() {
      if (isPollingInFlight) return;
      if (document.visibilityState === 'hidden') return;
      if (isAnyAdminModalOpen()) {
        if (pollTimer) clearTimeout(pollTimer);
        pollTimer = setTimeout(pollAdminState, 4000);
        return;
      }

      isPollingInFlight = true;
      try {
        const res = await fetch('/api/admin/dashboard-summary').then(async r => {
          if (r.status === 429) {
            const retryHeader = r.headers.get('Retry-After');
            const retrySec = retryHeader ? parseInt(retryHeader, 10) : 6;
            pollRetryAfter = Math.max(retrySec * 1000, 6000);
            return null;
          }
          if (!r.ok) return null;
          pollRetryAfter = 4000; // Reset backoff on success
          return r.json();
        }).catch(() => null);

        if (res) {
          const skipSettings = settingsMutationInFlight || (Date.now() - lastSettingsMutationTs < 2500);
          renderSystem(res.capabilities, skipSettings ? null : res.settings, res.queue, true);
          renderQueueStatus(res.queue);

          // Update KPI metrics on Dashboard cards
          if (res.kpis) {
            const elUsers = document.getElementById('metric-users');
            if (elUsers) elUsers.innerText = res.kpis.registered_accounts ?? 0;
            const elVisits = document.getElementById('metric-visits');
            if (elVisits) elVisits.innerText = res.kpis.total_sessions ?? 0;
            const elActive = document.getElementById('metric-active');
            if (elActive) elActive.innerText = res.kpis.active_today ?? 1;
            const elMedia = document.getElementById('metric-media');
            if (elMedia) elMedia.innerText = res.kpis.catalog_titles ?? 0;
          }

          // Tab-specific: Only render downloads if Aria2 tab is visible
          const tabAria2 = document.getElementById('tab-aria2');
          if (tabAria2 && !tabAria2.classList.contains('hidden') && res.downloads) {
            renderDownloads(res.downloads);
          }

          // Tab-specific: Only refresh user audit logs if User Telemetry tab is visible
          const tabUsers = document.getElementById('tab-users');
          const userModal = document.getElementById('user-detail-modal');
          const searchInput = document.getElementById('analytics-search');
          const isUsersActive = tabUsers && !tabUsers.classList.contains('hidden');
          const isModalOpen = userModal && !userModal.classList.contains('hidden');
          const isSearchFocused = searchInput && document.activeElement === searchInput;
          if (isUsersActive && !isModalOpen && !isSearchFocused) {
            loadAnalyticsData();
          }
        } else {
          renderSystem(null, null, null, false);
        }
      } catch (err) {
        renderSystem(null, null, null, false);
      } finally {
        isPollingInFlight = false;
        if (pollTimer) clearTimeout(pollTimer);
        pollTimer = setTimeout(pollAdminState, pollRetryAfter);
      }
    }

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        if (pollTimer) clearTimeout(pollTimer);
        pollAdminState();
      }
    });

"""
