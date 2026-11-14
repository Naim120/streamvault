import { SystemCapabilities, SystemSettings } from '../types';
import { supabase } from './supabase';

// Pure single-origin root-relative routing: All /api and /media requests flow to the
// current origin. Vite dev server proxies to :8000 locally, and Nginx proxies in production.
const API_BASE = '';
const getOrigin = () => (typeof window !== 'undefined' ? window.location.origin : 'http://localhost');
const getAdminSecret = (providedSecret?: string) => providedSecret || '';

export const api = {
  // 1. Streaming Token & URLs
  async getStreamToken(mediaId: string, userId?: string) {
    const url = new URL('/api/stream/token', getOrigin());
    url.searchParams.set('media_id', mediaId);
    if (userId) url.searchParams.set('user_id', userId);

    const headers: Record<string, string> = {};
    try {
      const { data } = await supabase.auth.getSession();
      if (data?.session?.access_token) {
        headers['Authorization'] = `Bearer ${data.session.access_token}`;
      }
    } catch {}

    const res = await fetch(url.toString(), { headers });
    if (!res.ok) throw new Error('Failed to generate streaming token');
    return res.json() as Promise<{
      media_id: string;
      resolved_id: string;
      token: string;
      expires: number;
      user_id: string;
      duration_seconds?: number;
      resume_position_seconds?: number;
      saved_preferences?: {
        audio_index?: number | null;
        audio_lang?: string | null;
        audio_label?: string | null;
        subtitle_index?: number | null;
        subtitle_lang?: string | null;
        subtitle_label?: string | null;
      } | null;
      resolved_episode?: any;
      audio_tracks?: Array<{
        index: number;
        id: number | string;
        label: string;
        lang: string;
        is_default?: boolean;
        channels?: number;
      }>;
      subtitle_tracks?: Array<{
        index: number;
        id: string;
        label: string;
        lang: string;
        url?: string;
        vtt_path?: string;
        vtt_filename?: string;
      }>;
      available_qualities?: string[];
    }>;
  },

  async getStreamTracks(targetId: string) {
    const res = await fetch(`${API_BASE}/api/metadata/streams/${targetId}/tracks`);
    if (!res.ok) throw new Error('Failed to fetch stream tracks');
    return res.json() as Promise<{
      target_id: string;
      audio_tracks: Array<{
        index: number;
        id: number | string;
        label: string;
        lang: string;
        is_default?: boolean;
        channels?: number;
      }>;
      subtitle_tracks: Array<{
        index: number;
        id: string;
        label: string;
        lang: string;
        url?: string;
        vtt_path?: string;
        vtt_filename?: string;
      }>;
      duration_seconds: number;
    }>;
  },

  getManifestUrl(targetId: string, token: string, expires: number, uid: string) {
    return `/api/stream/${targetId}/master.m3u8?token=${token}&expires=${expires}&uid=${uid}`;
  },

  getSubtitleUrl(targetId: string, subPath: string, token: string, expires: number, uid: string) {
    const cleanPath = subPath.startsWith('/') ? subPath.slice(1) : subPath;
    return `/api/stream/${targetId}/${cleanPath}?token=${token}&expires=${expires}&uid=${uid}`;
  },

  // 2. Login Telemetry
  async recordLoginTelemetry(payload: { user_id: string; email?: string; device_type?: string }) {
    try {
      await fetch(`${API_BASE}/api/telemetry/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } catch (e) {
      console.warn('Telemetry recording non-fatal error:', e);
    }
  },

  // 3. System Hardware & Transcode Settings
  async getCapabilities(): Promise<SystemCapabilities> {
    const res = await fetch(`${API_BASE}/api/system/capabilities`);
    return res.json();
  },

  async getSettings(): Promise<SystemSettings> {
    const res = await fetch(`${API_BASE}/api/system/settings`);
    return res.json();
  },

  async updateSettings(payload: Partial<SystemSettings>, adminSecret?: string) {
    const res = await fetch(`${API_BASE}/api/system/settings`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'X-Admin-Secret': getAdminSecret(adminSecret)
      },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error('Failed to update system settings');
    return res.json();
  },

  async getQueueStatus() {
    const res = await fetch(`${API_BASE}/api/system/status`);
    return res.json();
  },

  // 4. TMDB & Catalog Ingestion
  async searchTMDB(query: string, type: 'movie' | 'tv' | 'multi' = 'multi') {
    const url = new URL('/api/metadata/search', getOrigin());
    url.searchParams.set('query', query);
    url.searchParams.set('type', type);
    const res = await fetch(url.toString());
    return res.json();
  },

  async getTMDBDetails(tmdbId: number, type: 'movie' | 'series' = 'movie') {
    const res = await fetch(`${API_BASE}/api/metadata/details/${tmdbId}?type=${type}`);
    if (!res.ok) throw new Error('Details not found');
    return res.json();
  },

  async listIncomingFiles(adminSecret?: string) {
    const res = await fetch(`${API_BASE}/api/metadata/incoming-files`, {
      headers: { 'X-Admin-Secret': getAdminSecret(adminSecret) }
    });
    return res.json();
  },

  async triggerManualIngest(payload: {
    filename: string;
    tmdb_id: number;
    media_type: 'movie' | 'series';
    season_number?: number;
    episode_number?: number;
  }, adminSecret?: string) {
    const res = await fetch(`${API_BASE}/api/metadata/ingest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Admin-Secret': getAdminSecret(adminSecret)
      },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error('Failed to start media ingest');
    return res.json();
  },

  async updateBadge(mediaId: string, badgeType: string, adminSecret?: string) {
    const res = await fetch(`${API_BASE}/api/metadata/badge/${mediaId}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'X-Admin-Secret': getAdminSecret(adminSecret)
      },
      body: JSON.stringify({ badge_type: badgeType })
    });
    return res.json();
  },

  async deleteMedia(mediaId: string, adminSecret?: string) {
    const res = await fetch(`${API_BASE}/api/metadata/media/${mediaId}`, {
      method: 'DELETE',
      headers: { 'X-Admin-Secret': getAdminSecret(adminSecret) }
    });
    return res.json();
  },

  // 5. Aria2 Downloader Controls
  async getAria2Stats(adminSecret?: string) {
    const res = await fetch(`${API_BASE}/api/aria2/stats`, {
      headers: { 'X-Admin-Secret': getAdminSecret(adminSecret) }
    });
    return res.json();
  },

  async listAria2Downloads(adminSecret?: string) {
    const res = await fetch(`${API_BASE}/api/aria2/downloads`, {
      headers: { 'X-Admin-Secret': getAdminSecret(adminSecret) }
    });
    return res.json();
  },

  async addAria2Download(data: FormData | string, meta?: { title?: string; type?: string }, adminSecret?: string) {
    if (typeof data === 'string') {
      const res = await fetch(`${API_BASE}/api/aria2/add`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Admin-Secret': getAdminSecret(adminSecret)
        },
        body: JSON.stringify({ uri: data, title: meta?.title, type: meta?.type })
      });
      if (!res.ok) throw new Error('Failed to submit download to Aria2');
      return res.json();
    } else {
      const res = await fetch(`${API_BASE}/api/aria2/add`, {
        method: 'POST',
        headers: { 'X-Admin-Secret': getAdminSecret(adminSecret) },
        body: data
      });
      if (!res.ok) throw new Error('Failed to submit download to Aria2');
      return res.json();
    }
  },

  async aria2Action(action: 'pause' | 'unpause' | 'remove', gid: string, adminSecret?: string) {
    const method = action === 'remove' ? 'DELETE' : 'POST';
    const res = await fetch(`${API_BASE}/api/aria2/${action}/${gid}`, {
      method,
      headers: { 'X-Admin-Secret': getAdminSecret(adminSecret) }
    });
    return res.json();
  },

  // 6. Security-Guarded Auth Endpoints
  async forgotPassword(email: string, honeypot: string = '') {
    const origin = typeof window !== 'undefined' ? window.location.origin : undefined;
    const res = await fetch(`${API_BASE}/api/auth/forgot-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.trim().toLowerCase(), _honeypot: honeypot, origin, client_origin: origin })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const retryHdr = res.headers.get('Retry-After') || res.headers.get('X-Retry-After-Seconds');
      const err: any = new Error(data.detail || 'Failed to process forgot password request');
      err.status = res.status;
      err.retryAfterSeconds = data.retry_after_seconds || (retryHdr ? parseInt(retryHdr, 10) : undefined);
      err.attemptsRemaining = data.attempts_remaining;
      err.maxAttemptsReached = data.max_attempts_reached;
      throw err;
    }
    return data as {
      status: 'sent' | 'already_completed';
      message: string;
      cooldown_seconds: number;
      attempts_remaining?: number;
      max_attempts_reached?: boolean;
      reset_flow_token?: string;
      password_reset_completed?: boolean;
    };
  },

  async notifyPasswordResetComplete(email?: string, accessToken?: string) {
    let token = accessToken;
    if (!token) {
      try {
        const { data } = await supabase.auth.getSession();
        token = data?.session?.access_token;
      } catch {}
    }
    if (!token) return null;

    const res = await fetch(`${API_BASE}/api/auth/password-reset-complete`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({ email: email ? email.trim().toLowerCase() : undefined }),
    });
    if (!res.ok) return null;
    return res.json() as Promise<{ status: string; password_reset_completed: boolean }>;
  },

  async getResetFlowStatus(email: string, resetFlowToken: string) {
    const res = await fetch(`${API_BASE}/api/auth/reset-flow-status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: email.trim().toLowerCase(),
        reset_flow_token: resetFlowToken,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err: any = new Error(data.detail || 'Failed to check reset flow status');
      err.status = res.status;
      throw err;
    }
    return data as {
      password_reset_completed: boolean;
      attempts_remaining: number;
      max_attempts_reached: boolean;
      cooldown_remaining_seconds: number;
    };
  },

  async signupPrecheck(email: string) {
    const res = await fetch(`${API_BASE}/api/auth/signup-precheck`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.trim().toLowerCase() })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err: any = new Error(data.detail || 'Failed to inspect email verification state');
      err.status = res.status;
      throw err;
    }
    return data as {
      status: 'already_verified' | 'unverified_expired_resent' | 'unverified_existing' | 'unverified_cooldown' | 'new_user';
      exists: boolean;
      is_verified: boolean;
      email_sent?: boolean;
      cooldown_remaining_seconds: number;
      last_sent_seconds_ago?: number;
      message?: string;
    };
  },

  async resendVerification(email: string, honeypot: string = '', redirectTo?: string) {
    const origin = typeof window !== 'undefined' ? window.location.origin : undefined;
    const safeRedirect = redirectTo || (origin && !origin.includes('localhost') && !origin.includes('127.0.0.1') ? `${origin}/home` : undefined);
    const res = await fetch(`${API_BASE}/api/auth/resend-verification`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: email.trim().toLowerCase(),
        _honeypot: honeypot,
        origin,
        client_origin: origin,
        redirect_to: safeRedirect
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const retryHdr = res.headers.get('Retry-After');
      const err: any = new Error(data.detail || 'Failed to resend verification email');
      err.status = res.status;
      err.cooldownRemainingSeconds = data.cooldown_remaining_seconds || (retryHdr ? parseInt(retryHdr, 10) : 120);
      throw err;
    }
    return data as {
      status: string;
      cooldown_remaining_seconds: number;
      message: string;
      is_verified?: boolean;
    };
  },

  async getVerificationStatus(email: string) {
    const res = await fetch(`${API_BASE}/api/auth/verification-status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.trim().toLowerCase() })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err: any = new Error(data.detail || 'Failed to check verification status');
      err.status = res.status;
      throw err;
    }
    return data as {
      exists: boolean;
      is_verified: boolean;
      cooldown_remaining_seconds: number;
      last_sent_seconds_ago?: number;
    };
  },

  async getRedirectOrigin(): Promise<{ origin: string }> {
    const res = await fetch(`${API_BASE}/api/auth/redirect-origin`);
    if (!res.ok) throw new Error('Failed to fetch redirect origin');
    return res.json() as Promise<{ origin: string }>;
  }
};
