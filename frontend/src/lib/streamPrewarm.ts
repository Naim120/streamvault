import { api } from './api';

export interface PrewarmedStreamData {
  targetId: string;
  tokenData: {
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
  };
  manifestUrl: string;
  tracks?: {
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
  };
  duration_seconds: number;
  timestamp: number;
}

const PREWARM_CACHE = new Map<string, PrewarmedStreamData>();
const IN_FLIGHT_PROMISES = new Map<string, Promise<PrewarmedStreamData | null>>();
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes TTL

function makeCacheKey(targetId: string, userId?: string | null): string {
  const uid = userId || 'anonymous';
  return `${uid}:${targetId}`;
}

export function getPrewarmedStream(targetId: string, userId?: string | null): PrewarmedStreamData | null {
  if (!targetId) return null;
  const key = makeCacheKey(targetId, userId);
  const cached = PREWARM_CACHE.get(key);
  if (!cached) return null;
  if (Date.now() - cached.timestamp > CACHE_TTL_MS) {
    PREWARM_CACHE.delete(key);
    return null;
  }
  return cached;
}

export async function prewarmStream(targetId: string, userId?: string | null): Promise<PrewarmedStreamData | null> {
  if (!targetId) return null;
  const key = makeCacheKey(targetId, userId);
  
  const existing = getPrewarmedStream(targetId, userId);
  if (existing) return existing;

  if (IN_FLIGHT_PROMISES.has(key)) {
    return IN_FLIGHT_PROMISES.get(key)!;
  }

  const promise = (async () => {
    try {
      const tokenData = await api.getStreamToken(targetId, userId || undefined).catch(() => null);
      if (!tokenData) return null;

      const actualId = tokenData.resolved_id || targetId;
      const manifestUrl = api.getManifestUrl(
        actualId,
        tokenData.token,
        tokenData.expires,
        tokenData.user_id
      );

      // Speculative HTTP cache fill for master.m3u8 manifest and video variant playlist
      if (typeof window !== 'undefined' && window.fetch) {
        window.fetch(manifestUrl).then((res) => {
          if (res.ok) {
            const variantUrl = `/api/stream/${actualId}/video/playlist.m3u8?token=${tokenData.token}&expires=${tokenData.expires}&uid=${tokenData.user_id}`;
            window.fetch(variantUrl).catch(() => {});
          }
        }).catch(() => {});
      }

      const duration = (tokenData.duration_seconds && tokenData.duration_seconds > 0)
        ? tokenData.duration_seconds
        : 0;

      const prewarmed: PrewarmedStreamData = {
        targetId: actualId,
        tokenData,
        manifestUrl,
        tracks: {
          audio_tracks: tokenData.audio_tracks || [],
          subtitle_tracks: tokenData.subtitle_tracks || [],
          duration_seconds: duration
        },
        duration_seconds: duration,
        timestamp: Date.now()
      };

      PREWARM_CACHE.set(key, prewarmed);
      if (actualId !== targetId) {
        PREWARM_CACHE.set(makeCacheKey(actualId, userId), prewarmed);
      }
      return prewarmed;
    } catch {
      return null;
    } finally {
      IN_FLIGHT_PROMISES.delete(key);
    }
  })();

  IN_FLIGHT_PROMISES.set(key, promise);
  return promise;
}

export function invalidatePrewarmCache(targetId?: string, userId?: string | null) {
  if (targetId) {
    if (userId) {
      PREWARM_CACHE.delete(makeCacheKey(targetId, userId));
    } else {
      for (const k of Array.from(PREWARM_CACHE.keys())) {
        if (k.endsWith(`:${targetId}`)) {
          PREWARM_CACHE.delete(k);
        }
      }
    }
  } else {
    PREWARM_CACHE.clear();
    IN_FLIGHT_PROMISES.clear();
  }
}
