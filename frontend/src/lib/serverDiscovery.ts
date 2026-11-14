import { supabase } from './supabase';

/**
 * Detects if the frontend is running inside a native Android TV / JioOS Capacitor APK
 * or standalone local webview wrapper rather than a standard web browser on a remote origin.
 */
export const isNativeApp = (): boolean => {
  if (typeof window === 'undefined') return false;
  const proto = window.location.protocol;
  const host = window.location.hostname;
  return (
    proto === 'capacitor:' ||
    (window as any).Capacitor?.isNativePlatform?.() === true ||
    ((host === 'localhost' || host === '127.0.0.1') && window.location.port === '')
  );
};

let activeApiBaseUrl: string = '';
let isDiscovering = false;
let discoveryPromise: Promise<string> | null = null;
let lastHeartbeatTime: string | null = null;

const PING_TIMEOUT_MS = 3500;

/**
 * Pings candidate URL to verify connectivity and StreamVault API signature.
 */
export const pingServer = async (candidateUrl: string, timeoutMs = PING_TIMEOUT_MS): Promise<boolean> => {
  const cleanUrl = candidateUrl.trim().replace(/\/+$/, '');
  if (!cleanUrl.startsWith('http://') && !cleanUrl.startsWith('https://')) return false;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(`${cleanUrl}/api/ping`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: controller.signal
    });
    clearTimeout(timer);
    if (!res.ok) return false;
    const data = await res.json().catch(() => null);
    return Boolean(data && data.status === 'ok' && data.service === 'streamvault');
  } catch {
    clearTimeout(timer);
    return false;
  }
};

/**
 * Initializes server resolution. In native Android TV / JioOS mode, resolves the
 * reachable backend URL dynamically via Supabase Registry or local storage cache.
 */
export const initServerDiscovery = async (force = false): Promise<string> => {
  if (!isNativeApp()) {
    activeApiBaseUrl = '';
    return '';
  }

  if (activeApiBaseUrl && !force) {
    return activeApiBaseUrl;
  }

  if (discoveryPromise && !force) {
    return discoveryPromise;
  }

  discoveryPromise = (async () => {
    isDiscovering = true;
    try {
      // 1. Check manual user override in localStorage
      const customUrl = localStorage.getItem('sv_custom_server_url')?.trim();
      if (customUrl) {
        const customOk = await pingServer(customUrl);
        if (customOk) {
          activeApiBaseUrl = customUrl.replace(/\/+$/, '');
          return activeApiBaseUrl;
        }
      }

      // 2. Query Supabase Public RPC: get_active_server_endpoint()
      const candidates: string[] = [];
      try {
        const { data, error } = await supabase.rpc('get_active_server_endpoint');
        if (!error && data) {
          const endpointData = typeof data === 'string' ? JSON.parse(data) : data;
          if (endpointData.server_heartbeat_at) {
            lastHeartbeatTime = endpointData.server_heartbeat_at;
          }
          if (endpointData.active_tunnel_url) candidates.push(endpointData.active_tunnel_url);
          if (endpointData.active_server_url) candidates.push(endpointData.active_server_url);
          if (endpointData.cloudflare_pages_url) candidates.push(endpointData.cloudflare_pages_url);
        }
      } catch (err) {
        console.warn('[Discovery] Supabase endpoint fetch warning:', err);
      }

      // 3. Fallbacks: Last known working URL & built-in env URL
      const lastKnown = localStorage.getItem('sv_last_known_server_url')?.trim();
      if (lastKnown && !candidates.includes(lastKnown)) {
        candidates.push(lastKnown);
      }

      const envUrl = (import.meta.env.VITE_MEDIA_SERVER_URL || '').trim();
      if (envUrl && !candidates.includes(envUrl)) {
        candidates.push(envUrl);
      }

      // Test candidate URLs in priority order
      for (const cand of candidates) {
        if (!cand) continue;
        const normalized = cand.trim().replace(/\/+$/, '');
        const isHealthy = await pingServer(normalized);
        if (isHealthy) {
          activeApiBaseUrl = normalized;
          localStorage.setItem('sv_last_known_server_url', normalized);
          console.info('[Discovery] Connected to active server:', normalized);
          return activeApiBaseUrl;
        }
      }

      // If no candidate passed ping test, fallback to last known or empty
      if (lastKnown) {
        activeApiBaseUrl = lastKnown.replace(/\/+$/, '');
      } else if (envUrl) {
        activeApiBaseUrl = envUrl.replace(/\/+$/, '');
      }
    } finally {
      isDiscovering = false;
      discoveryPromise = null;
    }

    return activeApiBaseUrl;
  })();

  return discoveryPromise;
};

/**
 * Automatically rewrites a root-relative path (e.g. /api/stream/token) to prepend
 * the active discovered server origin when running in a native TV app shell.
 */
export const resolveApiUrl = (path: string): string => {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('data:') || path.startsWith('blob:')) {
    return path;
  }

  const cleanPath = path.startsWith('/') ? path : `/${path}`;

  if (!isNativeApp()) {
    return cleanPath;
  }

  const base = activeApiBaseUrl || localStorage.getItem('sv_last_known_server_url') || '';
  return base ? `${base}${cleanPath}` : cleanPath;
};

/**
 * Returns current active API base URL.
 */
export const getActiveApiBaseUrl = (): string => activeApiBaseUrl;

/**
 * Saves or clears user custom server URL override.
 */
export const setCustomServerUrl = async (url: string | null): Promise<boolean> => {
  if (!url || !url.trim()) {
    localStorage.removeItem('sv_custom_server_url');
    await initServerDiscovery(true);
    return true;
  }

  const clean = url.trim().replace(/\/+$/, '');
  const ok = await pingServer(clean, 4000);
  if (ok) {
    localStorage.setItem('sv_custom_server_url', clean);
    activeApiBaseUrl = clean;
    return true;
  }
  return false;
};

export const getCustomServerUrl = (): string | null => {
  return localStorage.getItem('sv_custom_server_url') || null;
};

export const getServerStatus = () => ({
  isNative: isNativeApp(),
  activeUrl: activeApiBaseUrl || (isNativeApp() ? 'Connecting...' : window.location.origin),
  isCustom: Boolean(localStorage.getItem('sv_custom_server_url')),
  lastHeartbeat: lastHeartbeatTime,
  isDiscovering
});

/**
 * Background auto-rediscovery trigger on network or gateway errors (e.g. VPS migration).
 */
export const handleServerConnectionError = (err?: any) => {
  if (!isNativeApp() || isDiscovering) return;
  console.warn('[Discovery] Server error encountered, triggering background re-discovery...', err);
  initServerDiscovery(true).catch(() => {});
};

