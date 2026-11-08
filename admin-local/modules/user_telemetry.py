import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query
import httpx
from .common import MEDIA_SERVER_URL, ADMIN_SECRET

router = APIRouter()

@router.get("/api/admin/analytics/users")
async def get_analytics_users_proxy(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    search: str = Query("", max_length=64),
    status_filter: str = Query("all"),
    sort_by: str = Query("last_seen_at"),
    sort_dir: str = Query("desc")
):
    """Proxy to Media Server user analytics directory"""
    url = f"{MEDIA_SERVER_URL}/api/system/analytics/users"
    params = {
        "page": page,
        "page_size": page_size,
        "search": search,
        "status_filter": status_filter,
        "sort_by": sort_by,
        "sort_dir": sort_dir
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, params=params, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.get("/api/admin/analytics/login-logs")
async def get_analytics_login_logs_proxy(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    user_id: Optional[str] = Query(None),
    search: str = Query("", max_length=64)
):
    """Proxy to Media Server login/session audit logs"""
    url = f"{MEDIA_SERVER_URL}/api/system/analytics/login-logs"
    params = {"page": page, "page_size": page_size, "search": search}
    if user_id:
        params["user_id"] = user_id
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, params=params, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")


@router.get("/api/admin/analytics/users/{user_id}/detail")
async def get_user_drilldown_proxy(user_id: str):
    """Proxy to Media Server single-user drill-down detail"""
    url = f"{MEDIA_SERVER_URL}/api/system/analytics/users/{user_id}/detail"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers={"X-Admin-Secret": ADMIN_SECRET})
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Failed to connect to Media Server: {e}")



USER_TELEMETRY_HTML = r"""      <!-- TAB 4: USER TELEMETRY & PRODUCTION ANALYTICS AUDIT -->
      <section id="tab-users" class="space-y-6 hidden">
        <!-- TOP 5 KPI SUMMARY CARDS -->
        <div class="grid grid-cols-2 md:grid-cols-5 gap-3.5 sm:gap-4">
          <!-- KPI 1: Total Registered Users -->
          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 space-y-1.5 shadow-lg">
            <div class="flex items-center justify-between text-neutral-400">
              <span class="text-[11px] font-bold uppercase tracking-wider">Total Users</span>
              <svg class="w-4 h-4 text-neutral-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>
            </div>
            <div id="kpi-total-users" class="text-2xl font-black text-white font-mono tracking-tight">--</div>
            <p class="text-[10px] text-neutral-500">Registered platform accounts</p>
          </div>

          <!-- KPI 2: Online Now -->
          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 space-y-1.5 shadow-lg">
            <div class="flex items-center justify-between text-emerald-400">
              <span class="text-[11px] font-bold uppercase tracking-wider">Online Now</span>
              <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
            </div>
            <div id="kpi-online-now" class="text-2xl font-black text-emerald-400 font-mono tracking-tight">--</div>
            <p class="text-[10px] text-neutral-500">Heartbeat &le; 2 mins ago</p>
          </div>

          <!-- KPI 3: Streaming Now -->
          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 space-y-1.5 shadow-lg">
            <div class="flex items-center justify-between text-red-400">
              <span class="text-[11px] font-bold uppercase tracking-wider">Streaming Now</span>
              <span class="w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse"></span>
            </div>
            <div id="kpi-watching-now" class="text-2xl font-black text-red-400 font-mono tracking-tight">--</div>
            <p class="text-[10px] text-neutral-500">Active video playback</p>
          </div>

          <!-- KPI 4: Total Platform Watch Time -->
          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 space-y-1.5 shadow-lg">
            <div class="flex items-center justify-between text-purple-400">
              <span class="text-[11px] font-bold uppercase tracking-wider">Watch Time</span>
              <svg class="w-4 h-4 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            </div>
            <div id="kpi-total-watch" class="text-xl sm:text-2xl font-black text-white font-mono tracking-tight truncate">--</div>
            <p class="text-[10px] text-neutral-500">Cumulative video streamed</p>
          </div>

          <!-- KPI 5: Total Platform Site Time -->
          <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-4 space-y-1.5 shadow-lg col-span-2 md:col-span-1">
            <div class="flex items-center justify-between text-cyan-400">
              <span class="text-[11px] font-bold uppercase tracking-wider">Site Time</span>
              <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            </div>
            <div id="kpi-total-site" class="text-xl sm:text-2xl font-black text-white font-mono tracking-tight truncate">--</div>
            <p class="text-[10px] text-neutral-500">Cumulative active sessions</p>
          </div>
        </div>

        <!-- MAIN ANALYTICS CONTAINER -->
        <div class="bg-[#141414] border border-neutral-800 rounded-2xl p-5 space-y-4 shadow-xl">
          <!-- SUB-VIEW TABS & HEADER -->
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-neutral-800">
            <div class="flex items-center gap-2">
              <button type="button" id="subtab-btn-directory" onclick="switchAnalyticsSubTab('directory')" class="px-3.5 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-2 bg-red-600/20 text-red-400 border border-red-500/40">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
                <span>User Analytics Directory</span>
              </button>
              <button type="button" id="subtab-btn-audit" onclick="switchAnalyticsSubTab('audit')" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition flex items-center gap-2 border border-transparent">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"></path></svg>
                <span>Full Login &amp; Session Audit Log</span>
              </button>
            </div>

            <div class="flex items-center gap-2">
              <button onclick="loadAnalyticsData()" id="btn-refresh-analytics" class="px-3 py-1.5 bg-[#1f1f1f] hover:bg-[#282828] text-neutral-300 hover:text-white rounded-xl text-xs font-semibold transition cursor-pointer flex items-center gap-1.5 border border-neutral-800">
                <svg id="refresh-analytics-spinner" class="w-3.5 h-3.5 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                <span>Refresh</span>
              </button>
            </div>
          </div>

          <!-- CONTROLS & FILTER BAR -->
          <div class="bg-[#181818] border border-neutral-800 rounded-xl p-3 flex flex-col md:flex-row gap-3 items-stretch md:items-center justify-between">
            <div class="relative flex-1">
              <input type="text" id="analytics-search" oninput="debounceAnalyticsSearch()" placeholder="Search email, display name, IP, or country..." class="w-full bg-[#141414] border border-neutral-700 focus:border-red-500 rounded-xl px-3.5 py-2 text-xs text-white placeholder-neutral-500 focus:outline-none transition">
              <button type="button" onclick="clearAnalyticsSearch()" class="absolute right-3 top-2.5 text-neutral-500 hover:text-white text-xs cursor-pointer">&times;</button>
            </div>

            <div class="flex flex-wrap items-center gap-2">
              <!-- Status Filter (Directory view only) -->
              <div id="analytics-status-filter-wrapper" class="flex items-center gap-1.5">
                <select id="analytics-status-filter" onchange="onAnalyticsStatusChange()" class="bg-[#141414] border border-neutral-700 text-neutral-300 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-red-500">
                  <option value="all">All Statuses</option>
                  <option value="online">🟢 Online Now</option>
                  <option value="watching">🔴 Streaming Now</option>
                  <option value="offline">⚪ Offline / Signed Out</option>
                </select>
              </div>

              <!-- Sort Dropdown (Directory view only) -->
              <div id="analytics-sort-wrapper" class="flex items-center gap-1.5">
                <select id="analytics-sort-by" onchange="onAnalyticsSortChange()" class="bg-[#141414] border border-neutral-700 text-neutral-300 text-xs rounded-xl px-3 py-2 focus:outline-none focus:border-red-500">
                  <option value="last_seen_at">Sort: Recently Active</option>
                  <option value="total_watch_seconds">Sort: Most Watch Time</option>
                  <option value="total_site_seconds">Sort: Most Site Time</option>
                  <option value="first_joined_at">Sort: Newest Users</option>
                  <option value="total_logins">Sort: Most Logins</option>
                </select>
              </div>

              <!-- Page Size -->
              <select id="analytics-page-size" onchange="onAnalyticsPageSizeChange(this.value)" class="bg-[#141414] border border-neutral-700 text-neutral-300 text-xs rounded-xl px-2.5 py-2 focus:outline-none focus:border-red-500 font-mono">
                <option value="10">10 / page</option>
                <option value="25" selected>25 / page</option>
                <option value="50">50 / page</option>
                <option value="100">100 / page</option>
              </select>
            </div>
          </div>

          <!-- VIEW 1: USER ANALYTICS DIRECTORY TABLE -->
          <div id="analytics-view-directory" class="space-y-3">
            <div class="overflow-x-auto rounded-xl border border-neutral-800/80">
              <table class="w-full text-left text-xs border-collapse">
                <thead>
                  <tr class="bg-[#181818] border-b border-neutral-800 text-neutral-400 uppercase text-[10px] tracking-wider font-bold">
                    <th class="py-3 px-3.5">User Profile</th>
                    <th class="py-3 px-3">Live Status</th>
                    <th class="py-3 px-3">Dual-Stack Client IPs</th>
                    <th class="py-3 px-3">Location</th>
                    <th class="py-3 px-3">Site Time</th>
                    <th class="py-3 px-3">Streaming &amp; Ratio</th>
                    <th class="py-3 px-3">Activity Timestamps</th>
                    <th class="py-3 px-3.5 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody id="users-directory-tbody" class="divide-y divide-neutral-800/60 bg-[#141414] text-neutral-300">
                  <tr><td colspan="8" class="py-8 text-center text-neutral-500">Loading user telemetry directory...</td></tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- VIEW 2: FULL SESSION & SECURITY AUDIT LOG TABLE -->
          <div id="analytics-view-audit" class="space-y-3 hidden">
            <div class="overflow-x-auto rounded-xl border border-neutral-800/80">
              <table class="w-full text-left text-xs border-collapse">
                <thead>
                  <tr class="bg-[#181818] border-b border-neutral-800 text-neutral-400 uppercase text-[10px] tracking-wider font-bold">
                    <th class="py-3 px-3.5">User / Account</th>
                    <th class="py-3 px-3">Session Start</th>
                    <th class="py-3 px-3">Duration (Site / Watch)</th>
                    <th class="py-3 px-3">Dual-Stack Client IPs</th>
                    <th class="py-3 px-3">Location</th>
                    <th class="py-3 px-3">Device / Browser / OS</th>
                    <th class="py-3 px-3.5 text-right">Session State</th>
                  </tr>
                </thead>
                <tbody id="users-audit-tbody" class="divide-y divide-neutral-800/60 bg-[#141414] text-neutral-300">
                  <tr><td colspan="7" class="py-8 text-center text-neutral-500">Loading session audit logs...</td></tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- PAGINATION CONTROLS FOOTER -->
          <div class="flex flex-col sm:flex-row items-center justify-between gap-3 pt-3 border-t border-neutral-800 text-xs text-neutral-400">
            <div id="analytics-pagination-info" class="font-mono text-[11px]">
              Showing 0 to 0 of 0 records
            </div>

            <div class="flex items-center gap-1.5 font-mono text-xs">
              <button type="button" id="btn-page-first" onclick="changeAnalyticsPage('first')" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800">&laquo;</button>
              <button type="button" id="btn-page-prev" onclick="changeAnalyticsPage(-1)" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800">&lsaquo; Prev</button>
              <span id="analytics-page-display" class="px-3 py-1.5 rounded-lg bg-black border border-neutral-800 text-white font-bold text-[11px]">Page 1 of 1</span>
              <button type="button" id="btn-page-next" onclick="changeAnalyticsPage(1)" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800">Next &rsaquo;</button>
              <button type="button" id="btn-page-last" onclick="changeAnalyticsPage('last')" class="px-2.5 py-1.5 rounded-lg bg-[#1a1a1a] hover:bg-neutral-800 text-neutral-300 disabled:opacity-30 disabled:cursor-not-allowed transition border border-neutral-800">&raquo;</button>
            </div>
          </div>
        </div>
      </section>
"""

USER_DETAIL_MODAL_HTML = r"""  <!-- USER DRILL-DOWN ANALYTICS MODAL -->
  <div id="user-detail-modal" class="fixed inset-0 z-50 bg-black/85 hidden items-center justify-center p-4 overflow-y-auto">
    <div class="bg-[#141414] border border-neutral-800 rounded-3xl w-full max-w-3xl p-6 sm:p-7 space-y-5 shadow-2xl my-auto max-h-[90vh] overflow-y-auto">
      <div class="flex items-start justify-between pb-4 border-b border-neutral-800">
        <div class="flex items-center gap-3">
          <div id="detail-user-avatar" class="w-12 h-12 rounded-2xl bg-neutral-800 border border-neutral-700 flex items-center justify-center text-lg font-bold text-white shadow-inner">
            U
          </div>
          <div>
            <div class="flex items-center gap-2">
              <h3 id="detail-user-email" class="text-base font-bold text-white truncate max-w-sm">user@example.com</h3>
              <span id="detail-user-status" class="px-2 py-0.5 rounded text-[10px] font-bold">Offline</span>
            </div>
            <p class="text-xs text-neutral-400 mt-0.5">
              <span id="detail-user-name" class="text-neutral-300 font-medium">Display Name</span> &bull; 
              Joined <span id="detail-user-joined" class="font-mono text-neutral-400">--</span> &bull; 
              ID: <code id="detail-user-id" class="font-mono text-[10px] text-neutral-500"></code>
            </p>
          </div>
        </div>
        <button onclick="closeUserDetailModal()" class="text-neutral-400 hover:text-white text-xl cursor-pointer px-2 py-1">&times;</button>
      </div>

      <!-- 4 HIGHLIGHT METRIC PILLS -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div class="bg-neutral-900/80 border border-neutral-800 p-3 rounded-xl space-y-0.5">
          <span class="text-[10px] font-bold text-purple-400 uppercase tracking-wider">Watch Time</span>
          <div id="detail-stat-watch" class="text-lg font-bold text-white font-mono">0s</div>
        </div>
        <div class="bg-neutral-900/80 border border-neutral-800 p-3 rounded-xl space-y-0.5">
          <span class="text-[10px] font-bold text-cyan-400 uppercase tracking-wider">Site Time</span>
          <div id="detail-stat-site" class="text-lg font-bold text-white font-mono">0s</div>
        </div>
        <div class="bg-neutral-900/80 border border-neutral-800 p-3 rounded-xl space-y-0.5">
          <span class="text-[10px] font-bold text-emerald-400 uppercase tracking-wider">Watch Ratio</span>
          <div id="detail-stat-ratio" class="text-lg font-bold text-white font-mono">0%</div>
        </div>
        <div class="bg-neutral-900/80 border border-neutral-800 p-3 rounded-xl space-y-0.5">
          <span class="text-[10px] font-bold text-amber-400 uppercase tracking-wider">Total Logins</span>
          <div id="detail-stat-logins" class="text-lg font-bold text-white font-mono">0</div>
        </div>
      </div>

      <!-- DUAL STACK & CLIENT INFO CARD -->
      <div class="bg-neutral-900/60 border border-neutral-800 p-3.5 rounded-xl space-y-2 text-xs">
        <div class="text-[11px] font-bold text-neutral-400 uppercase tracking-wider">Last Known Network &amp; Device</div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-neutral-300">
          <div class="flex items-center justify-between bg-black/40 p-2 rounded border border-neutral-800">
            <span class="text-neutral-400">IPv4 Address:</span>
            <span id="detail-ipv4" class="font-mono text-emerald-400 font-semibold select-all">None</span>
          </div>
          <div class="flex items-center justify-between bg-black/40 p-2 rounded border border-neutral-800">
            <span class="text-neutral-400">IPv6 Address:</span>
            <span id="detail-ipv6" class="font-mono text-cyan-400 font-semibold truncate max-w-[200px] select-all">None</span>
          </div>
          <div class="flex items-center justify-between bg-black/40 p-2 rounded border border-neutral-800">
            <span class="text-neutral-400">Geo Location:</span>
            <span id="detail-location" class="font-medium text-white">Unknown</span>
          </div>
          <div class="flex items-center justify-between bg-black/40 p-2 rounded border border-neutral-800">
            <span class="text-neutral-400">Device &amp; Browser:</span>
            <span id="detail-device-browser" class="font-medium text-white truncate max-w-[200px]">Unknown</span>
          </div>
        </div>
      </div>

      <!-- SECTION A: RECENT WATCH PROGRESS -->
      <div class="space-y-2">
        <div class="flex items-center justify-between text-xs font-bold text-neutral-300">
          <span class="uppercase tracking-wider">Recent Watch Progress &amp; Activity</span>
          <span id="detail-watch-count" class="text-neutral-500 font-mono text-[11px]">0 titles</span>
        </div>
        <div id="detail-watch-list" class="space-y-2 max-h-48 overflow-y-auto pr-1">
          <div class="text-neutral-500 text-xs py-2 italic">No watch progress recorded for this user.</div>
        </div>
      </div>

      <!-- SECTION B: RECENT SESSIONS -->
      <div class="space-y-2">
        <div class="flex items-center justify-between text-xs font-bold text-neutral-300">
          <span class="uppercase tracking-wider">Recent Login Sessions (Last 10)</span>
          <span id="detail-session-count" class="text-neutral-500 font-mono text-[11px]">0 sessions</span>
        </div>
        <div id="detail-session-list" class="space-y-2 max-h-48 overflow-y-auto pr-1 text-xs">
          <div class="text-neutral-500 text-xs py-2 italic">No login sessions recorded.</div>
        </div>
      </div>

      <div class="flex justify-end pt-2 border-t border-neutral-800">
        <button type="button" onclick="closeUserDetailModal()" class="px-5 py-2 bg-neutral-800 hover:bg-neutral-700 text-white rounded-xl text-xs font-bold transition cursor-pointer">
          Close
        </button>
      </div>
    </div>
  </div>

"""

USER_TELEMETRY_JS = r"""    // ==========================================
    // USER ANALYTICS & PAGINATED AUDIT ENGINE
    // ==========================================
    let currentAnalyticsTab = 'directory'; // 'directory' | 'audit'
    let analyticsUsersPage = 1;
    let analyticsUsersPageSize = 25;
    let analyticsUsersTotal = 0;
    let analyticsUsersTotalPages = 1;

    let analyticsAuditPage = 1;
    let analyticsAuditPageSize = 25;
    let analyticsAuditTotal = 0;
    let analyticsAuditTotalPages = 1;

    let analyticsSearchTimer = null;

    function formatDuration(totalSec) {
      const s = Math.max(0, parseInt(totalSec || 0));
      if (s < 60) return `${s}s`;
      const mins = Math.floor(s / 60);
      const remSec = s % 60;
      if (mins < 60) return remSec > 0 ? `${mins}m ${remSec}s` : `${mins}m`;
      const hrs = Math.floor(mins / 60);
      const remMins = mins % 60;
      return remMins > 0 ? `${hrs}h ${remMins}m` : `${hrs}h`;
    }

    function formatRelativeTime(isoStr) {
      if (!isoStr) return 'Never';
      try {
        const dt = new Date(isoStr);
        if (isNaN(dt.getTime())) return 'Unknown';
        const now = new Date();
        const diffSec = Math.floor((now.getTime() - dt.getTime()) / 1000);
        if (diffSec < 45) return 'Just now';
        if (diffSec < 120) return '1m ago';
        if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
        if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
        const days = Math.floor(diffSec / 86400);
        if (days < 30) return `${days}d ago`;
        return dt.toLocaleDateString();
      } catch (e) {
        return 'Unknown';
      }
    }

    async function copyToClipboard(text, label = 'Text') {
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        showAdminToast({ type: 'success', title: 'Copied', message: `Copied ${label} to clipboard!` });
      } catch (e) {
        showAdminToast({ type: 'info', title: 'Clipboard', message: text });
      }
    }

    function switchAnalyticsSubTab(tab) {
      currentAnalyticsTab = tab;
      const btnDir = document.getElementById('subtab-btn-directory');
      const btnAudit = document.getElementById('subtab-btn-audit');
      const viewDir = document.getElementById('analytics-view-directory');
      const viewAudit = document.getElementById('analytics-view-audit');
      const filterWrap = document.getElementById('analytics-status-filter-wrapper');
      const sortWrap = document.getElementById('analytics-sort-wrapper');

      if (tab === 'directory') {
        if (btnDir) btnDir.className = 'px-3.5 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-2 bg-red-600/20 text-red-400 border border-red-500/40';
        if (btnAudit) btnAudit.className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition flex items-center gap-2 border border-transparent';
        if (viewDir) viewDir.classList.remove('hidden');
        if (viewAudit) viewAudit.classList.add('hidden');
        if (filterWrap) filterWrap.classList.remove('hidden');
        if (sortWrap) sortWrap.classList.remove('hidden');
      } else {
        if (btnDir) btnDir.className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition flex items-center gap-2 border border-transparent';
        if (btnAudit) btnAudit.className = 'px-3.5 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-2 bg-red-600/20 text-red-400 border border-red-500/40';
        if (viewDir) viewDir.classList.add('hidden');
        if (viewAudit) viewAudit.classList.remove('hidden');
        if (filterWrap) filterWrap.classList.add('hidden');
        if (sortWrap) sortWrap.classList.add('hidden');
      }
      loadAnalyticsData();
    }

    function debounceAnalyticsSearch() {
      clearTimeout(analyticsSearchTimer);
      analyticsSearchTimer = setTimeout(() => {
        if (currentAnalyticsTab === 'directory') analyticsUsersPage = 1;
        else analyticsAuditPage = 1;
        loadAnalyticsData();
      }, 350);
    }

    function clearAnalyticsSearch() {
      const input = document.getElementById('analytics-search');
      if (input) input.value = '';
      if (currentAnalyticsTab === 'directory') analyticsUsersPage = 1;
      else analyticsAuditPage = 1;
      loadAnalyticsData();
    }

    function onAnalyticsStatusChange() {
      analyticsUsersPage = 1;
      loadAnalyticsData();
    }

    function onAnalyticsSortChange() {
      analyticsUsersPage = 1;
      loadAnalyticsData();
    }

    function onAnalyticsPageSizeChange(val) {
      const parsed = parseInt(val) || 25;
      if (currentAnalyticsTab === 'directory') {
        analyticsUsersPageSize = parsed;
        analyticsUsersPage = 1;
      } else {
        analyticsAuditPageSize = parsed;
        analyticsAuditPage = 1;
      }
      loadAnalyticsData();
    }

    function changeAnalyticsPage(target) {
      if (currentAnalyticsTab === 'directory') {
        if (target === 'first') analyticsUsersPage = 1;
        else if (target === 'last') analyticsUsersPage = analyticsUsersTotalPages;
        else analyticsUsersPage = Math.max(1, Math.min(analyticsUsersTotalPages, analyticsUsersPage + target));
      } else {
        if (target === 'first') analyticsAuditPage = 1;
        else if (target === 'last') analyticsAuditPage = analyticsAuditTotalPages;
        else analyticsAuditPage = Math.max(1, Math.min(analyticsAuditTotalPages, analyticsAuditPage + target));
      }
      loadAnalyticsData();
    }

    async function loadAnalyticsData() {
      const spinner = document.getElementById('refresh-analytics-spinner');
      if (spinner) spinner.classList.add('animate-spin');

      try {
        if (currentAnalyticsTab === 'directory') {
          await loadUsersDirectory();
        } else {
          await loadSessionAuditLogs();
        }
      } finally {
        if (spinner) setTimeout(() => spinner.classList.remove('animate-spin'), 300);
      }
    }

    async function loadUsersDirectory() {
      const tbody = document.getElementById('users-directory-tbody');
      const search = (document.getElementById('analytics-search')?.value || '').trim();
      const statusFilter = document.getElementById('analytics-status-filter')?.value || 'all';
      const sortBy = document.getElementById('analytics-sort-by')?.value || 'last_seen_at';

      const params = new URLSearchParams({
        page: analyticsUsersPage,
        page_size: analyticsUsersPageSize,
        search: search,
        status_filter: statusFilter,
        sort_by: sortBy,
        sort_dir: 'desc'
      });

      try {
        const res = await fetch(`/api/admin/analytics/users?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // 1. Update KPI Summary Cards
        if (data.kpis) {
          const k = data.kpis;
          document.getElementById('kpi-total-users').innerText = (k.total_registered_users || 0).toLocaleString();
          document.getElementById('kpi-online-now').innerText = (k.online_now_count || 0).toLocaleString();
          document.getElementById('kpi-watching-now').innerText = (k.watching_now_count || 0).toLocaleString();
          document.getElementById('kpi-total-watch').innerText = formatDuration(k.total_platform_watch_seconds);
          document.getElementById('kpi-total-site').innerText = formatDuration(k.total_platform_site_seconds);

          // Mirror into dashboard KPI metrics
          const dashUsers = document.getElementById('metric-users');
          if (dashUsers) dashUsers.innerText = k.total_registered_users || 0;
          const dashActive = document.getElementById('metric-active');
          if (dashActive) dashActive.innerText = k.online_now_count || 0;
        }

        // 2. Update Pagination state
        analyticsUsersTotal = data.total_count || 0;
        analyticsUsersTotalPages = data.total_pages || 1;
        analyticsUsersPage = data.page || 1;

        updatePaginationFooter(analyticsUsersPage, analyticsUsersTotalPages, analyticsUsersTotal, analyticsUsersPageSize, 'users');

        // 3. Render Directory Table Rows
        const items = data.items || [];
        if (items.length === 0) {
          tbody.innerHTML = `
            <tr>
              <td colspan="8" class="py-12 text-center text-neutral-500">
                <div class="flex flex-col items-center justify-center gap-2">
                  <svg class="w-8 h-8 text-neutral-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
                  <span>No user accounts match the current filter or search criteria.</span>
                </div>
              </td>
            </tr>
          `;
          return;
        }

        tbody.innerHTML = items.map(user => {
          const isWatching = Boolean(user.is_watching);
          const isOnline = Boolean(user.is_online);
          const email = user.email || 'Anonymous User';
          const name = user.display_name || email.split('@')[0];
          const initial = (name[0] || 'U').toUpperCase();

          // Status Badge
          let statusBadge = '';
          if (isWatching) {
            statusBadge = `
              <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-red-600/20 text-red-400 border border-red-500/40">
                <span class="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"></span>
                <span>Streaming</span>
              </span>
            `;
          } else if (isOnline) {
            statusBadge = `
              <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                <span>Online</span>
              </span>
            `;
          } else {
            statusBadge = `
              <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium bg-neutral-800 text-neutral-400 border border-neutral-700">
                <span class="w-1.5 h-1.5 rounded-full bg-neutral-600"></span>
                <span>Offline (${formatRelativeTime(user.last_seen_at)})</span>
              </span>
            `;
          }

          // Dual-Stack IPs
          const ipv4 = user.last_ipv4 || '';
          const ipv6 = user.last_ipv6 || '';

          const ipv4Html = ipv4 ? `
            <div class="flex items-center gap-1">
              <span class="px-1.5 py-0.5 bg-emerald-950/60 border border-emerald-600/30 text-emerald-300 font-mono text-[10px] rounded select-all">${escapeHtml(ipv4)}</span>
              <button type="button" data-val="${escapeHtml(ipv4)}" onclick="copyToClipboard(this.dataset.val, 'IPv4')" title="Copy IPv4" class="text-neutral-500 hover:text-white text-[10px] cursor-pointer">&boxbox;</button>
            </div>
          ` : '<span class="text-[10px] text-neutral-600 font-mono">IPv4: None</span>';

          const ipv6Html = ipv6 ? `
            <div class="flex items-center gap-1">
              <span class="px-1.5 py-0.5 bg-cyan-950/60 border border-cyan-600/30 text-cyan-300 font-mono text-[10px] rounded truncate max-w-[130px] select-all" title="${escapeHtml(ipv6)}">${escapeHtml(ipv6)}</span>
              <button type="button" data-val="${escapeHtml(ipv6)}" onclick="copyToClipboard(this.dataset.val, 'IPv6')" title="Copy IPv6" class="text-neutral-500 hover:text-white text-[10px] cursor-pointer">&boxbox;</button>
            </div>
          ` : '<span class="text-[10px] text-neutral-600 font-mono">IPv6: None</span>';

          // Location
          const locCountry = user.last_country || 'Local Host';
          const locCity = user.last_city || '';
          const locText = locCity ? `${locCity}, ${locCountry}` : locCountry;

          // Time & Ratio
          const siteSec = parseInt(user.total_site_seconds || 0);
          const watchSec = parseInt(user.total_watch_seconds || 0);
          const ratioPct = siteSec > 0 ? Math.min(100, Math.round((watchSec / siteSec) * 100)) : 0;

          // Joined Date
          const joinedStr = user.first_joined_at ? new Date(user.first_joined_at).toLocaleDateString() : '--';

          return `
            <tr class="hover:bg-[#181818]/90 transition">
              <td class="py-3 px-3.5">
                <div class="flex items-center gap-2.5">
                  <div class="w-8 h-8 rounded-xl bg-neutral-800 border border-neutral-700/80 flex items-center justify-center font-bold text-white text-xs flex-shrink-0 shadow-inner">
                    ${escapeHtml(initial)}
                  </div>
                  <div class="min-w-0">
                    <div class="font-bold text-white text-xs truncate max-w-[180px]" title="${escapeHtml(email)}">${escapeHtml(email)}</div>
                    <div class="text-[10px] text-neutral-400 truncate max-w-[180px]">${escapeHtml(name)}</div>
                  </div>
                </div>
              </td>
              <td class="py-3 px-3 whitespace-nowrap">
                ${statusBadge}
                ${isWatching && user.current_activity ? `
                  <div class="text-[9px] text-neutral-400 mt-1 truncate max-w-[140px]" title="${escapeHtml(user.current_activity)}">${escapeHtml(user.current_activity.replace('Watching: ', '▶ '))}</div>
                ` : ''}
              </td>
              <td class="py-3 px-3 whitespace-nowrap space-y-1">
                ${ipv4Html}
                ${ipv6Html}
              </td>
              <td class="py-3 px-3 whitespace-nowrap">
                <div class="text-xs text-neutral-200 font-medium">${escapeHtml(locText)}</div>
                <div class="text-[10px] text-neutral-500">${escapeHtml(user.last_device || 'Web Browser')}</div>
              </td>
              <td class="py-3 px-3 whitespace-nowrap font-mono text-cyan-300 font-semibold">
                ${formatDuration(siteSec)}
              </td>
              <td class="py-3 px-3 whitespace-nowrap">
                <div class="flex items-center justify-between text-[11px] mb-1">
                  <span class="font-mono text-purple-300 font-semibold">${formatDuration(watchSec)}</span>
                  <span class="text-[10px] font-mono text-neutral-400">${ratioPct}%</span>
                </div>
                <div class="w-24 bg-neutral-800 rounded-full h-1 overflow-hidden">
                  <div class="bg-gradient-to-r from-red-600 to-purple-500 h-1 rounded-full" style="width: ${ratioPct}%"></div>
                </div>
              </td>
              <td class="py-3 px-3 whitespace-nowrap text-[10px] text-neutral-400">
                <div>Joined: <span class="text-neutral-300 font-mono">${joinedStr}</span></div>
                <div>Logins: <span class="text-neutral-300 font-mono">${(user.total_logins || 0).toLocaleString()}</span></div>
              </td>
              <td class="py-3 px-3.5 text-right whitespace-nowrap">
                <button type="button" data-id="${escapeHtml(user.id)}" onclick="openUserDetailModal(this.dataset.id)" class="px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 hover:text-white rounded-xl text-xs font-semibold transition cursor-pointer flex items-center gap-1.5 inline-flex border border-neutral-700/60 shadow">
                  <svg class="w-3.5 h-3.5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
                  <span>Inspect</span>
                </button>
              </td>
            </tr>
          `;
        }).join('');
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-red-400 text-xs">Failed to load user directory: ${escapeHtml(err.message)}</td></tr>`;
      }
    }

    async function loadSessionAuditLogs() {
      const tbody = document.getElementById('users-audit-tbody');
      const search = (document.getElementById('analytics-search')?.value || '').trim();

      const params = new URLSearchParams({
        page: analyticsAuditPage,
        page_size: analyticsAuditPageSize,
        search: search
      });

      try {
        const res = await fetch(`/api/admin/analytics/login-logs?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        analyticsAuditTotal = data.total_count || 0;
        analyticsAuditTotalPages = data.total_pages || 1;
        analyticsAuditPage = data.page || 1;

        updatePaginationFooter(analyticsAuditPage, analyticsAuditTotalPages, analyticsAuditTotal, analyticsAuditPageSize, 'sessions');

        const items = data.items || [];
        if (items.length === 0) {
          tbody.innerHTML = `<tr><td colspan="7" class="py-12 text-center text-neutral-500">No session audit logs recorded.</td></tr>`;
          return;
        }

        tbody.innerHTML = items.map(log => {
          const profile = log.profiles || {};
          const email = profile.email || log.user_id || 'Anonymous User';
          const name = profile.display_name || email.split('@')[0];

          const loginTime = log.logged_in_at ? new Date(log.logged_in_at).toLocaleString() : '--';
          const rawSiteSec = parseInt(log.session_site_seconds || 0);
          const rawWatchSec = parseInt(log.session_watch_seconds || 0);
          const isAct = Boolean(log.session_state === 'Active Session' || log.is_active_session);
          const siteDur = rawSiteSec > 0 ? formatDuration(rawSiteSec) : (isAct ? '< 1m' : '0s');
          const watchDur = formatDuration(rawWatchSec);

          const ipv4 = log.ipv4_address || log.ip_address || '';
          const ipv6 = log.ipv6_address || '';

          const locCountry = log.country || 'Local Host';
          const locCity = log.city || '';
          const locText = locCity ? `${locCity}, ${locCountry}` : locCountry;

          const isLoggedOut = Boolean(log.session_state === 'Signed Out' || log.logged_out_at);
          let statusBadge = '';
          if (isAct) {
            statusBadge = `
              <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                <span>Active Session</span>
              </span>
            `;
          } else if (isLoggedOut) {
            statusBadge = `
              <span class="px-2 py-0.5 rounded text-[10px] font-medium bg-neutral-800 text-neutral-400 border border-neutral-700">Signed Out</span>
            `;
          } else {
            statusBadge = `
              <span class="px-2 py-0.5 rounded text-[10px] font-medium bg-neutral-800/80 text-neutral-400 border border-neutral-700/60">Session Ended</span>
            `;
          }

          return `
            <tr class="hover:bg-[#181818]/90 transition">
              <td class="py-3 px-3.5">
                <div class="font-bold text-white text-xs truncate max-w-[180px]">${escapeHtml(email)}</div>
                <div class="text-[10px] text-neutral-400 truncate max-w-[180px]">${escapeHtml(name)}</div>
              </td>
              <td class="py-3 px-3 whitespace-nowrap text-neutral-300 font-mono text-[11px]">
                ${escapeHtml(loginTime)}
              </td>
              <td class="py-3 px-3 whitespace-nowrap text-xs">
                <div>Site: <span class="font-mono text-cyan-300 font-semibold">${siteDur}</span></div>
                <div>Stream: <span class="font-mono text-purple-300 font-semibold">${watchDur}</span></div>
              </td>
              <td class="py-3 px-3 whitespace-nowrap space-y-1">
                ${ipv4 ? `<div class="font-mono text-[10px] text-emerald-300 select-all">${escapeHtml(ipv4)}</div>` : ''}
                ${ipv6 ? `<div class="font-mono text-[10px] text-cyan-300 truncate max-w-[120px] select-all">${escapeHtml(ipv6)}</div>` : ''}
              </td>
              <td class="py-3 px-3 whitespace-nowrap text-neutral-300 text-xs">
                ${escapeHtml(locText)}
              </td>
              <td class="py-3 px-3 whitespace-nowrap text-xs text-neutral-300">
                <div>${escapeHtml(log.os_name || 'Unknown OS')} &bull; ${escapeHtml(log.browser || 'Browser')}</div>
                <div class="text-[10px] text-neutral-500">${escapeHtml(log.device_type || 'Desktop')}</div>
              </td>
              <td class="py-3 px-3.5 text-right whitespace-nowrap">
                ${statusBadge}
              </td>
            </tr>
          `;
        }).join('');
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" class="py-8 text-center text-red-400 text-xs">Failed to load session logs: ${escapeHtml(err.message)}</td></tr>`;
      }
    }

    function updatePaginationFooter(page, totalPages, totalCount, pageSize, unitLabel) {
      const infoEl = document.getElementById('analytics-pagination-info');
      const pageDisplay = document.getElementById('analytics-page-display');
      const btnFirst = document.getElementById('btn-page-first');
      const btnPrev = document.getElementById('btn-page-prev');
      const btnNext = document.getElementById('btn-page-next');
      const btnLast = document.getElementById('btn-page-last');

      if (totalCount === 0) {
        if (infoEl) infoEl.innerText = `Showing 0 of 0 ${unitLabel}`;
        if (pageDisplay) pageDisplay.innerText = 'Page 1 of 1';
        if (btnFirst) btnFirst.disabled = true;
        if (btnPrev) btnPrev.disabled = true;
        if (btnNext) btnNext.disabled = true;
        if (btnLast) btnLast.disabled = true;
        return;
      }

      const start = (page - 1) * pageSize + 1;
      const end = Math.min(page * pageSize, totalCount);

      if (infoEl) infoEl.innerText = `Showing ${start.toLocaleString()}–${end.toLocaleString()} of ${totalCount.toLocaleString()} ${unitLabel}`;
      if (pageDisplay) pageDisplay.innerText = `Page ${page} of ${totalPages}`;

      if (btnFirst) btnFirst.disabled = (page <= 1);
      if (btnPrev) btnPrev.disabled = (page <= 1);
      if (btnNext) btnNext.disabled = (page >= totalPages);
      if (btnLast) btnLast.disabled = (page >= totalPages);
    }

    async function openUserDetailModal(userId) {
      const modal = document.getElementById('user-detail-modal');
      if (!modal) return;
      modal.classList.remove('hidden');
      modal.classList.add('flex');

      // Set loading state
      document.getElementById('detail-user-email').innerText = 'Loading user profile...';
      document.getElementById('detail-user-name').innerText = '--';
      document.getElementById('detail-user-id').innerText = userId;
      document.getElementById('detail-watch-list').innerHTML = '<div class="text-neutral-500 py-3 text-center">Fetching user history...</div>';
      document.getElementById('detail-session-list').innerHTML = '<div class="text-neutral-500 py-3 text-center">Fetching session logs...</div>';

      try {
        const res = await fetch(`/api/admin/analytics/users/${userId}/detail`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const p = data.profile || {};

        const email = p.email || 'Anonymous User';
        const name = p.display_name || email.split('@')[0];
        const initial = (name[0] || 'U').toUpperCase();

        document.getElementById('detail-user-avatar').innerText = initial;
        document.getElementById('detail-user-email').innerText = email;
        document.getElementById('detail-user-name').innerText = name;
        document.getElementById('detail-user-joined').innerText = p.first_joined_at ? new Date(p.first_joined_at).toLocaleDateString() : '--';
        document.getElementById('detail-user-id').innerText = p.id || userId;

        // Status badge
        const statusEl = document.getElementById('detail-user-status');
        if (statusEl) {
          if (p.is_online) {
            statusEl.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40';
            statusEl.innerText = p.current_activity && p.current_activity.startsWith('Watching:') ? 'Streaming Now' : 'Online Now';
          } else {
            statusEl.className = 'px-2 py-0.5 rounded text-[10px] font-medium bg-neutral-800 text-neutral-400 border border-neutral-700';
            statusEl.innerText = `Offline (${formatRelativeTime(p.last_seen_at)})`;
          }
        }

        // Stats
        const watchSec = parseInt(p.total_watch_seconds || 0);
        const siteSec = parseInt(p.total_site_seconds || 0);
        const ratio = siteSec > 0 ? Math.min(100, Math.round((watchSec / siteSec) * 100)) : 0;

        document.getElementById('detail-stat-watch').innerText = formatDuration(watchSec);
        document.getElementById('detail-stat-site').innerText = formatDuration(siteSec);
        document.getElementById('detail-stat-ratio').innerText = `${ratio}%`;
        document.getElementById('detail-stat-logins').innerText = (p.total_logins || 0).toLocaleString();

        // Network
        document.getElementById('detail-ipv4').innerText = p.last_ipv4 || 'None';
        document.getElementById('detail-ipv6').innerText = p.last_ipv6 || 'None';
        const loc = p.last_city ? `${p.last_city}, ${p.last_country || 'Unknown'}` : (p.last_country || 'Local Host');
        document.getElementById('detail-location').innerText = loc;
        document.getElementById('detail-device-browser').innerText = `${p.last_device || 'Desktop'} • ${p.last_browser || 'Browser'}`;

        // Section A: Watch Progress
        const watchItems = data.recent_watch_progress || [];
        const watchCountEl = document.getElementById('detail-watch-count');
        if (watchCountEl) watchCountEl.innerText = `${watchItems.length} titles`;

        const watchCont = document.getElementById('detail-watch-list');
        if (watchItems.length === 0) {
          watchCont.innerHTML = '<div class="text-neutral-500 py-3 text-xs italic">No playback history recorded for this user yet.</div>';
        } else {
          watchCont.innerHTML = watchItems.map(item => `
            <div class="bg-black/50 border border-neutral-800 p-2.5 rounded-xl space-y-1.5">
              <div class="flex items-center justify-between text-xs">
                <span class="font-bold text-white">${escapeHtml(item.media_title || 'Media Title')}</span>
                <span class="font-mono text-purple-400 font-semibold">${item.completion_percent}%</span>
              </div>
              ${item.episode_title ? `<div class="text-[10px] text-neutral-400">${escapeHtml(item.episode_title)}</div>` : ''}
              <div class="w-full bg-neutral-800 rounded-full h-1 overflow-hidden">
                <div class="bg-red-600 h-1 rounded-full" style="width: ${item.completion_percent}%"></div>
              </div>
              <div class="text-[9px] text-neutral-500 font-mono">Last active: ${formatRelativeTime(item.updated_at)}</div>
            </div>
          `).join('');
        }

        // Section B: Recent Sessions
        const sessions = data.recent_sessions || [];
        const sessCountEl = document.getElementById('detail-session-count');
        if (sessCountEl) sessCountEl.innerText = `${sessions.length} sessions`;

        const sessCont = document.getElementById('detail-session-list');
        if (sessions.length === 0) {
          sessCont.innerHTML = '<div class="text-neutral-500 py-3 text-xs italic">No session logs found.</div>';
        } else {
          sessCont.innerHTML = sessions.map(s => {
            const isAct = Boolean(s.session_state === 'Active Session' || s.is_active_session);
            const isOut = Boolean(s.session_state === 'Signed Out' || s.logged_out_at);
            const badgeLabel = isAct ? 'Active' : (isOut ? 'Logged out' : 'Ended');
            const badgeCls = isAct ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/40' : (isOut ? 'bg-neutral-800 text-neutral-400' : 'bg-neutral-800/80 text-neutral-500');
            const rawSite = parseInt(s.session_site_seconds || 0);
            const durText = rawSite > 0 ? formatDuration(rawSite) : (isAct ? '< 1m' : '0s');

            return `
            <div class="bg-black/50 border border-neutral-800 p-2 rounded-lg flex items-center justify-between text-xs">
              <div>
                <div class="text-neutral-300 font-medium">${new Date(s.logged_in_at).toLocaleString()}</div>
                <div class="text-[10px] text-neutral-500">${escapeHtml(s.ipv4_address || s.ip_address || '127.0.0.1')} &bull; ${escapeHtml(s.country || 'Local Host')}</div>
              </div>
              <div class="text-right">
                <div class="font-mono text-cyan-300 text-[11px]">${durText}</div>
                <span class="text-[9px] px-1.5 py-0.5 rounded ${badgeCls}">${badgeLabel}</span>
              </div>
            </div>
            `;
          }).join('');
        }
      } catch (err) {
        showAdminToast({ type: 'error', title: 'Inspection Failed', message: err.message });
        closeUserDetailModal();
      }
    }

    function closeUserDetailModal() {
      const modal = document.getElementById('user-detail-modal');
      if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
      }
    }

"""
