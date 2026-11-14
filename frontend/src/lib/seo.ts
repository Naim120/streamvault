export interface PageSeoOptions {
  title?: string;
  description?: string;
  canonicalPath?: string;
  noIndex?: boolean;
  ogImage?: string;
}

const DEFAULT_TITLE = 'StreamVault — Watch Movies, TV Series & Originals in HD';
const DEFAULT_DESCRIPTION =
  'StreamVault is a high-performance streaming platform featuring adaptive 1080p HD playback, multi-language audio tracks, custom subtitles, and instant cross-device sync.';

function setMetaTag(selector: string, attribute: string, value: string): void {
  if (typeof document === 'undefined') return;
  let element = document.querySelector(selector) as HTMLMetaElement | null;
  if (!element) {
    element = document.createElement('meta');
    if (selector.startsWith('meta[name=')) {
      const match = selector.match(/name="([^"]+)"/);
      if (match) element.setAttribute('name', match[1]);
    } else if (selector.startsWith('meta[property=')) {
      const match = selector.match(/property="([^"]+)"/);
      if (match) element.setAttribute('property', match[1]);
    }
    document.head.appendChild(element);
  }
  element.setAttribute(attribute, value);
}

function setCanonical(url: string): void {
  if (typeof document === 'undefined') return;
  let link = document.querySelector('link[rel="canonical"]') as HTMLLinkElement | null;
  if (!link) {
    link = document.createElement('link');
    link.setAttribute('rel', 'canonical');
    document.head.appendChild(link);
  }
  link.setAttribute('href', url);
}

export function updatePageSeo(options: PageSeoOptions): void {
  if (typeof window === 'undefined') return;

  const title = options.title ? options.title : DEFAULT_TITLE;
  const description = options.description ? options.description : DEFAULT_DESCRIPTION;
  const canonicalUrl = `${window.location.origin}${options.canonicalPath || window.location.pathname}`;

  // 1. Title
  document.title = title;

  // 2. Meta description
  setMetaTag('meta[name="description"]', 'content', description);

  // 3. Robots
  const robotsValue = options.noIndex ? 'noindex, nofollow' : 'index, follow, max-image-preview:large';
  setMetaTag('meta[name="robots"]', 'content', robotsValue);

  // 4. Open Graph
  setMetaTag('meta[property="og:title"]', 'content', title);
  setMetaTag('meta[property="og:description"]', 'content', description);
  setMetaTag('meta[property="og:url"]', 'content', canonicalUrl);
  if (options.ogImage) {
    setMetaTag('meta[property="og:image"]', 'content', options.ogImage);
  }

  // 5. Twitter
  setMetaTag('meta[name="twitter:title"]', 'content', title);
  setMetaTag('meta[name="twitter:description"]', 'content', description);
  if (options.ogImage) {
    setMetaTag('meta[name="twitter:image"]', 'content', options.ogImage);
  }

  // 6. Canonical link
  setCanonical(canonicalUrl);
}

