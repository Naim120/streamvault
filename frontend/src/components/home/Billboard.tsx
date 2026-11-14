import React, { useState, useRef, useEffect } from 'react';
import { Play, Pause, Info, Volume2, VolumeX, Film } from 'lucide-react';
import { MediaItem, Episode, WatchProgressItem } from '../../types';
import { useUIStore } from '../../store/useUIStore';
import { usePlayerStore } from '../../store/usePlayerStore';
import { useAuthStore } from '../../store/useAuthStore';
import { supabase } from '../../lib/supabase';
import { MediaImage } from '../common/MediaImage';
import { extractSafeTrailer } from '../../lib/security';
import { prewarmStream } from '../../lib/streamPrewarm';
import {
  isTrailerBlocked,
  isTrailerPlayable,
  markTrailerBlocked,
  markTrailerPlayable
} from '../../lib/trailerValidator';

interface BillboardProps {
  item: MediaItem;
  resumeProgress?: WatchProgressItem | null;
}

export const Billboard: React.FC<BillboardProps> = ({ item, resumeProgress }) => {
  const { openDetail, detailMedia } = useUIStore();
  const { openPlayer, openTrailer, isOpen: isPlayerOpen } = usePlayerStore();
  const { user } = useAuthStore();

  const [muted, setMuted] = useState(true);
  const [isPlaying, setIsPlaying] = useState(true);
  const [liveTrailerKey, setLiveTrailerKey] = useState<string | null>(item?.trailer_youtube_key || null);
  const trailer = extractSafeTrailer(item?.trailer_youtube_key || liveTrailerKey);

  const [isVideoReady, setIsVideoReady] = useState(false);
  const [trailerError, setTrailerError] = useState(() => isTrailerBlocked(trailer?.src));
  const [trailerVerifiedPlayable, setTrailerVerifiedPlayable] = useState(() => isTrailerPlayable(trailer?.src));
  const [isInView, setIsInView] = useState(true);
  const [isSynopsisExpanded, setIsSynopsisExpanded] = useState(false);
  const [localResume, setLocalResume] = useState<WatchProgressItem | null>(resumeProgress || null);
  const [prewarmedEpisode, setPrewarmedEpisode] = useState<Episode | null>(null);

  const billboardContainerRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const lastPlayerStateRef = useRef<number | null>(null);
  const readyCheckTimerRef = useRef<number | null>(null);
  const fallbackWatchdogRef = useRef<number | null>(null);

  useEffect(() => {
    if (resumeProgress !== undefined) {
      setLocalResume(resumeProgress);
      return;
    }
    if (!user || !item) {
      setLocalResume(null);
      return;
    }
    let isMounted = true;
    supabase
      .from('watch_progress')
      .select('*')
      .eq('user_id', user.id)
      .eq('media_id', item.id)
      .eq('is_completed', false)
      .gt('position_seconds', 5)
      .order('updated_at', { ascending: false })
      .limit(1)
      .then(({ data }) => {
        if (isMounted && data && data.length > 0) {
          setLocalResume(data[0] as any);
        } else if (isMounted) {
          setLocalResume(null);
        }
      });
    return () => { isMounted = false; };
  }, [item?.id, user?.id, resumeProgress]);

  const hasResume = localResume && !localResume.is_completed && localResume.position_seconds > 5;

  // Speculatively pre-warm stream token, metadata & manifest for instant playback
  useEffect(() => {
    if (!item?.id) return;
    const target = (hasResume && localResume?.episode_id) ? localResume.episode_id : item.id;
    prewarmStream(target, user?.id).then((res) => {
      if (res?.tokenData?.resolved_episode) {
        setPrewarmedEpisode(res.tokenData.resolved_episode);
      }
    });
  }, [item?.id, localResume?.episode_id, user?.id, hasResume]);

  const handlePlayHero = () => {
    if (hasResume && localResume) {
      let ep: Episode | null = null;
      if (item.type === 'series') {
        if (prewarmedEpisode) {
          ep = prewarmedEpisode;
        } else if (localResume.episode_id) {
          ep = {
            id: localResume.episode_id,
            media_id: item.id,
            season_id: '',
            season_number: localResume.season_number || 1,
            episode_number: localResume.episode_number || 1,
            title: localResume.episode_title || 'Episode',
            synopsis: localResume.episode_synopsis || null,
            duration_seconds: localResume.episode_duration_seconds || localResume.duration_seconds || 0,
            still_url: localResume.episode_still_url || item.backdrop_url,
            is_published: true
          };
        }
      }
      openPlayer(item, ep, localResume.position_seconds);
    } else {
      openPlayer(item, prewarmedEpisode || null, 0);
    }
  };

  if (!item) return null;

  useEffect(() => {
    let isMounted = true;
    // Immediately synchronize liveTrailerKey with the current item (resets to null if item has no trailer!)
    const initialKey = item?.trailer_youtube_key ? item.trailer_youtube_key.trim() : null;
    setLiveTrailerKey(initialKey || null);

    const activeTrailer = extractSafeTrailer(initialKey);
    setTrailerError(isTrailerBlocked(activeTrailer?.src));
    setTrailerVerifiedPlayable(isTrailerPlayable(activeTrailer?.src));
    setIsVideoReady(false);
    lastPlayerStateRef.current = null;
    if (readyCheckTimerRef.current) {
      clearTimeout(readyCheckTimerRef.current);
      readyCheckTimerRef.current = null;
    }
    if (fallbackWatchdogRef.current) {
      clearTimeout(fallbackWatchdogRef.current);
      fallbackWatchdogRef.current = null;
    }

    if (!initialKey && item?.id) {
      supabase
        .from('media_items')
        .select('trailer_youtube_key')
        .eq('id', item.id)
        .maybeSingle()
        .then(({ data }) => {
          if (!isMounted) return;
          const dbKey = data?.trailer_youtube_key ? data.trailer_youtube_key.trim() : null;
          setLiveTrailerKey(dbKey || null);
          const fetchedTrailer = extractSafeTrailer(dbKey);
          setTrailerError(isTrailerBlocked(fetchedTrailer?.src));
          setTrailerVerifiedPlayable(isTrailerPlayable(fetchedTrailer?.src));
        });
    }
    return () => {
      isMounted = false;
    };
  }, [item?.id, item?.trailer_youtube_key]);

  // Listen for broadcast trailer status changes across the app
  useEffect(() => {
    const handleStatusChange = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail && detail.src === trailer?.src) {
        if (detail.status === 'blocked') {
          setTrailerError(true);
          setIsVideoReady(false);
          setTrailerVerifiedPlayable(false);
        } else if (detail.status === 'playable') {
          setTrailerVerifiedPlayable(true);
        }
      }
    };
    window.addEventListener('sv:trailer-status', handleStatusChange);
    return () => window.removeEventListener('sv:trailer-status', handleStatusChange);
  }, [trailer?.src]);

  // Re-sync Billboard visibility and layout when returning from DetailModal or VideoPlayer
  useEffect(() => {
    if (!detailMedia && !isPlayerOpen) {
      setIsInView(true);
      if (!trailer || trailerError) {
        setIsVideoReady(false);
      }
    }
  }, [detailMedia, isPlayerOpen, trailer, trailerError]);

  // Listen for YouTube embed events: play confirmation or genuine error fallback to backdrop poster
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (!iframeRef.current || event.source !== iframeRef.current.contentWindow) return;
      try {
        const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        if (data && typeof data === 'object') {
          // 1. If a genuine error code occurs from YouTube player, fallback to backdrop poster
          if (
            data.event === 'onError' ||
            data.event === 'onPlayerError' ||
            (data.info && (data.info.error || [2, 5, 100, 101, 150].includes(data.info)))
          ) {
            markTrailerBlocked(trailer?.src);
            setTrailerError(true);
            setIsVideoReady(false);
            setTrailerVerifiedPlayable(false);
            return;
          }

          // 2. Instant bot-check & unplayable video detection in initialDelivery / infoDelivery (<150ms)
          const vd = data.info?.videoData;
          if (vd) {
            const isExplicitlyUnplayable = vd.isPlayable === false;
            const hasErrorCode = Boolean(vd.errorCode);
            const isBlankTitle =
              (data.event === 'initialDelivery' || data.event === 'infoDelivery') &&
              typeof vd.title === 'string' &&
              vd.title.trim() === '';

            if (isExplicitlyUnplayable || hasErrorCode || isBlankTitle) {
              markTrailerBlocked(trailer?.src);
              setTrailerError(true);
              setIsVideoReady(false);
              setTrailerVerifiedPlayable(false);
              return;
            }
          }

          // Track latest player state
          const rawState = typeof data.info === 'number' ? data.info : data.info?.playerState;
          if (typeof rawState === 'number') {
            lastPlayerStateRef.current = rawState;
          }

          // 3. When onReady arrives, trigger playback and mute handshake, but do NOT reveal iframe yet
          if (data.event === 'onReady') {
            const win = iframeRef.current?.contentWindow;
            if (win) {
              try {
                win.postMessage(JSON.stringify({ event: 'command', func: 'mute', args: [] }), '*');
                win.postMessage(JSON.stringify({ event: 'command', func: 'playVideo', args: [] }), '*');
              } catch {}
            }

            // Fast post-onReady watchdog: if after 450ms player state hasn't advanced to PLAYING (1) or BUFFERING (3), mark blocked
            if (readyCheckTimerRef.current) clearTimeout(readyCheckTimerRef.current);
            readyCheckTimerRef.current = window.setTimeout(() => {
              if (lastPlayerStateRef.current !== 1 && lastPlayerStateRef.current !== 3) {
                markTrailerBlocked(trailer?.src);
                setTrailerError(true);
                setIsVideoReady(false);
                setTrailerVerifiedPlayable(false);
              }
            }, 450);
            return;
          }

          // 4. STRICT REVEAL: ONLY set isVideoReady = true when YouTube explicitly confirms PLAYING (state === 1)
          const isPlayingState =
            (data.event === 'onStateChange' && data.info === 1) ||
            (data.info && data.info.playerState === 1);

          if (isPlayingState) {
            if (readyCheckTimerRef.current) {
              clearTimeout(readyCheckTimerRef.current);
              readyCheckTimerRef.current = null;
            }
            if (fallbackWatchdogRef.current) {
              clearTimeout(fallbackWatchdogRef.current);
              fallbackWatchdogRef.current = null;
            }
            markTrailerPlayable(trailer?.src);
            setTrailerVerifiedPlayable(true);
            setIsVideoReady(true);
            setTrailerError(false);
          }
        }
      } catch {
        if (
          typeof event.data === 'string' &&
          (event.data.includes('onError') || event.data.includes('onPlayerError') || event.data.includes('"error"'))
        ) {
          markTrailerBlocked(trailer?.src);
          setTrailerError(true);
          setIsVideoReady(false);
          setTrailerVerifiedPlayable(false);
        }
      }
    };

    window.addEventListener('message', handleMessage);
    return () => {
      window.removeEventListener('message', handleMessage);
      if (readyCheckTimerRef.current) {
        clearTimeout(readyCheckTimerRef.current);
        readyCheckTimerRef.current = null;
      }
    };
  }, [trailer?.src]);

  // Initiate YouTube JS API handshake on iframe load
  const handleIframeLoad = () => {
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    try {
      win.postMessage(JSON.stringify({ event: 'listening' }), '*');
      win.postMessage(JSON.stringify({ event: 'command', func: 'playVideo', args: [] }), '*');
      win.postMessage(JSON.stringify({ event: 'command', func: 'mute', args: [] }), '*');

      setTimeout(() => {
        try {
          win.postMessage(JSON.stringify({ event: 'listening' }), '*');
          win.postMessage(JSON.stringify({ event: 'command', func: 'playVideo', args: [] }), '*');
        } catch {}
      }, 500);
    } catch {}
  };

  // Fallback watchdog: If YouTube trailer genuinely fails to reach PLAYING within 4.5s (e.g. bot check wall, ad-blocker), fallback cleanly to backdrop poster
  useEffect(() => {
    if (!trailer || trailer.type !== 'youtube' || isVideoReady || trailerError) return;

    if (fallbackWatchdogRef.current) clearTimeout(fallbackWatchdogRef.current);
    fallbackWatchdogRef.current = window.setTimeout(() => {
      markTrailerBlocked(trailer.src);
      setTrailerError(true);
      setIsVideoReady(false);
      setTrailerVerifiedPlayable(false);
    }, 4500);

    return () => {
      if (fallbackWatchdogRef.current) {
        clearTimeout(fallbackWatchdogRef.current);
        fallbackWatchdogRef.current = null;
      }
    };
  }, [trailer?.src, trailer?.type, isVideoReady, trailerError]);

  // Active in view observer: Pauses when scrolled out of view, resumes when scrolled in view
  useEffect(() => {
    const element = billboardContainerRef.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsInView(entry.isIntersecting && entry.intersectionRatio > 0.2);
      },
      { threshold: [0, 0.2, 0.5] }
    );

    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Strict Playback Controller: Trailer only plays when actively in view AND no detail modal or video player is active
  const shouldPlay = isInView && !detailMedia && !isPlayerOpen && isPlaying && !!trailer && !trailerError;

  useEffect(() => {
    if (!trailer) return;
    if (shouldPlay) {
      if (trailer.type === 'youtube') {
        iframeRef.current?.contentWindow?.postMessage(
          JSON.stringify({ event: 'command', func: 'playVideo', args: [] }),
          '*'
        );
      } else if (videoRef.current) {
        videoRef.current.play().catch(() => {});
      }
    } else {
      if (trailer.type === 'youtube') {
        iframeRef.current?.contentWindow?.postMessage(
          JSON.stringify({ event: 'command', func: 'pauseVideo', args: [] }),
          '*'
        );
        iframeRef.current?.contentWindow?.postMessage(
          JSON.stringify({ event: 'command', func: 'mute', args: [] }),
          '*'
        );
      } else if (videoRef.current) {
        videoRef.current.pause();
        videoRef.current.muted = true;
      }
    }
  }, [shouldPlay, trailer]);

  const toggleMute = () => {
    const nextMuted = !muted;
    setMuted(nextMuted);
    if (trailer?.type === 'youtube') {
      iframeRef.current?.contentWindow?.postMessage(
        JSON.stringify({
          event: 'command',
          func: nextMuted ? 'mute' : 'unMute',
          args: []
        }),
        '*'
      );
      if (!nextMuted) {
        iframeRef.current?.contentWindow?.postMessage(
          JSON.stringify({
            event: 'command',
            func: 'setVolume',
            args: [100]
          }),
          '*'
        );
      }
    } else if (videoRef.current) {
      videoRef.current.muted = nextMuted;
    }
  };

  const togglePlayPause = () => {
    const nextPlaying = !isPlaying;
    setIsPlaying(nextPlaying);
    if (trailer?.type === 'youtube') {
      iframeRef.current?.contentWindow?.postMessage(
        JSON.stringify({
          event: 'command',
          func: nextPlaying ? 'playVideo' : 'pauseVideo',
          args: []
        }),
        '*'
      );
    } else if (videoRef.current) {
      if (nextPlaying) {
        videoRef.current.play().catch(() => {});
      } else {
        videoRef.current.pause();
      }
    }
  };

  const isTrailerShowing = Boolean(
    trailer &&
    !trailerError &&
    isVideoReady &&
    !detailMedia &&
    !isPlayerOpen
  );

  return (
    <div
      ref={billboardContainerRef}
      className="relative w-full h-[65vh] sm:h-[75vh] lg:h-[85vh] min-h-[460px] max-h-[860px] bg-neutral-950 overflow-hidden select-none"
      style={{ contain: 'paint' }}
    >
      {/* 1. Underlying Backdrop Image Base Layer (Always rendered underneath for zero layout shift) */}
      <div
        className={`absolute inset-0 w-full h-full transition-opacity duration-700 ${
          isTrailerShowing ? 'opacity-0' : 'opacity-100'
        }`}
      >
        <MediaImage
          title={item.title}
          posterUrl={item.poster_url}
          backdropUrl={item.backdrop_url}
          aspect="backdrop"
          fallbackYear={item.release_year}
          loading="eager"
          fetchPriority="high"
          className="w-full h-full"
        />
      </div>

      {/* 2. Auto-playing Trailer (Rendered above static backdrop when available and active) */}
      {trailer && !trailerError && (
        <div 
          className={`absolute inset-0 w-full h-full overflow-hidden pointer-events-none flex items-center justify-center transition-opacity duration-700 ${
            isTrailerShowing ? 'opacity-100' : 'opacity-0'
          }`}
          style={{ contain: 'paint', clipPath: 'inset(0)' }}
        >
          {trailer.type === 'youtube' ? (
            <div 
              className="relative w-full h-full flex items-center justify-center overflow-hidden pointer-events-none"
              style={{ contain: 'paint', clipPath: 'inset(0)' }}
            >
              <iframe
                key={`${item.id}-${trailer.src}`}
                ref={iframeRef}
                onLoad={handleIframeLoad}
                className="pointer-events-none"
                style={{
                  position: 'absolute',
                  top: '50%',
                  left: '50%',
                  transform: 'translate(-50%, -50%) scale(1.18)',
                  transformOrigin: 'center center',
                  width: '100vw',
                  height: '56.25vw',
                  minHeight: '100%',
                  minWidth: '177.77vh',
                  maxWidth: 'none',
                }}
                src={`https://www.youtube-nocookie.com/embed/${trailer.src}?autoplay=1&mute=1&controls=0&loop=1&playlist=${trailer.src}&modestbranding=1&rel=0&iv_load_policy=3&disablekb=1&enablejsapi=1&origin=${typeof window !== 'undefined' ? window.location.origin : ''}&playsinline=1`}
                title="Hero Trailer"
                referrerPolicy="origin"
                allow="autoplay; encrypted-media; picture-in-picture"
                onError={() => {
                  markTrailerBlocked(trailer.src);
                  setTrailerError(true);
                  setIsVideoReady(false);
                  setTrailerVerifiedPlayable(false);
                }}
              />
            </div>
          ) : (
            <video
              key={`${item.id}-${trailer.src}`}
              ref={videoRef}
              src={trailer.src}
              autoPlay
              muted
              loop
              playsInline
              className="w-full h-full object-cover"
              onPlay={() => {
                markTrailerPlayable(trailer.src);
                setTrailerVerifiedPlayable(true);
                setIsVideoReady(true);
                setTrailerError(false);
              }}
              onError={() => {
                markTrailerBlocked(trailer.src);
                setTrailerError(true);
                setIsVideoReady(false);
                setTrailerVerifiedPlayable(false);
              }}
            />
          )}
        </div>
      )}

      {/* 3. Cinematic Vignette Gradients */}
      <div className="absolute inset-0 bg-gradient-to-r from-black/90 via-black/40 to-transparent pointer-events-none z-10" />
      <div className="absolute inset-0 bg-gradient-to-l from-black/60 via-transparent to-transparent pointer-events-none z-10" />
      <div className="absolute inset-0 bg-gradient-to-t from-black via-black/30 to-transparent pointer-events-none z-10" />
      {/* Seamless Bottom Fade to Content Rows */}
      <div className="absolute -bottom-1 left-0 right-0 h-32 sm:h-48 bg-gradient-to-t from-black via-black/90 to-transparent pointer-events-none z-10" />

      {/* 4. Content Container */}
      <div className="absolute bottom-12 sm:bottom-16 left-4 sm:left-12 max-w-xl z-20 space-y-3">
        {/* Maturity & Release Year Badge */}
        <div className="flex items-center gap-3 text-xs">
          <span className="px-1.5 py-0.5 border border-white/40 bg-black/50 rounded text-[11px] text-white backdrop-blur-md">
            {item.maturity_rating || 'U/A 16+'}
          </span>
          <span className="text-neutral-300 font-semibold">{item.release_year}</span>
        </div>

        {/* Title */}
        <h1 className="text-3xl sm:text-5xl md:text-6xl font-black text-white tracking-tight drop-shadow-2xl">
          {item.title}
        </h1>

        {/* Synopsis with Read More option */}
        <div className="max-w-lg drop-shadow-md">
          <p className={`text-neutral-200 text-xs sm:text-sm leading-relaxed transition-all ${
            isSynopsisExpanded ? '' : 'line-clamp-3'
          }`}>
            {item.synopsis}
          </p>
          {item.synopsis && item.synopsis.length > 120 && (
            <button
              onClick={() => setIsSynopsisExpanded(!isSynopsisExpanded)}
              className="mt-1 text-neutral-400 hover:text-white text-xs font-bold underline underline-offset-2 transition cursor-pointer"
            >
              {isSynopsisExpanded ? 'Read Less' : 'Read More'}
            </button>
          )}
        </div>

        {/* CTAs */}
        <div className="flex flex-wrap items-center gap-3 pt-2">
          <button
            onClick={handlePlayHero}
            onMouseEnter={() => {
              const target = (hasResume && localResume?.episode_id) ? localResume.episode_id : item.id;
              prewarmStream(target, user?.id);
            }}
            className="px-6 sm:px-8 py-3 bg-white hover:bg-neutral-200 text-black font-black text-sm rounded-xl transition duration-200 flex items-center gap-2 shadow-2xl cursor-pointer"
          >
            <Play className="w-5 h-5 fill-current ml-0.5" />
            {hasResume && localResume
              ? (item.type === 'series' && localResume.season_number
                  ? `Resume S${localResume.season_number}:E${localResume.episode_number}`
                  : 'Resume')
              : 'Play'}
          </button>

          {trailer && !trailerError && (isVideoReady || trailerVerifiedPlayable) && (
            <button
              onClick={() => openTrailer(item, trailer.src)}
              className="px-5 sm:px-6 py-3 bg-neutral-900/80 hover:bg-neutral-800 text-white font-bold text-sm rounded-xl transition duration-200 backdrop-blur-md flex items-center gap-2 border border-white/20 cursor-pointer shadow-xl group"
              title="Watch Official Trailer"
            >
              <Film className="w-4 h-4 text-red-500 group-hover:scale-110 transition-transform" />
              Watch Trailer
            </button>
          )}

          <button
            onClick={() => openDetail(item)}
            className="px-5 sm:px-6 py-3 bg-neutral-600/60 hover:bg-neutral-600/80 text-white font-bold text-sm rounded-xl transition duration-200 backdrop-blur-md flex items-center gap-2 border border-white/20 cursor-pointer"
          >
            <Info className="w-5 h-5" />
            More Info
          </button>
        </div>
      </div>

      {/* 5. Floating Trailer Media Controls: Play/Pause and Mute/Unmute */}
      {trailer && !trailerError && isTrailerShowing && (
        <div className="absolute bottom-12 sm:bottom-16 right-6 sm:right-10 md:right-12 z-30 flex items-center gap-2.5">
          <button
            onClick={togglePlayPause}
            aria-label={isPlaying ? 'Pause trailer' : 'Play trailer'}
            className="p-2.5 sm:p-3 bg-black/75 hover:bg-black text-white rounded-full transition backdrop-blur-md border border-white/25 cursor-pointer shadow-xl flex items-center justify-center group"
            title={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? <Pause className="w-4 h-4 sm:w-5 sm:h-5 fill-current" /> : <Play className="w-4 h-4 sm:w-5 sm:h-5 fill-current ml-0.5" />}
          </button>

          <button
            onClick={toggleMute}
            aria-label={muted ? 'Unmute trailer' : 'Mute trailer'}
            className="p-2.5 sm:p-3 bg-black/75 hover:bg-black text-white rounded-full transition backdrop-blur-md border border-white/25 cursor-pointer shadow-xl flex items-center justify-center group"
            title={muted ? 'Unmute' : 'Mute'}
          >
            {muted ? <VolumeX className="w-4 h-4 sm:w-5 sm:h-5" /> : <Volume2 className="w-4 h-4 sm:w-5 sm:h-5" />}
          </button>
        </div>
      )}
    </div>
  );
};
