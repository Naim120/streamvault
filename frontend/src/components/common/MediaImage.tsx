import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Film } from 'lucide-react';
import { sanitizeMediaUrl } from '../../lib/security';

export interface MediaImageProps {
  title: string;
  posterUrl?: string | null;
  backdropUrl?: string | null;
  aspect?: 'poster' | 'backdrop' | 'square';
  className?: string;
  imgClassName?: string;
  alt?: string;
  fallbackYear?: number | null;
  showShimmer?: boolean;
  loading?: 'lazy' | 'eager';
  fetchPriority?: 'high' | 'low' | 'auto';
}

export const MediaImage: React.FC<MediaImageProps> = ({
  title,
  posterUrl,
  backdropUrl,
  aspect = 'poster',
  className = '',
  imgClassName = '',
  alt,
  fallbackYear,
  showShimmer = true,
  loading = 'lazy',
  fetchPriority,
}) => {
  // Determine primary & secondary URLs based on intended aspect using strict URL sanitizer
  const cleanPoster = sanitizeMediaUrl(posterUrl);
  const cleanBackdrop = sanitizeMediaUrl(backdropUrl);

  const isBackdropAspect = aspect === 'backdrop';
  const primaryUrl = isBackdropAspect
    ? (cleanBackdrop || cleanPoster)
    : (cleanPoster || cleanBackdrop);

  const secondaryUrl = isBackdropAspect
    ? (cleanPoster && cleanPoster !== cleanBackdrop ? cleanPoster : null)
    : (cleanBackdrop && cleanBackdrop !== cleanPoster ? cleanBackdrop : null);

  const [activeUrl, setActiveUrl] = useState<string | null>(primaryUrl);
  const [hasTriedSecondary, setHasTriedSecondary] = useState<boolean>(false);
  const [isLoaded, setIsLoaded] = useState<boolean>(false);
  const [hasFailed, setHasFailed] = useState<boolean>(!primaryUrl);

  const imgRef = useRef<HTMLImageElement | null>(null);

  // Sync state whenever props change
  useEffect(() => {
    setActiveUrl(primaryUrl);
    setHasTriedSecondary(false);
    setIsLoaded(false);
    setHasFailed(!primaryUrl);
  }, [primaryUrl, secondaryUrl]);

  const handleError = useCallback(() => {
    if (!hasTriedSecondary && secondaryUrl && activeUrl !== secondaryUrl) {
      // Gracefully switch to secondary image source (e.g. backdrop failed -> try poster)
      setHasTriedSecondary(true);
      setActiveUrl(secondaryUrl);
      setIsLoaded(false);
    } else {
      // Both sources failed or none available
      setHasFailed(true);
    }
  }, [hasTriedSecondary, secondaryUrl, activeUrl]);

  const handleLoad = useCallback(() => {
    setIsLoaded(true);
  }, []);

  // Check image completion on mount, URL change, and when container transitions from display:none
  const checkImageComplete = useCallback(() => {
    const node = imgRef.current;
    if (node) {
      if (node.complete) {
        if (node.naturalWidth > 0) {
          setIsLoaded(true);
        } else if (node.src) {
          handleError();
        }
      }
    }
  }, [handleError]);

  // Ref callback to immediately check browser cache status on node attach
  const imgCallbackRef = useCallback((node: HTMLImageElement | null) => {
    imgRef.current = node;
    if (node && node.complete) {
      if (node.naturalWidth > 0) {
        setIsLoaded(true);
      } else if (node.src) {
        handleError();
      }
    }
  }, [handleError]);

  // Resilient load check for display:none containers or cached images
  useEffect(() => {
    checkImageComplete();

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        checkImageComplete();
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);

    // Watch for parent container showing up via animation frame or timer
    const t1 = setTimeout(checkImageComplete, 50);
    const t2 = setTimeout(checkImageComplete, 300);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [activeUrl, checkImageComplete]);

  // Whether we are rendering a portrait poster in a widescreen backdrop slot
  const isPosterInBackdrop = isBackdropAspect && activeUrl === cleanPoster && !cleanBackdrop;

  // Eager loading guarantees visibility even if load events were swallowed while container was hidden
  const effectiveFetchPriority = fetchPriority || (loading === 'eager' ? 'high' : 'auto');
  const showImage = isLoaded || (loading === 'eager' && imgRef.current?.complete && (imgRef.current?.naturalWidth ?? 0) > 0);

  return (
    <div className={`relative w-full h-full overflow-hidden ${className}`}>
      {/* 1. Shimmer Skeleton while image is actively loading */}
      {showShimmer && !showImage && !hasFailed && (
        <div className="absolute inset-0 skeleton-shimmer z-0" />
      )}

      {/* 2. Primary / Secondary Active Image */}
      {!hasFailed && activeUrl ? (
        <div className="relative w-full h-full overflow-hidden">
          {/* Ambient Blurred Backdrop Fill when displaying portrait poster in widescreen backdrop slot */}
          {isPosterInBackdrop && (
            <img
              src={activeUrl}
              alt=""
              aria-hidden="true"
              className="absolute inset-0 w-full h-full object-cover blur-2xl scale-125 opacity-40 select-none pointer-events-none"
            />
          )}

          <img
            ref={imgCallbackRef}
            src={activeUrl}
            alt={alt || title}
            loading={loading}
            // @ts-expect-error fetchpriority is standard HTML
            fetchpriority={effectiveFetchPriority}
            onLoad={handleLoad}
            onError={handleError}
            className={`w-full h-full ${
              isPosterInBackdrop ? 'relative object-contain object-center' : 'object-cover'
            } transition-opacity duration-300 ${
              showImage ? 'opacity-100' : 'opacity-0'
            } ${imgClassName}`}
          />
        </div>
      ) : (
        /* 3. Branded StreamVault Resilient Fallback Card */
        <div className="w-full h-full bg-gradient-to-br from-neutral-800 via-neutral-900 to-black flex flex-col items-center justify-center p-3 text-center border border-neutral-800 select-none">
          <div className="w-9 h-9 rounded-xl bg-red-600/20 text-red-500 flex items-center justify-center mb-1.5 font-black text-xs shadow-inner">
            <Film className="w-4 h-4" />
          </div>
          <p className="text-white font-bold text-xs line-clamp-2 px-1 leading-tight">
            {title}
          </p>
          {fallbackYear && (
            <span className="text-[10px] text-neutral-400 font-mono mt-1">
              {fallbackYear}
            </span>
          )}
        </div>
      )}
    </div>
  );
};
