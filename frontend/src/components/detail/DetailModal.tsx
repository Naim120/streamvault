import React, { useEffect, useState, useRef } from 'react';
import { 
  Play, Pause, Plus, Check, ThumbsUp, Share2, Volume2, VolumeX, ArrowLeft, 
  ChevronDown, Clock, Sparkles, X, Film 
} from 'lucide-react';
import { useUIStore } from '../../store/useUIStore';
import { usePlayerStore } from '../../store/usePlayerStore';
import { useAuthStore } from '../../store/useAuthStore';
import { supabase } from '../../lib/supabase';
import { CustomList, Episode, MediaItem, Season, WatchProgressItem } from '../../types';
import { MediaImage } from '../common/MediaImage';
import { extractSafeTrailer } from '../../lib/security';
import { prewarmStream } from '../../lib/streamPrewarm';
import {
  isTrailerBlocked,
  isTrailerPlayable,
  markTrailerBlocked,
  markTrailerPlayable
} from '../../lib/trailerValidator';

export const DetailModal: React.FC = () => {
  const { detailMedia: media, closeDetail, showToast, openDetail, progressRefreshTrigger } = useUIStore();
  const { openPlayer, openTrailer, isOpen: isPlayerOpen } = usePlayerStore();
  const { user } = useAuthStore();

  const trailer = extractSafeTrailer(media?.trailer_youtube_key);

  const [activeTab, setActiveTab] = useState<'episodes' | 'more'>('episodes');
  const [trailerMuted, setTrailerMuted] = useState(true);
  const [trailerPlaying, setTrailerPlaying] = useState(true);
  const [trailerReady, setTrailerReady] = useState(false);
  const [trailerError, setTrailerError] = useState(() => isTrailerBlocked(trailer?.src));
  const [trailerVerifiedPlayable, setTrailerVerifiedPlayable] = useState(() => isTrailerPlayable(trailer?.src));
  const [userLiked, setUserLiked] = useState(false);
  const [watchProgress, setWatchProgress] = useState<WatchProgressItem | null>(null);
  const [activeResumeEpisode, setActiveResumeEpisode] = useState<Episode | null>(null);
  const [episodeProgressMap, setEpisodeProgressMap] = useState<Record<string, { position_seconds: number; duration_seconds: number; is_completed: boolean }>>({});
  const [isCopied, setIsCopied] = useState(false);
  const [isSynopsisExpanded, setIsSynopsisExpanded] = useState(false);

  const trailerContainerRef = useRef<HTMLDivElement | null>(null);
  const trailerIframeRef = useRef<HTMLIFrameElement | null>(null);
  const trailerVideoRef = useRef<HTMLVideoElement | null>(null);
  const lastPlayerStateRef = useRef<number | null>(null);
  const readyCheckTimerRef = useRef<number | null>(null);
  const fallbackWatchdogRef = useRef<number | null>(null);

  // Series Specific
  const [seasons, setSeasons] = useState<Season[]>([]);
  const [selectedSeasonNumber, setSelectedSeasonNumber] = useState<number>(1);
  const [isSeasonDropdownOpen, setIsSeasonDropdownOpen] = useState(false);
  const seasonMenuRef = useRef<HTMLDivElement | null>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [allSeriesEpisodes, setAllSeriesEpisodes] = useState<Episode[]>([]);
  const [episodesProgress, setEpisodesProgress] = useState<Record<string, number>>({});

  // Close season dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (seasonMenuRef.current && !seasonMenuRef.current.contains(e.target as Node)) {
        setIsSeasonDropdownOpen(false);
      }
    };
    if (isSeasonDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isSeasonDropdownOpen]);

  // Recommendations
  const [similarItems, setSimilarItems] = useState<MediaItem[]>([]);

  // Custom Lists Popover & Modal
  const [showListPopover, setShowListPopover] = useState(false);
  const [customLists, setCustomLists] = useState<CustomList[]>([]);
  const [inListIds, setInListIds] = useState<Set<string>>(new Set());
  const [newListName, setNewListName] = useState('');
  const [isCreatingList, setIsCreatingList] = useState(false);

  // Lock body & html scroll to eliminate double scrollbar
  useEffect(() => {
    const origBodyOverflow = document.body.style.overflow;
    const origHtmlOverflow = document.documentElement.style.overflow;
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = origBodyOverflow;
      document.documentElement.style.overflow = origHtmlOverflow;
    };
  }, []);

  // Sync trailer states on media change or trailer source change
  useEffect(() => {
    setTrailerError(isTrailerBlocked(trailer?.src));
    setTrailerVerifiedPlayable(isTrailerPlayable(trailer?.src));
    setTrailerReady(false);
    lastPlayerStateRef.current = null;
    if (readyCheckTimerRef.current) {
      clearTimeout(readyCheckTimerRef.current);
      readyCheckTimerRef.current = null;
    }
    if (fallbackWatchdogRef.current) {
      clearTimeout(fallbackWatchdogRef.current);
      fallbackWatchdogRef.current = null;
    }
  }, [media?.id, trailer?.src]);

  // Listen for broadcast trailer status changes across the app
  useEffect(() => {
    const handleStatusChange = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail && detail.src === trailer?.src) {
        if (detail.status === 'blocked') {
          setTrailerError(true);
          setTrailerReady(false);
          setTrailerVerifiedPlayable(false);
        } else if (detail.status === 'playable') {
          setTrailerVerifiedPlayable(true);
        }
      }
    };
    window.addEventListener('sv:trailer-status', handleStatusChange);
    return () => window.removeEventListener('sv:trailer-status', handleStatusChange);
  }, [trailer?.src]);

  // Listen for YouTube player events to reveal on play or fallback gracefully to backdrop poster
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (!trailerIframeRef.current || event.source !== trailerIframeRef.current.contentWindow) return;
      try {
        const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        if (data && typeof data === 'object') {
          // 1. YouTube error event or standard error codes (e.g. 100, 101, 150)
          if (
            data.event === 'onError' ||
            data.event === 'onPlayerError' ||
            (data.info && (data.info.error || [2, 5, 100, 101, 150].includes(data.info)))
          ) {
            markTrailerBlocked(trailer?.src);
            setTrailerError(true);
            setTrailerReady(false);
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
              setTrailerReady(false);
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
            const win = trailerIframeRef.current?.contentWindow;
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
                setTrailerReady(false);
                setTrailerVerifiedPlayable(false);
              }
            }, 450);
            return;
          }

          // 4. STRICT REVEAL: ONLY confirm trailerReady & trailerVerifiedPlayable when YouTube explicitly confirms PLAYING (state === 1)
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
            setTrailerReady(true);
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
          setTrailerReady(false);
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

  const handleTrailerIframeLoad = () => {
    const win = trailerIframeRef.current?.contentWindow;
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

  // Fallback watchdog: If YouTube trailer genuinely fails to reach PLAYING within 4.5s (e.g. ad-blocker or silent drop), fallback cleanly to backdrop poster
  useEffect(() => {
    if (!trailer || trailer.type !== 'youtube' || trailerReady || trailerError) return;

    if (fallbackWatchdogRef.current) clearTimeout(fallbackWatchdogRef.current);
    fallbackWatchdogRef.current = window.setTimeout(() => {
      markTrailerBlocked(trailer.src);
      setTrailerError(true);
      setTrailerReady(false);
      setTrailerVerifiedPlayable(false);
    }, 4500);

    return () => {
      if (fallbackWatchdogRef.current) {
        clearTimeout(fallbackWatchdogRef.current);
        fallbackWatchdogRef.current = null;
      }
    };
  }, [trailer?.src, trailer?.type, trailerReady, trailerError]);

  // Pause DetailModal trailer whenever the video player is open
  useEffect(() => {
    if (isPlayerOpen && trailer) {
      if (trailer.type === 'youtube') {
        trailerIframeRef.current?.contentWindow?.postMessage(
          JSON.stringify({ event: 'command', func: 'pauseVideo', args: [] }),
          '*'
        );
        trailerIframeRef.current?.contentWindow?.postMessage(
          JSON.stringify({ event: 'command', func: 'mute', args: [] }),
          '*'
        );
      } else if (trailerVideoRef.current) {
        trailerVideoRef.current.pause();
        trailerVideoRef.current.muted = true;
      }
      setTrailerPlaying(false);
    }
  }, [isPlayerOpen, trailer]);

  // Active in view observer: Pauses DetailModal trailer when scrolled down out of view
  useEffect(() => {
    const element = trailerContainerRef.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        const inView = entry.isIntersecting && entry.intersectionRatio > 0.15;
        if (!inView && trailerPlaying) {
          if (trailer?.type === 'youtube') {
            trailerIframeRef.current?.contentWindow?.postMessage(
              JSON.stringify({ event: 'command', func: 'pauseVideo', args: [] }),
              '*'
            );
          } else if (trailerVideoRef.current) {
            trailerVideoRef.current.pause();
          }
        } else if (inView && !isPlayerOpen && trailerPlaying) {
          if (trailer?.type === 'youtube') {
            trailerIframeRef.current?.contentWindow?.postMessage(
              JSON.stringify({ event: 'command', func: 'playVideo', args: [] }),
              '*'
            );
          } else if (trailerVideoRef.current) {
            trailerVideoRef.current.play().catch(() => {});
          }
        }
      },
      { threshold: [0, 0.15, 0.5] }
    );

    observer.observe(element);
    return () => observer.disconnect();
  }, [trailer, trailerPlaying, isPlayerOpen]);

  const toggleTrailerSound = () => {
    const nextMuted = !trailerMuted;
    setTrailerMuted(nextMuted);
    if (trailer?.type === 'youtube') {
      trailerIframeRef.current?.contentWindow?.postMessage(
        JSON.stringify({
          event: 'command',
          func: nextMuted ? 'mute' : 'unMute',
          args: []
        }),
        '*'
      );
      if (!nextMuted) {
        trailerIframeRef.current?.contentWindow?.postMessage(
          JSON.stringify({
            event: 'command',
            func: 'setVolume',
            args: [100]
          }),
          '*'
        );
      }
    } else if (trailerVideoRef.current) {
      trailerVideoRef.current.muted = nextMuted;
    }
  };

  const toggleTrailerPlayback = () => {
    const nextPlaying = !trailerPlaying;
    setTrailerPlaying(nextPlaying);
    if (trailer?.type === 'youtube') {
      trailerIframeRef.current?.contentWindow?.postMessage(
        JSON.stringify({
          event: 'command',
          func: nextPlaying ? 'playVideo' : 'pauseVideo',
          args: []
        }),
        '*'
      );
    } else if (trailerVideoRef.current) {
      if (nextPlaying) {
        trailerVideoRef.current.play().catch(() => {});
      } else {
        trailerVideoRef.current.pause();
      }
    }
  };

  const handleClose = () => {
    closeDetail();
  };

  // Ensure full media data (synopsis, cast, audio_languages, genres) is loaded fresh from database
  useEffect(() => {
    if (!media?.id) return;
    supabase
      .from('media_items')
      .select('*')
      .eq('id', media.id)
      .maybeSingle()
      .then(({ data }) => {
        if (data) {
          openDetail({ ...media, ...data });
        }
      });
  }, [media?.id]);

  // 1. Fetch Watch Progress, User Likes, and Custom Lists
  useEffect(() => {
    if (!user || !media) return;

    const fetchUserMediaState = async () => {
      // Check user like
      const { data: likeData } = await supabase
        .from('user_likes')
        .select('media_id')
        .eq('user_id', user.id)
        .eq('media_id', media.id)
        .maybeSingle();
      setUserLiked(!!likeData);

      // Fetch all watch progress rows for this media item ordered by updated_at DESC
      const { data: allProgress } = await supabase
        .from('watch_progress')
        .select('*')
        .eq('user_id', user.id)
        .eq('media_id', media.id)
        .order('updated_at', { ascending: false });

      if (allProgress && allProgress.length > 0) {
        // Most recently updated row is the latest watched episode/movie
        const latestRow = allProgress[0];
        setWatchProgress(latestRow as any);

        if (latestRow.season_number && latestRow.season_number >= 1) {
          setSelectedSeasonNumber(latestRow.season_number);
        }

        // If it's a series episode, fetch episode info to display title
        if (latestRow.episode_id) {
          const { data: epData } = await supabase
            .from('episodes')
            .select('*')
            .eq('id', latestRow.episode_id)
            .maybeSingle();
          if (epData) {
            setActiveResumeEpisode(epData as Episode);
          }
        }

        // Populate lookup map for all episodes of this series
        const progMap: Record<string, { position_seconds: number; duration_seconds: number; is_completed: boolean }> = {};
        allProgress.forEach((p) => {
          if (p.episode_id) {
            progMap[p.episode_id] = {
              position_seconds: p.position_seconds || 0,
              duration_seconds: p.duration_seconds || 0,
              is_completed: p.is_completed || false,
            };
          }
        });
        setEpisodeProgressMap(progMap);
      } else {
        setWatchProgress(null);
        setActiveResumeEpisode(null);
        setEpisodeProgressMap({});
      }

      // Fetch user custom lists and membership
      const { data: lists } = await supabase
        .from('custom_lists')
        .select('*')
        .eq('user_id', user.id);
      if (lists) setCustomLists(lists as CustomList[]);

      const { data: listItems } = await supabase
        .from('custom_list_items')
        .select('list_id')
        .eq('media_id', media.id);
      if (listItems) {
        setInListIds(new Set(listItems.map((li) => li.list_id)));
      }
    };

    fetchUserMediaState();
  }, [user, media, progressRefreshTrigger]);

  // 2. Fetch Seasons & Episodes for Series (merging seasons table and distinct episode season numbers)
  useEffect(() => {
    if (!media || media.type !== 'series') return;

    const fetchSeasons = async () => {
      const { data: seasonsData } = await supabase
        .from('seasons')
        .select('*')
        .eq('media_id', media.id)
        .order('season_number', { ascending: true });

      const { data: epSeasons } = await supabase
        .from('episodes')
        .select('season_number')
        .eq('media_id', media.id);

      const seasonNums = new Set<number>();
      if (seasonsData && seasonsData.length > 0) {
        seasonsData.forEach(s => seasonNums.add(s.season_number));
      }
      if (epSeasons && epSeasons.length > 0) {
        epSeasons.forEach(e => {
          if (e.season_number) seasonNums.add(e.season_number);
        });
      }
      if (seasonNums.size === 0) {
        seasonNums.add(1);
      }

      const mergedSeasons: Season[] = Array.from(seasonNums)
        .sort((a, b) => a - b)
        .map(num => {
          const match = seasonsData?.find(s => s.season_number === num);
          return match || {
            id: `season-${num}`,
            media_id: media.id,
            season_number: num,
            title: `Season ${num}`,
            episode_count: 0
          };
        });

      setSeasons(mergedSeasons);
    };

    fetchSeasons();
  }, [media]);

  // 3. Fetch Episodes when Season changes (filtered strictly to selectedSeasonNumber)
  useEffect(() => {
    if (!media || media.type !== 'series') return;

    const fetchEpisodes = async () => {
      const { data: episodesData } = await supabase
        .from('episodes')
        .select('*')
        .eq('media_id', media.id)
        .eq('season_number', selectedSeasonNumber)
        .order('episode_number', { ascending: true });

      if (episodesData) {
        setEpisodes(episodesData as Episode[]);
      }
    };

    fetchEpisodes();
  }, [media, selectedSeasonNumber]);

  // 3B. Fetch all published episodes for this series chronologically
  useEffect(() => {
    if (!media || media.type !== 'series') {
      setAllSeriesEpisodes([]);
      return;
    }

    const fetchAllEpisodes = async () => {
      const { data } = await supabase
        .from('episodes')
        .select('*')
        .eq('media_id', media.id)
        .eq('is_published', true)
        .order('season_number', { ascending: true })
        .order('episode_number', { ascending: true });

      if (data) {
        setAllSeriesEpisodes(data as Episode[]);
      }
    };

    fetchAllEpisodes();
  }, [media?.id, progressRefreshTrigger]);

  // 4. Fetch Similar Titles (Recommendations)
  useEffect(() => {
    if (!media) return;

    const fetchSimilar = async () => {
      const firstGenre = media.genres?.[0] || 'Action';
      const { data } = await supabase
        .from('media_items')
        .select('*')
        .neq('id', media.id)
        .eq('is_published', true)
        .contains('genres', [firstGenre])
        .limit(6);

      if (data && data.length > 0) {
        setSimilarItems(data as MediaItem[]);
      } else {
        const { data: fallback } = await supabase
          .from('media_items')
          .select('*')
          .neq('id', media.id)
          .eq('is_published', true)
          .limit(6);
        if (fallback) setSimilarItems(fallback as MediaItem[]);
      }
    };

    fetchSimilar();
  }, [media]);

  // Like Toggle
  const handleToggleLike = async () => {
    if (!user || !media) return;
    const newLiked = !userLiked;
    setUserLiked(newLiked);

    if (newLiked) {
      await supabase.from('user_likes').insert({ user_id: user.id, media_id: media.id });
      showToast('Added to Liked Titles');
    } else {
      await supabase.from('user_likes').delete().eq('user_id', user.id).eq('media_id', media.id);
      showToast('Removed from Liked Titles');
    }
  };

  // Custom List Toggle
  const handleToggleList = async (listId: string) => {
    if (!user || !media) return;
    const isCurrentlyIn = inListIds.has(listId);
    const updated = new Set(inListIds);

    if (isCurrentlyIn) {
      updated.delete(listId);
      await supabase.from('custom_list_items').delete().eq('list_id', listId).eq('media_id', media.id);
      showToast('Removed from list');
    } else {
      updated.add(listId);
      await supabase.from('custom_list_items').insert({ list_id: listId, media_id: media.id });
      showToast('Added to list');
    }
    setInListIds(updated);
  };

  // Create List
  const handleCreateList = async () => {
    if (!user || !media || !newListName.trim() || isCreatingList) return;
    setIsCreatingList(true);
    try {
      const { data, error } = await supabase
        .from('custom_lists')
        .insert({ user_id: user.id, name: newListName.trim() })
        .select()
        .single();

      if (data && !error) {
        setCustomLists(prev => [...prev, data as CustomList]);
        await handleToggleList(data.id);
        setNewListName('');
        setShowListPopover(false);
      }
    } finally {
      setIsCreatingList(false);
    }
  };

  // Share Link with Visual Feedback & Toast
  const handleShare = async () => {
    if (!media) return;
    const shareUrl = `${window.location.origin}/home/${media.id}`;
    let copied = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(shareUrl);
        copied = true;
      }
    } catch {
      // Fallback below
    }

    if (!copied) {
      try {
        const textArea = document.createElement('textarea');
        textArea.value = shareUrl;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        copied = document.execCommand('copy');
        document.body.removeChild(textArea);
      } catch (e) {
        console.error('Fallback copy failed', e);
      }
    }

    setIsCopied(true);
    showToast('✓ Link copied to clipboard!');
    setTimeout(() => {
      setIsCopied(false);
    }, 2500);
  };

  const isSeries = media?.type === 'series';

  // Smart primary CTA resolution (Resume / Continue / Play Again / Play)
  const ctaResolution = React.useMemo(() => {
    if (!media) {
      return {
        mode: 'play',
        label: 'Play',
        subtitle: '',
        targetEpisode: null,
        targetSeekTime: 0,
        showProgressBar: false,
        progressPct: 0,
        remainingMinutes: 0
      };
    }
    if (!isSeries) {
      const hasMovieResume = Boolean(
        watchProgress &&
        !watchProgress.is_completed &&
        watchProgress.position_seconds > 5 &&
        (watchProgress.duration_seconds <= 0 || watchProgress.position_seconds < watchProgress.duration_seconds * 0.95)
      );
      const remainingMinutes = hasMovieResume && watchProgress?.duration_seconds
        ? Math.max(1, Math.round((watchProgress.duration_seconds - watchProgress.position_seconds) / 60))
        : 0;

      return {
        mode: hasMovieResume ? 'resume' : 'play',
        label: hasMovieResume ? 'Resume' : 'Play',
        subtitle: hasMovieResume ? `${remainingMinutes}m remaining` : '',
        targetEpisode: null,
        targetSeekTime: (hasMovieResume && watchProgress) ? watchProgress.position_seconds : 0,
        showProgressBar: hasMovieResume,
        progressPct: hasMovieResume && watchProgress?.duration_seconds
          ? (watchProgress.position_seconds / watchProgress.duration_seconds) * 100
          : 0,
        remainingMinutes,
      };
    }

    // Series Resolution
    const sortedEps = allSeriesEpisodes.length > 0 ? allSeriesEpisodes : episodes;
    if (sortedEps.length === 0) {
      return {
        mode: 'play',
        label: 'Play',
        subtitle: '',
        targetEpisode: null,
        targetSeekTime: 0,
        showProgressBar: false,
        progressPct: 0,
        remainingMinutes: 0,
      };
    }

    const firstEp = sortedEps[0];

    // Check watchProgress (most recently updated progress row)
    if (!watchProgress || !watchProgress.episode_id) {
      // Case 3: User has never watched any episode of this series
      return {
        mode: 'play',
        label: `Play S${firstEp.season_number}:E${firstEp.episode_number}`,
        subtitle: '',
        targetEpisode: firstEp,
        targetSeekTime: 0,
        showProgressBar: false,
        progressPct: 0,
        remainingMinutes: 0,
      };
    }

    const latestWatchedEp = sortedEps.find(e => e.id === watchProgress.episode_id) || activeResumeEpisode;
    const dur = watchProgress.duration_seconds || 0;
    const isCompleted = Boolean(
      watchProgress.is_completed ||
      (dur > 0 && (watchProgress.position_seconds >= dur - 30 || watchProgress.position_seconds / dur >= 0.95))
    );

    if (!isCompleted && watchProgress.position_seconds > 5 && latestWatchedEp) {
      // Case 1: Latest watched episode is In-Progress
      const remainingMinutes = dur > 0
        ? Math.max(1, Math.round((dur - watchProgress.position_seconds) / 60))
        : 0;
      const progressPct = dur > 0 ? (watchProgress.position_seconds / dur) * 100 : 0;

      return {
        mode: 'resume',
        label: `Resume S${latestWatchedEp.season_number}:E${latestWatchedEp.episode_number}`,
        subtitle: `S${latestWatchedEp.season_number}:E${latestWatchedEp.episode_number}${latestWatchedEp.title ? ` "${latestWatchedEp.title}"` : ''}`,
        targetEpisode: latestWatchedEp,
        targetSeekTime: watchProgress.position_seconds,
        showProgressBar: true,
        progressPct,
        remainingMinutes,
      };
    }

    if (isCompleted && latestWatchedEp) {
      // Case 2: Latest watched episode is Completed -> find next sequential episode
      const nextEp = sortedEps.find(e => 
        (e.season_number === latestWatchedEp.season_number && e.episode_number > latestWatchedEp.episode_number) ||
        (e.season_number > latestWatchedEp.season_number)
      );

      if (nextEp) {
        const nextEpProgress = episodeProgressMap[nextEp.id];
        const nextDur = nextEpProgress?.duration_seconds || 0;
        const nextIsCompleted = nextEpProgress?.is_completed || false;
        const nextSeekTime = (!nextIsCompleted && nextEpProgress && nextEpProgress.position_seconds > 5)
          ? nextEpProgress.position_seconds
          : 0;
        const remainingMinutes = nextSeekTime > 0 && nextDur > 0
          ? Math.max(1, Math.round((nextDur - nextSeekTime) / 60))
          : 0;

        return {
          mode: 'continue',
          label: `Continue S${nextEp.season_number}:E${nextEp.episode_number}`,
          subtitle: `Up Next: S${nextEp.season_number}:E${nextEp.episode_number}${nextEp.title ? ` "${nextEp.title}"` : ''}`,
          targetEpisode: nextEp,
          targetSeekTime: nextSeekTime,
          showProgressBar: nextSeekTime > 0,
          progressPct: nextSeekTime > 0 && nextDur > 0 ? (nextSeekTime / nextDur) * 100 : 0,
          remainingMinutes,
        };
      }

      // If no next episode exists (user finished entire series!)
      return {
        mode: 'play_again',
        label: `Play Again S${firstEp.season_number}:E${firstEp.episode_number}`,
        subtitle: 'All episodes watched',
        targetEpisode: firstEp,
        targetSeekTime: 0,
        showProgressBar: false,
        progressPct: 0,
        remainingMinutes: 0,
      };
    }

    // Default fallback
    return {
      mode: 'play',
      label: `Play S${firstEp.season_number}:E${firstEp.episode_number}`,
      subtitle: '',
      targetEpisode: firstEp,
      targetSeekTime: 0,
      showProgressBar: false,
      progressPct: 0,
      remainingMinutes: 0,
    };
  }, [isSeries, watchProgress, allSeriesEpisodes, episodes, activeResumeEpisode, episodeProgressMap]);

  // Speculatively pre-warm stream for primary CTA and first 2 season episodes
  useEffect(() => {
    if (!media) return;
    const target = ctaResolution.targetEpisode?.id || media.id;
    if (target) {
      prewarmStream(target, user?.id);
    }
    if (episodes && episodes.length > 0) {
      episodes.slice(0, 2).forEach((ep) => {
        if (ep.id !== target) {
          prewarmStream(ep.id, user?.id);
        }
      });
    }
  }, [media?.id, ctaResolution.targetEpisode?.id, episodes, user?.id]);

  const primaryButtonLabel = ctaResolution.label;
  const hasResume = ctaResolution.mode === 'resume' || (ctaResolution.mode === 'continue' && ctaResolution.showProgressBar);
  const remainingMinutes = ctaResolution.remainingMinutes;

  // Primary CTA Action: Play / Resume / Continue
  const handlePrimaryCTA = () => {
    if (!media) return;
    if (ctaResolution.targetEpisode) {
      if (selectedSeasonNumber !== ctaResolution.targetEpisode.season_number) {
        setSelectedSeasonNumber(ctaResolution.targetEpisode.season_number);
      }
      openPlayer(media, ctaResolution.targetEpisode, ctaResolution.targetSeekTime);
    } else {
      openPlayer(media, null, ctaResolution.targetSeekTime);
    }
  };

  if (!media) return null;

  return (
    <div className="fixed inset-0 z-50 bg-[#141414] overflow-y-auto text-white animate-fade-in">
      <div className="min-h-screen flex flex-col relative w-full">
        {/* Navigation Control: Top-Left Back Button for mobile and desktop */}
        <div className="absolute top-4 left-4 sm:top-6 sm:left-8 z-40">
          <button
            onClick={handleClose}
            aria-label="Back"
            className="px-3.5 py-2 bg-black/75 hover:bg-black text-white rounded-full transition duration-150 backdrop-blur-md border border-white/20 cursor-pointer flex items-center gap-2 shadow-2xl group"
          >
            <ArrowLeft className="w-5 h-5 group-hover:-translate-x-1 transition-transform" />
            <span className="text-xs font-bold tracking-wide">Back</span>
          </button>
        </div>

        {/* Top Video Trailer / Backdrop Hero with Seamless Bottom Fade */}
        <div 
          ref={trailerContainerRef} 
          className="relative w-full min-h-[500px] sm:min-h-[540px] md:min-h-[580px] h-[62vh] sm:h-[68vh] md:h-[72vh] bg-[#141414] overflow-hidden"
          style={{ contain: 'paint' }}
        >
          {/* Backdrop Image fallback */}
          <div className={`absolute inset-0 w-full h-full transition-opacity duration-700 ${
            trailerReady && !trailerError ? 'opacity-0' : 'opacity-100'
          }`}>
            <MediaImage
              title={media.title}
              posterUrl={media.poster_url}
              backdropUrl={media.backdrop_url}
              aspect="backdrop"
              fallbackYear={media.release_year}
              loading="eager"
            />
          </div>

          {/* Autoplaying Silent Trailer centered mathematically */}
          {trailer && !trailerError && (
            <div 
              className={`absolute inset-0 w-full h-full overflow-hidden pointer-events-none flex items-center justify-center transition-opacity duration-700 ${
                trailerReady ? 'opacity-100' : 'opacity-0'
              }`}
              style={{ contain: 'paint', clipPath: 'inset(0)' }}
            >
              {trailer.type === 'youtube' ? (
                <div 
                  className="relative w-full h-full flex items-center justify-center overflow-hidden pointer-events-none"
                  style={{ contain: 'paint', clipPath: 'inset(0)' }}
                >
                  <iframe
                    key={`${media.id}-${trailer.src}`}
                    ref={trailerIframeRef}
                    onLoad={handleTrailerIframeLoad}
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
                    title="Trailer Preview"
                    referrerPolicy="origin"
                    allow="autoplay; encrypted-media; picture-in-picture"
                    onError={() => {
                      markTrailerBlocked(trailer.src);
                      setTrailerError(true);
                      setTrailerReady(false);
                      setTrailerVerifiedPlayable(false);
                    }}
                  />
                </div>
              ) : (
                <video
                  key={`${media.id}-${trailer.src}`}
                  ref={trailerVideoRef}
                  src={trailer.src}
                  autoPlay
                  muted={trailerMuted}
                  loop
                  playsInline
                  className="w-full h-full object-cover"
                  onPlay={() => {
                    markTrailerPlayable(trailer.src);
                    setTrailerVerifiedPlayable(true);
                    setTrailerReady(true);
                    setTrailerError(false);
                  }}
                  onError={() => {
                    markTrailerBlocked(trailer.src);
                    setTrailerError(true);
                    setTrailerReady(false);
                    setTrailerVerifiedPlayable(false);
                  }}
                />
              )}
            </div>
          )}

          {/* Floating Trailer Media Controls: Moved to top-right on mobile to prevent overlapping continue watching title & progress bar */}
          {trailer && trailerReady && !trailerError && (
            <div className="absolute top-4 sm:top-auto sm:bottom-6 right-4 sm:right-8 z-30 flex items-center gap-2 sm:gap-2.5">
              <button
                onClick={toggleTrailerPlayback}
                aria-label={trailerPlaying ? 'Pause trailer' : 'Play trailer'}
                className="p-2 sm:p-3 bg-black/75 hover:bg-black text-white rounded-full transition backdrop-blur-md border border-white/25 cursor-pointer shadow-xl flex items-center justify-center group"
                title={trailerPlaying ? 'Pause Trailer' : 'Play Trailer'}
              >
                {trailerPlaying ? (
                  <Pause className="w-4 h-4 sm:w-5 sm:h-5 fill-current" />
                ) : (
                  <Play className="w-4 h-4 sm:w-5 sm:h-5 fill-current ml-0.5" />
                )}
              </button>

              <button
                onClick={toggleTrailerSound}
                aria-label={trailerMuted ? 'Unmute trailer' : 'Mute trailer'}
                className="p-2 sm:p-3 bg-black/75 hover:bg-black text-white rounded-full transition backdrop-blur-md border border-white/25 cursor-pointer shadow-xl flex items-center justify-center group"
                title={trailerMuted ? 'Unmute' : 'Mute'}
              >
                {trailerMuted ? <VolumeX className="w-4 h-4 sm:w-5 sm:h-5" /> : <Volume2 className="w-4 h-4 sm:w-5 sm:h-5" />}
              </button>
            </div>
          )}

          {/* Cinematic Vignette Gradients */}
          <div className="absolute inset-0 bg-gradient-to-r from-black/90 via-black/40 to-transparent pointer-events-none" />
          <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-black/30 pointer-events-none" />

          {/* Rich Bottom Fade Feature: Melts trailer box into below div seamlessly with solid #141414 base */}
          <div className="absolute inset-x-0 -bottom-[1px] h-48 sm:h-64 bg-gradient-to-t from-[#141414] from-30% via-[#141414]/95 via-60% to-transparent pointer-events-none z-10" />

          {/* Content Container (Title -> Genre -> Storyline -> Play Button) - Positioned comfortably above seamless bottom fade */}
          <div className="absolute bottom-5 sm:bottom-6 md:bottom-8 left-4 sm:left-8 md:left-14 right-4 sm:right-auto max-w-2xl z-20 space-y-2 sm:space-y-2.5">
            {/* STREAMVAULT Badge */}
            <div className="flex items-center gap-1.5 text-[10px] sm:text-[11px] font-black text-red-500 uppercase tracking-widest">
              <Film className="w-3.5 h-3.5" />
              <span>STREAMVAULT {media.type.toUpperCase()}</span>
            </div>

            {/* Title */}
            <h1 className="text-3xl sm:text-4xl md:text-5xl font-black text-white tracking-tight drop-shadow-2xl">
              {media.title}
            </h1>

            {/* Genre and Metadata Row below Title (Match percent removed) */}
            <div className="flex flex-wrap items-center gap-2 sm:gap-2.5 text-xs text-neutral-300">
              <span className="font-semibold">{media.release_year}</span>
              <span className="px-1.5 py-0.5 border border-white/30 bg-black/50 rounded text-[11px] text-white">
                {media.maturity_rating || 'U/A 16+'}
              </span>
              <span>
                {media.type === 'movie' 
                  ? `${Math.floor((media.runtime_minutes || 110) / 60)}h ${(media.runtime_minutes || 110) % 60}m`
                  : `${seasons.length || 1} ${seasons.length === 1 ? 'Season' : 'Seasons'}`}
              </span>
              {media.genres && media.genres.length > 0 && (
                <span className="text-neutral-300 font-semibold">
                  • {media.genres.join(', ')}
                </span>
              )}
            </div>

            {/* Storyline Synopsis below Genre with Read More option */}
            <div className="max-w-xl drop-shadow-md">
              <p className={`text-neutral-200 text-xs sm:text-sm leading-relaxed transition-all ${
                isSynopsisExpanded ? '' : 'line-clamp-2'
              }`}>
                {media.synopsis || 'An extraordinary cinematic experience streaming in high-definition on StreamVault.'}
              </p>
              {media.synopsis && media.synopsis.length > 110 && (
                <button
                  onClick={() => setIsSynopsisExpanded(!isSynopsisExpanded)}
                  className="mt-1 text-neutral-400 hover:text-white text-xs font-bold underline underline-offset-2 transition cursor-pointer"
                >
                  {isSynopsisExpanded ? 'Read Less' : 'Read More'}
                </button>
              )}
            </div>

            {/* Play Button, Action Controls, and Watch Trailer Button below Play */}
            <div className="space-y-2.5 pt-1">
              <div className="flex items-center gap-3">
                <button
                  onClick={handlePrimaryCTA}
                  onMouseEnter={() => {
                    const target = ctaResolution.targetEpisode?.id || media.id;
                    if (target) prewarmStream(target, user?.id);
                  }}
                  className="px-6 py-2.5 sm:px-7 sm:py-3 bg-white hover:bg-neutral-200 text-black font-black text-sm rounded-xl transition duration-200 flex items-center gap-2 shadow-2xl cursor-pointer"
                >
                  <Play className="w-5 h-5 fill-current ml-0.5" />
                  {primaryButtonLabel}
                </button>

                {/* Add to List Trigger */}
                <div className="relative">
                  <button
                    onClick={() => setShowListPopover(!showListPopover)}
                    className={`p-3 rounded-full border transition duration-200 cursor-pointer flex items-center justify-center ${
                      inListIds.size > 0 
                        ? 'bg-red-600/20 border-red-500/50 text-red-500' 
                        : 'bg-black/60 hover:bg-black border-white/20 text-white backdrop-blur-md'
                    }`}
                    title="Add to Custom List"
                  >
                    {inListIds.size > 0 ? <Check className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                  </button>

                  {/* Centered Add to My Lists Modal with Dark Backdrop Overlay */}
                  {showListPopover && (
                    <div
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowListPopover(false);
                        setNewListName('');
                      }}
                      className="fixed inset-0 z-[90] bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 animate-fade-in"
                    >
                      <div
                        onClick={(e) => e.stopPropagation()}
                        className="bg-[#181818] border border-neutral-700/80 rounded-2xl w-full max-w-sm p-5 shadow-2xl space-y-4 overflow-hidden"
                      >
                        {/* Header */}
                        <div className="flex items-center justify-between border-b border-neutral-800 pb-3">
                          <h3 className="text-white font-bold text-base">Add to My Lists</h3>
                          <button
                            type="button"
                            onClick={() => {
                              setShowListPopover(false);
                              setNewListName('');
                            }}
                            className="p-1 text-neutral-400 hover:text-white rounded-lg transition cursor-pointer"
                          >
                            <X className="w-5 h-5"/>
                          </button>
                        </div>

                        {/* Existing Lists */}
                        {customLists.length > 0 && (
                          <div className="max-h-48 overflow-y-auto space-y-1.5 pr-1">
                            {customLists.map((list) => {
                              const isInList = inListIds.has(list.id);
                              return (
                                <button
                                  key={list.id}
                                  type="button"
                                  onClick={async () => {
                                    await handleToggleList(list.id);
                                    setShowListPopover(false);
                                  }}
                                  className={`w-full px-3.5 py-2.5 rounded-xl text-xs sm:text-sm font-medium transition flex items-center justify-between cursor-pointer ${
                                    isInList
                                      ? 'bg-red-600/20 text-red-400 border border-red-500/40'
                                      : 'bg-neutral-800/70 text-neutral-200 hover:bg-neutral-800 border border-transparent'
                                  }`}
                                >
                                  <span className="truncate pr-2">{list.name}</span>
                                  {isInList ? <Check className="w-4 h-4 text-red-500 shrink-0"/> : <Plus className="w-4 h-4 text-neutral-400 shrink-0"/>}
                                </button>
                              );
                            })}
                          </div>
                        )}

                        {/* Create New List Input */}
                        <div className="pt-1 space-y-3">
                          <label className="block text-[11px] font-bold uppercase tracking-wider text-neutral-400">
                            Create New List
                          </label>
                          <input
                            type="text"
                            value={newListName}
                            onChange={(e) => setNewListName(e.target.value)}
                            placeholder="Enter new list name..."
                            maxLength={40}
                            className="w-full px-3.5 py-2.5 bg-neutral-900 border border-neutral-700 focus:border-red-500 rounded-xl text-sm text-white placeholder-neutral-500 outline-none transition"
                          />

                          {/* Bottom Action Buttons: Cancel & Add */}
                          <div className="flex items-center justify-end gap-2.5 pt-1">
                            <button
                              type="button"
                              onClick={() => {
                                setShowListPopover(false);
                                setNewListName('');
                              }}
                              className="px-4 py-2 rounded-xl bg-neutral-800 hover:bg-neutral-700 text-neutral-300 text-xs font-semibold transition cursor-pointer"
                            >
                              Cancel
                            </button>
                            <button
                              type="button"
                              disabled={!newListName.trim() || isCreatingList}
                              onClick={async () => {
                                if (!newListName.trim()) return;
                                await handleCreateList();
                              }}
                              className={`px-5 py-2 rounded-xl text-xs font-bold transition ${
                                newListName.trim() && !isCreatingList
                                  ? 'bg-red-600 hover:bg-red-700 text-white shadow-lg shadow-red-600/30 cursor-pointer'
                                  : 'bg-neutral-800 text-neutral-500 opacity-50 cursor-not-allowed'
                              }`}
                            >
                              Add
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Like Button */}
                <button
                  onClick={handleToggleLike}
                  className={`p-3 rounded-full border transition duration-200 cursor-pointer ${
                    userLiked 
                      ? 'bg-red-600/20 border-red-500/50 text-red-500' 
                      : 'bg-black/60 hover:bg-black border-white/20 text-white backdrop-blur-md'
                  }`}
                  title="Rate Title"
                >
                  <ThumbsUp className="w-4 h-4" />
                </button>

                {/* Share Button (Copies Link with Visual Feedback) */}
                <button
                  onClick={handleShare}
                  className={`p-3 rounded-full border transition duration-200 cursor-pointer flex items-center gap-1.5 ${
                    isCopied
                      ? 'bg-emerald-600/20 border-emerald-500/60 text-emerald-400'
                      : 'bg-black/60 hover:bg-black border-white/20 text-white backdrop-blur-md'
                  }`}
                  title={isCopied ? 'Copied!' : 'Share Title'}
                >
                  {isCopied ? (
                    <>
                      <Check className="w-4 h-4 text-emerald-400" />
                      <span className="text-xs font-bold text-emerald-400 pr-1">Copied!</span>
                    </>
                  ) : (
                    <Share2 className="w-4 h-4" />
                  )}
                </button>
              </div>

              {/* Watch Trailer Button placed below Play button (rendered only when verified playable or ready, zero 4-second popout) */}
              {trailer && !trailerError && (trailerReady || trailerVerifiedPlayable) && (
                <div className="flex items-center">
                  <button
                    onClick={() => {
                      openTrailer(media, trailer.src);
                    }}
                    className="px-5 py-2.5 sm:px-6 sm:py-2.5 bg-neutral-900/80 hover:bg-neutral-800 text-white font-bold text-xs sm:text-sm rounded-xl transition duration-200 flex items-center gap-2 border border-white/20 backdrop-blur-md shadow-xl cursor-pointer group"
                    title="Watch Official Trailer"
                  >
                    <Film className="w-4 h-4 text-red-500 group-hover:scale-110 transition-transform" />
                    <span>Watch Trailer</span>
                  </button>
                </div>
              )}
            </div>

            {/* Resume / Continue / Up Next Banner directly below CTA */}
            {(ctaResolution.subtitle || ctaResolution.showProgressBar) && (
              <div className="pt-2 max-w-md space-y-1.5">
                <div className="flex items-center justify-between text-xs text-neutral-300 font-semibold">
                  <span className="truncate pr-2">
                    {ctaResolution.subtitle}
                  </span>
                  {ctaResolution.remainingMinutes > 0 && (
                    <span className="text-neutral-400 font-mono text-[11px] whitespace-nowrap">
                      {ctaResolution.remainingMinutes}m remaining
                    </span>
                  )}
                </div>
                {ctaResolution.showProgressBar && (
                  <div className="w-full bg-neutral-800 h-2 rounded-full overflow-hidden shadow-inner">
                    <div 
                      className="bg-red-600 h-full rounded-full transition-all duration-300" 
                      style={{ 
                        width: `${Math.min(100, Math.max(0, ctaResolution.progressPct))}%` 
                      }} 
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Dedicated Page Below Content: Cast Credits & Episodes & Recommendations */}
        <div className="max-w-6xl mx-auto w-full px-4 sm:px-8 md:px-12 py-6 space-y-8 bg-[#141414] pb-24">
          {/* Cast & Credits Info Box */}
          <div className="bg-neutral-900/40 p-4 sm:p-6 rounded-2xl border border-neutral-800/60 flex flex-wrap gap-6 text-xs text-neutral-400">
            {media.cast_members && media.cast_members.length > 0 && (
              <div>
                <span className="text-neutral-500 font-medium">Starring: </span>
                <span className="text-neutral-300 font-semibold">
                  {media.cast_members.slice(0, 5).map(c => c.name).join(', ')}
                </span>
              </div>
            )}
            {media.creators_directors && media.creators_directors.length > 0 && (
              <div>
                <span className="text-neutral-500 font-medium">Creators/Director: </span>
                <span className="text-neutral-300 font-semibold">
                  {media.creators_directors.map(cd => cd.name).join(', ')}
                </span>
              </div>
            )}
            <div>
              <span className="text-neutral-500 font-medium">Audio: </span>
              <span className="text-neutral-300 font-semibold">
                {media.audio_languages && media.audio_languages.length > 0 
                  ? media.audio_languages.join(', ') 
                  : 'English'}
              </span>
            </div>
          </div>

          {/* Series Episode Selector */}
          {media.type === 'series' && (
            <div className="space-y-4 pt-4 border-t border-neutral-800">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <h3 className="text-white font-black text-lg sm:text-xl">Episodes</h3>
                </div>

                {/* Season Indicator: Season 1 Tab if 1 season, Custom Dropdown if > 1 season */}
                {seasons.length > 1 ? (
                  <div className="relative" ref={seasonMenuRef}>
                    <button
                      type="button"
                      onClick={() => setIsSeasonDropdownOpen(!isSeasonDropdownOpen)}
                      className="bg-[#1e1e1e] hover:bg-[#262626] text-white font-bold text-xs sm:text-sm px-3.5 sm:px-4 py-2 rounded-xl border border-neutral-700/80 hover:border-neutral-500 shadow-lg flex items-center gap-2 cursor-pointer transition active:scale-95"
                      aria-haspopup="true"
                      aria-expanded={isSeasonDropdownOpen}
                    >
                      <span>Season {selectedSeasonNumber}</span>
                      <ChevronDown className={`w-4 h-4 text-neutral-400 transition-transform duration-200 ${isSeasonDropdownOpen ? 'rotate-180 text-white' : ''}`} />
                    </button>

                    {isSeasonDropdownOpen && (
                      <div className="absolute right-0 top-full mt-2 w-48 sm:w-52 bg-[#1a1a1a]/95 backdrop-blur-xl border border-neutral-700/80 rounded-2xl shadow-2xl p-1.5 z-50 animate-fade-in divide-y divide-neutral-800/80">
                        <div className="px-3 py-1.5 text-[10px] font-bold text-neutral-400 uppercase tracking-wider">
                          Select Season
                        </div>
                        <div className="py-1 space-y-1 max-h-60 overflow-y-auto">
                          {seasons.map((s) => {
                            const isSelected = s.season_number === selectedSeasonNumber;
                            return (
                              <button
                                key={s.id || s.season_number}
                                type="button"
                                onClick={() => {
                                  setSelectedSeasonNumber(s.season_number);
                                  setIsSeasonDropdownOpen(false);
                                }}
                                className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs sm:text-sm font-semibold transition cursor-pointer ${
                                  isSelected
                                    ? 'bg-red-600/20 text-red-400 border border-red-500/30'
                                    : 'text-neutral-300 hover:bg-neutral-800/90 hover:text-white'
                                }`}
                              >
                                <span>Season {s.season_number}</span>
                                {isSelected && <Check className="w-4 h-4 text-red-500" />}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="px-3.5 py-1.5 bg-[#222222] text-white font-bold text-xs sm:text-sm rounded-xl border border-neutral-700 shadow-sm flex items-center gap-1.5">
                    <span>Season {seasons[0]?.season_number || 1}</span>
                  </div>
                )}
              </div>

              {/* Episode List */}
              <div className="space-y-3">
                {episodes.length > 0 ? (
                  episodes.map((ep) => (
                    <div
                      key={ep.id}
                      onMouseEnter={() => {
                        prewarmStream(ep.id, user?.id);
                      }}
                      onClick={() => {
                        const savedProg = episodeProgressMap[ep.id];
                        const startSec = (savedProg && savedProg.position_seconds > 5 && !savedProg.is_completed)
                          ? savedProg.position_seconds
                          : 0;
                        const resolvedEp = (savedProg && savedProg.duration_seconds > 0 && ep.duration_seconds <= 0)
                          ? { ...ep, duration_seconds: savedProg.duration_seconds }
                          : ep;
                        openPlayer(media, resolvedEp, startSec);
                      }}
                      className="group bg-[#1a1a1a] hover:bg-[#242424] p-3 sm:p-4 rounded-2xl flex items-center gap-4 cursor-pointer transition border border-neutral-800/80 hover:border-neutral-700"
                    >
                      {/* Thumbnail */}
                      <div className="relative w-28 sm:w-36 aspect-video rounded-xl overflow-hidden flex-shrink-0 bg-neutral-900">
                        <MediaImage
                          title={ep.title}
                          posterUrl={ep.still_url || media.backdrop_url || media.poster_url}
                          backdropUrl={ep.still_url || media.backdrop_url}
                          aspect="backdrop"
                          fallbackYear={media.release_year}
                          imgClassName="group-hover:scale-105 transition duration-300"
                        />
                        <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition">
                          <Play className="w-6 h-6 text-white fill-current drop-shadow-md" />
                        </div>
                        {/* Watch progress bar on thumbnail */}
                        {episodeProgressMap[ep.id] && episodeProgressMap[ep.id].position_seconds > 0 && (
                          <div className="absolute bottom-0 left-0 right-0 h-1.5 bg-black/60 z-20">
                            <div 
                              className="bg-red-600 h-full transition-all duration-300" 
                              style={{ 
                                width: `${Math.min(100, Math.max(0, (episodeProgressMap[ep.id].position_seconds / (episodeProgressMap[ep.id].duration_seconds || ep.duration_seconds || 1)) * 100))}%` 
                              }} 
                            />
                          </div>
                        )}
                      </div>

                      {/* Metadata */}
                      <div className="flex-1 space-y-1">
                        <div className="flex items-center justify-between">
                          <h4 className="text-white font-bold text-xs sm:text-sm group-hover:text-red-500 transition line-clamp-1">
                            {ep.episode_number}. {ep.title}
                          </h4>
                          <span className="text-neutral-400 text-xs sm:text-sm font-mono whitespace-nowrap pl-2">
                            {Math.round((ep.duration_seconds || 2700) / 60)}m
                          </span>
                        </div>
                        <p className="text-neutral-400 text-xs line-clamp-2 leading-relaxed">
                          {ep.synopsis}
                        </p>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="p-6 bg-[#181818] border border-neutral-800 rounded-2xl text-center space-y-1.5">
                    <p className="text-neutral-300 font-semibold text-sm">Episodes Ingestion Pending</p>
                    <p className="text-neutral-500 text-xs">Video files for this series can be dropped into <code className="text-neutral-400 font-mono">media/incoming/</code> or uploaded via the Admin Panel.</p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* More Like This (Recommendations) */}
          <div className="space-y-4 pt-4 border-t border-neutral-800">
            <h3 className="text-white font-black text-lg sm:text-xl">More Like This</h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
              {similarItems.map((item) => (
                <div
                  key={item.id}
                  onClick={() => openDetail(item)}
                  className="bg-[#1a1a1a] rounded-2xl overflow-hidden border border-neutral-800/80 hover:border-neutral-700 transition cursor-pointer group"
                >
                  <div className="relative aspect-video w-full bg-neutral-900">
                    <MediaImage
                      title={item.title}
                      posterUrl={item.poster_url}
                      backdropUrl={item.backdrop_url}
                      aspect="backdrop"
                      fallbackYear={item.release_year}
                      imgClassName="group-hover:scale-105 transition duration-300"
                    />
                  </div>
                  <div className="p-3.5 space-y-1">
                    <h4 className="text-white font-bold text-xs sm:text-sm line-clamp-1 group-hover:text-red-500 transition">
                      {item.title}
                    </h4>
                    <p className="text-neutral-400 text-[11px]">{item.release_year}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
