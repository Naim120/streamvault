import { extractSafeTrailer } from './security';

export type TrailerStatus = 'playable' | 'blocked' | 'unknown';

const STORAGE_KEY = 'sv_trailer_status_v1';
const memoryCache = new Map<string, 'playable' | 'blocked'>();

// Hydrate initial status from sessionStorage
try {
  if (typeof window !== 'undefined' && window.sessionStorage) {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        Object.entries(parsed).forEach(([k, v]) => {
          if (v === 'playable' || v === 'blocked') {
            memoryCache.set(k, v);
          }
        });
      }
    }
  }
} catch {
  // Ignore sessionStorage errors (e.g. security sandboxing or quota)
}

function persistCache(): void {
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      const obj: Record<string, 'playable' | 'blocked'> = {};
      memoryCache.forEach((status, src) => {
        obj[src] = status;
      });
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
    }
  } catch {
    // Ignore storage quota errors
  }
}

/**
 * Returns the current known status of a trailer source ('playable' | 'blocked' | 'unknown').
 */
export function getTrailerStatus(src?: string | null): TrailerStatus {
  if (!src) return 'unknown';
  const clean = src.trim();
  if (!clean) return 'unknown';
  return memoryCache.get(clean) || 'unknown';
}

/**
 * Synchronous check whether a trailer source is known to be blocked.
 */
export function isTrailerBlocked(src?: string | null): boolean {
  return getTrailerStatus(src) === 'blocked';
}

/**
 * Synchronous check whether a trailer source is confirmed playable.
 */
export function isTrailerPlayable(src?: string | null): boolean {
  return getTrailerStatus(src) === 'playable';
}

/**
 * Marks a trailer source as blocked (e.g. bot-check wall, deleted, private, or embedding disabled).
 * Broadcasts 'sv:trailer-status' event to all components.
 */
export function markTrailerBlocked(src?: string | null): void {
  if (!src) return;
  const clean = src.trim();
  if (!clean) return;

  if (memoryCache.get(clean) === 'blocked') return;
  memoryCache.set(clean, 'blocked');
  persistCache();

  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('sv:trailer-status', {
        detail: { src: clean, status: 'blocked' }
      })
    );
  }
}

/**
 * Marks a trailer source as verified playable (e.g. YouTube confirmed state === 1 PLAYING).
 * Broadcasts 'sv:trailer-status' event to all components.
 */
export function markTrailerPlayable(src?: string | null): void {
  if (!src) return;
  const clean = src.trim();
  if (!clean) return;

  if (memoryCache.get(clean) === 'playable') return;
  memoryCache.set(clean, 'playable');
  persistCache();

  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('sv:trailer-status', {
        detail: { src: clean, status: 'playable' }
      })
    );
  }
}

// Track active background pre-validations to avoid duplicate network fetches
const inflightValidations = new Set<string>();

/**
 * Fast background pre-validation probe.
 * Checks YouTube's lightweight oEmbed endpoint to immediately catch deleted,
 * private, or embed-restricted trailers before the user even opens the modal.
 */
export async function prevalidateTrailer(rawKey?: string | null): Promise<void> {
  if (!rawKey) return;
  const trailer = extractSafeTrailer(rawKey);
  if (!trailer || !trailer.src) return;

  const currentStatus = getTrailerStatus(trailer.src);
  if (currentStatus === 'playable' || currentStatus === 'blocked') {
    return;
  }

  if (trailer.type === 'video') {
    // Direct videos are playable by default
    return;
  }

  if (trailer.type === 'youtube') {
    if (inflightValidations.has(trailer.src)) return;
    inflightValidations.add(trailer.src);

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 2500);

      const res = await fetch(
        `https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=${encodeURIComponent(trailer.src)}&format=json`,
        { signal: controller.signal }
      );
      clearTimeout(timeoutId);

      // 401, 403, or 404 indicates private, deleted, or embedding explicitly disabled
      if (res.status === 401 || res.status === 403 || res.status === 404) {
        markTrailerBlocked(trailer.src);
      }
    } catch {
      // Abort or network failure: do not permanently block, let runtime iframe player handle it
    } finally {
      inflightValidations.delete(trailer.src);
    }
  }
}
