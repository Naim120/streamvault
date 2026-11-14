/**
 * StreamVault Client-Side Security & Sanitization Utilities
 * Eliminates XSS, URI scheme hijacking, and PostgREST injection vectors.
 */

const SAFE_DATA_IMAGE_REGEX = /^data:image\/(png|jpe?g|webp|gif|avif);base64,[a-z0-9+/=\s]+$/i;
const YOUTUBE_KEY_REGEX = /^[a-zA-Z0-9_-]{6,15}$/;

/**
 * Strips all HTML/script tags and stray angle brackets from untrusted strings
 * (e.g. parsed WebVTT subtitles, user inputs) and decodes basic text entities.
 */
export function stripHtmlTags(rawText: string): string {
  if (!rawText) return '';
  return rawText
    .replace(/<[^>]*>?/gm, '') // Strips closed and unclosed HTML/VTT tags
    .replace(/[<>]/g, '')      // Removes any remaining stray angle brackets
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'");
}

/**
 * Validates and extracts a YouTube video key (including /shorts/ and /embed/).
 * Strictly verifies against ^[a-zA-Z0-9_-]{6,15}$ to prevent iframe URL injection.
 */
export function sanitizeYouTubeKey(keyOrUrl?: string | null): string | null {
  if (!keyOrUrl) return null;
  const str = keyOrUrl.trim();
  if (!str || str === 'null' || str === 'undefined' || str === 'none') return null;

  if (str.includes('youtube.com') || str.includes('youtu.be')) {
    const regExp = /(?:youtu\.be\/|youtube\.com\/(?:embed\/|v\/|shorts\/|u\/\w\/|watch\?v=|.*&v=))([^#&?]*)/;
    const match = str.match(regExp);
    if (match && YOUTUBE_KEY_REGEX.test(match[1])) {
      return match[1];
    }
    return null;
  }

  if (YOUTUBE_KEY_REGEX.test(str)) {
    return str;
  }

  return null;
}

/**
 * Helper to check if a URL uses an allowed origin/scheme (HTTPS, root-relative, or local loopback).
 */
function isAllowedMediaOrigin(url: string): boolean {
  const lower = url.toLowerCase();
  return (
    lower.startsWith('https://') ||
    lower.startsWith('http://localhost:') ||
    lower.startsWith('http://127.0.0.1:') ||
    (url.startsWith('/') && !url.startsWith('//'))
  );
}

/**
 * Resolves and validates a safe trailer source (YouTube embed or direct video file).
 * Strictly forbids javascript:, data:text/html, and unsafe schemes.
 */
export function extractSafeTrailer(keyOrUrl?: string | null): { type: 'youtube' | 'video'; src: string } | null {
  if (!keyOrUrl) return null;
  const str = keyOrUrl.trim();
  if (!str || str === 'null' || str === 'undefined' || str === 'none') return null;

  const ytKey = sanitizeYouTubeKey(str);
  if (ytKey) {
    return { type: 'youtube', src: ytKey };
  }

  // Strip query parameters (e.g. ?token=...) before checking file extension
  const cleanPath = str.split('?')[0].toLowerCase();
  const isVideoFile =
    cleanPath.endsWith('.mp4') ||
    cleanPath.endsWith('.webm') ||
    cleanPath.endsWith('.m3u8') ||
    cleanPath.includes('/stream/');

  if (isVideoFile && isAllowedMediaOrigin(str)) {
    return { type: 'video', src: str };
  }

  return null;
}

/**
 * Validates media URLs (poster_url, backdrop_url, still_url).
 * Allows HTTPS, root-relative paths, local dev loopback, and raster base64 images (blocks SVG XSS).
 */
export function sanitizeMediaUrl(url?: string | null): string | null {
  if (!url) return null;
  const trimmed = url.trim();
  if (!trimmed || trimmed === 'null' || trimmed === 'undefined') return null;

  const lower = trimmed.toLowerCase();

  // Explicitly block dangerous schemes and SVG data URIs
  if (
    lower.startsWith('javascript:') ||
    lower.startsWith('vbscript:') ||
    lower.startsWith('data:text/') ||
    lower.startsWith('data:application/') ||
    lower.startsWith('data:image/svg')
  ) {
    return null;
  }

  if (isAllowedMediaOrigin(trimmed)) {
    return trimmed;
  }

  // Only allow safe raster data:image/ URIs (no SVG)
  if (SAFE_DATA_IMAGE_REGEX.test(trimmed)) {
    return trimmed;
  }

  return null;
}

/**
 * Escapes regex metacharacters to safely construct RegExp from user tokens without ReDoS.
 */
export function escapeRegex(token: string): string {
  if (!token) return '';
  return token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Sanitizes user search strings against XSS, SQL/PostgREST injection, and ReDoS.
 * Strips control characters, HTML tags (< >), SQL/PostgREST operators (; ' " \ % * ( ) [ ]),
 * and caps length at 64 characters.
 */
export function sanitizeSearchInput(query: string): string {
  if (!query) return '';
  return query
    .slice(0, 64)
    .replace(/[\x00-\x1F\x7F]/g, '') // control chars
    .replace(/[<>[\](),*\\;"'%_]/g, ' ') // SQL/PostgREST/HTML operators
    .replace(/\s+/g, ' ')
    .trim();
}
