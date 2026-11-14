/**
 * StreamVault Production Client Telemetry & Dual-Stack IP Probe
 */

import { resolveApiUrl } from './serverDiscovery';
import { supabase } from './supabase';

export interface DualStackIPs {
  ipv4: string | null;
  ipv6: string | null;
}

const STORAGE_KEY_SESSION_ID = 'streamvault_telemetry_session_id';

/**
 * Dual-Stack IP resolution:
 * StreamVault's backend automatically resolves and validates the client's public
 * IPv4 and IPv6 addresses directly from Cloudflare (CF-Connecting-IP, CF-Connecting-IPv6)
 * and Nginx gateway headers. We avoid external third-party queries (api6.ipify.org) to eliminate
 * ERR_NAME_NOT_RESOLVED console errors on networks without IPv6 routing.
 */
export async function probeDualStackIPs(): Promise<DualStackIPs> {
  return { ipv4: null, ipv6: null };
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

