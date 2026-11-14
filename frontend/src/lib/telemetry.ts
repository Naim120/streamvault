/**
 * StreamVault Production Client Telemetry & Dual-Stack IP Probe
 */

import { resolveApiUrl } from './serverDiscovery';
import { supabase } from './supabase';

export interface DualStackIPs {
  ipv4: string | null;
  ipv6: string | null;
}

const STORAGE_KEY_IPS = 'streamvault_dual_stack_ips';
const STORAGE_KEY_SESSION_ID = 'streamvault_telemetry_session_id';

async function fetchWithTimeout(url: string, timeoutMs: number = 2500): Promise<string | null> {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(id);
    if (!res.ok) return null;
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const data = await res.json();
      return (data?.ip || '').trim() || null;
    }
    const text = await res.text();
    return text.trim() || null;
  } catch {
    clearTimeout(id);
    return null;
  }
}

/**
 * Discovers dual-stack IPv4 and IPv6 addresses in parallel.
 * Results are cached in sessionStorage for the lifetime of the browser tab.
 */
export async function probeDualStackIPs(): Promise<DualStackIPs> {
  if (typeof window === 'undefined') {
    return { ipv4: null, ipv6: null };
  }

  try {
    const cached = sessionStorage.getItem(STORAGE_KEY_IPS);
    if (cached) {
      const parsed = JSON.parse(cached);
      if (parsed && (parsed.ipv4 || parsed.ipv6)) {
        return parsed;
      }
    }
  } catch {}

  const [v4Result, v6Result] = await Promise.allSettled([
    // IPv4 Probe with fallback
    fetchWithTimeout('https://api4.ipify.org?format=json', 2500)
      .then((ip) => ip || fetchWithTimeout('https://ipv4.icanhazip.com', 2500)),
    // IPv6 Probe with fallback
    fetchWithTimeout('https://api6.ipify.org?format=json', 2500)
      .then((ip) => ip || fetchWithTimeout('https://ipv6.icanhazip.com', 2500))
  ]);

  const ipv4 = v4Result.status === 'fulfilled' ? v4Result.value : null;
  const ipv6 = v6Result.status === 'fulfilled' ? v6Result.value : null;

  const result: DualStackIPs = { ipv4, ipv6 };
  try {
    sessionStorage.setItem(STORAGE_KEY_IPS, JSON.stringify(result));
  } catch {}

  return result;
}

export function getCachedSessionLogId(): string | null {
  if (typeof window === 'undefined') return null;
  return sessionStorage.getItem(STORAGE_KEY_SESSION_ID);
}

export function setCachedSessionLogId(id: string | null) {
  if (typeof window === 'undefined') return;
  if (id) {
    sessionStorage.setItem(STORAGE_KEY_SESSION_ID, id);
  } else {
    sessionStorage.removeItem(STORAGE_KEY_SESSION_ID);
  }
}

export function clearTelemetrySession() {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.removeItem(STORAGE_KEY_SESSION_ID);
    sessionStorage.removeItem(STORAGE_KEY_IPS);
  } catch {}
}

export async function getEffectiveToken(providedToken?: string | null): Promise<string | null> {
  if (providedToken && typeof providedToken === 'string' && providedToken.trim().length > 10) {
    return providedToken.trim();
  }
  try {
    const { data } = await supabase.auth.getSession();
    const token = data?.session?.access_token;
    if (token && typeof token === 'string' && token.trim().length > 10) {
      return token.trim();
    }
  } catch {}
  return null;
}

/**
 * Starts an authenticated telemetry session and stores session_log_id.
 */
export async function startTelemetrySession(
  accessToken?: string | null,
  email?: string
): Promise<string | null> {
  const token = await getEffectiveToken(accessToken);
  if (!token) return null;

  try {
    const ips = await probeDualStackIPs();
    const isMobile = typeof window !== 'undefined' && window.innerWidth < 768;
    const deviceType = isMobile ? 'Mobile' : 'Desktop';

    const res = await fetch(resolveApiUrl('/api/telemetry/session-start'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({
        client_ipv4: ips.ipv4,
        client_ipv6: ips.ipv6,
        device_type: deviceType,
        email
      })
    });

    if (res.ok) {
      const data = await res.json();
      const sessionId = data?.session_log_id || null;
      setCachedSessionLogId(sessionId);
      return sessionId;
    }
  } catch (err) {
    console.warn('Telemetry session start warning:', err);
  }
  return null;
}

let lastKnownCatalogVersion: number | null = null;
type CatalogVersionListener = (newVer: number) => void;
const catalogListeners = new Set<CatalogVersionListener>();

export function onCatalogVersionChange(listener: CatalogVersionListener) {
  catalogListeners.add(listener);
  return () => {
    catalogListeners.delete(listener);
  };
}

export function handleCatalogVersion(newVer: number | undefined | null) {
  if (typeof newVer !== 'number') return;
  if (lastKnownCatalogVersion !== null && newVer > lastKnownCatalogVersion) {
    catalogListeners.forEach(fn => {
      try {
        fn(newVer);
      } catch (e) {
        console.error('Catalog version listener error:', e);
      }
    });
  }
  lastKnownCatalogVersion = newVer;
}

/**
 * Sends a periodic activity and watch-time heartbeat.
 */
export async function sendTelemetryHeartbeat(
  accessToken: string | null | undefined,
  payload: {
    session_log_id?: string | null;
    delta_site_seconds: number;
    delta_watch_seconds: number;
    active_media_title?: string | null;
    client_ipv4?: string | null;
    client_ipv6?: string | null;
  }
): Promise<void> {
  let token = await getEffectiveToken(accessToken);
  if (!token) return;

  try {
    const ips = await probeDualStackIPs();
    const bodyStr = JSON.stringify({
      session_log_id: payload.session_log_id || getCachedSessionLogId(),
      delta_site_seconds: payload.delta_site_seconds,
      delta_watch_seconds: payload.delta_watch_seconds,
      active_media_title: payload.active_media_title,
      client_ipv4: payload.client_ipv4 || ips.ipv4,
      client_ipv6: payload.client_ipv6 || ips.ipv6
    });

    let res = await fetch(resolveApiUrl('/api/telemetry/heartbeat'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: bodyStr
    });

    // If heartbeat returns 401, refresh session and retry once
    if (res.status === 401) {
      try {
        const { data: refreshData } = await supabase.auth.refreshSession();
        if (refreshData?.session?.access_token) {
          token = refreshData.session.access_token;
          res = await fetch(resolveApiUrl('/api/telemetry/heartbeat'), {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${token}`
            },
            body: bodyStr
          });
        }
      } catch {}
    }

    if (res.ok) {
      const data = await res.json().catch(() => null);
      if (data && typeof data.catalog_version === 'number') {
        handleCatalogVersion(data.catalog_version);
      }
    }
  } catch (err) {
    console.warn('Telemetry heartbeat warning:', err);
  }
}

/**
 * Sends a logout signal.
 */
export async function sendTelemetryLogout(
  accessToken?: string | null,
  sessionLogId?: string | null,
  explicitSignout: boolean = true
): Promise<void> {
  const sessionId = sessionLogId || getCachedSessionLogId();
  setCachedSessionLogId(null);

  const token = await getEffectiveToken(accessToken);
  if (!token) return;

  const url = resolveApiUrl('/api/telemetry/logout');
  const body = JSON.stringify({
    session_log_id: sessionId,
    explicit_signout: explicitSignout
  });

  try {
    await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body,
      keepalive: true
    });
  } catch {
    // Non-fatal
  }
}

