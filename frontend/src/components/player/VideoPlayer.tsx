import React, { useEffect, useRef, useState, useCallback } from 'react';
import Hls from 'hls.js';
import { 
  Play, Pause, RotateCcw, RotateCw, Volume2, VolumeX, Maximize, Minimize, 
  ArrowLeft, Settings, MessageSquare, FastForward, Check, X, Film 
} from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { usePlayerStore } from '../../store/usePlayerStore';
import { useAuthStore } from '../../store/useAuthStore';
import { useUIStore } from '../../store/useUIStore';
import { api } from '../../lib/api';
import { supabase } from '../../lib/supabase';
import { AudioTrack, Episode, MediaStream, SubtitleTrack, SavedTrackPreferences } from '../../types';
import { MediaImage } from '../common/MediaImage';

import { stripHtmlTags, extractSafeTrailer } from '../../lib/security';
import { getPrewarmedStream, invalidatePrewarmCache } from '../../lib/streamPrewarm';

interface VttCue {
  start: number;
  end: number;
  text: string;
}

function parseVttTime(timeStr: string): number {
  const clean = timeStr.trim();
  const parts = clean.split(':');
  if (parts.length === 3) {
    const hours = parseFloat(parts[0]);
    const minutes = parseFloat(parts[1]);
    const seconds = parseFloat(parts[2].replace(',', '.'));
    return hours * 3600 + minutes * 60 + seconds;
  } else if (parts.length === 2) {
    const minutes = parseFloat(parts[0]);
    const seconds = parseFloat(parts[1].replace(',', '.'));
    return minutes * 60 + seconds;
  }
  return 0;
}

function parseWebVTT(vttText: string): VttCue[] {
  const lines = vttText.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  const cues: VttCue[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i].trim();
    if (line.includes('-->')) {
      const parts = line.split('-->');
      if (parts.length === 2) {
        const startStr = parts[0].trim().split(/\s+/)[0];
        const endStr = parts[1].trim().split(/\s+/)[0];
        const start = parseVttTime(startStr);
        const end = parseVttTime(endStr);

        i++;
        const textLines: string[] = [];
        while (i < lines.length && lines[i].trim() !== '') {
          const tLine = lines[i].trim();
          if (!tLine.includes('-->')) {
            // Strip any HTML/script tags from WebVTT subtitle cues
            textLines.push(stripHtmlTags(tLine));
          }
          i++;
        }
        if (textLines.length > 0 && end > start) {
          cues.push({
            start,
            end,
            text: textLines.join('\n')
          });
        }
      }
    }
    i++;
  }
  return cues;
}

const cleanTrackLabel = (raw?: string | null): string => {
  if (!raw) return '';
  return stripHtmlTags(raw).trim();
};

export interface PlayerAudioTrack {
  index: number;
  id: number | string;
  label: string;
  lang: string;
  is_default?: boolean;
}

export interface PlayerSubtitleTrack {
  index: number;
  id: string;
  label: string;
  lang: string;
  url: string;
}

function findMatchingAudioIndex(
  tracks: PlayerAudioTrack[],
  prefs: SavedTrackPreferences | null | undefined
): number {
  if (!prefs || tracks.length === 0) return -1;
  // Priority 1: Label + Lang match
  if (prefs.audio_label && prefs.audio_lang) {
    const idx = tracks.findIndex(
      t => t.label.toLowerCase() === prefs.audio_label!.toLowerCase() &&
           t.lang.toLowerCase() === prefs.audio_lang!.toLowerCase()
    );
    if (idx !== -1) return idx;
  }
  // Priority 2: Label match
  if (prefs.audio_label) {
    const idx = tracks.findIndex(
      t => t.label.toLowerCase() === prefs.audio_label!.toLowerCase()
    );
    if (idx !== -1) return idx;
  }
  // Priority 3: Index match
  if (prefs.audio_index !== null && prefs.audio_index !== undefined && tracks[prefs.audio_index]) {
    return prefs.audio_index;
  }
  return -1;
}

function findMatchingSubtitleIndex(
  tracks: PlayerSubtitleTrack[],
  prefs: SavedTrackPreferences | null | undefined
): number {
  if (!prefs || tracks.length === 0) return -1;
  // Priority 1: Label + Lang match
  if (prefs.subtitle_label && prefs.subtitle_lang) {
    const idx = tracks.findIndex(
      t => t.label.toLowerCase() === prefs.subtitle_label!.toLowerCase() &&
           t.lang.toLowerCase() === prefs.subtitle_lang!.toLowerCase()
    );
    if (idx !== -1) return idx;
  }
  // Priority 2: Label match
  if (prefs.subtitle_label) {
    const idx = tracks.findIndex(
      t => t.label.toLowerCase() === prefs.subtitle_label!.toLowerCase()
    );
    if (idx !== -1) return idx;
  }
  // Priority 3: Index match
  if (prefs.subtitle_index !== null && prefs.subtitle_index !== undefined && prefs.subtitle_index >= 0 && tracks[prefs.subtitle_index]) {
    return prefs.subtitle_index;
  }
  return -1;
}

interface ReadyPlaybackConfig {
  targetId: string;
  manifestUrl: string;
  startAtSeconds: number;
}

export const VideoPlayer: React.FC = () => {
  const { media, episode, startSeconds, isTrailer, trailerUrl, closePlayer, playEpisode, openPlayer, setIsPlaying: setStoreIsPlaying } = usePlayerStore();
  const { user } = useAuthStore();
  const queryClient = useQueryClient();
  const { triggerProgressRefresh } = useUIStore();

  const trailer = isTrailer ? extractSafeTrailer(trailerUrl || media?.trailer_youtube_key) : null;

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const hlsRef = useRef<Hls | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const controlsTimeoutRef = useRef<any>(null);
  const progressBarRef = useRef<HTMLDivElement | null>(null);
  const isScrubbingRef = useRef<boolean>(false);
  const needsClockNudgeRef = useRef<boolean>(false);

  const initialTimeFromStore = (startSeconds && startSeconds > 5) ? startSeconds : 0;
  const [initialSeekTime, setInitialSeekTime] = useState<number>(initialTimeFromStore);
  const [currentTime, setCurrentTime] = useState<number>(initialTimeFromStore);
  const [isBuffering, setIsBuffering] = useState<boolean>(true);
  const hasRestoredProgressRef = useRef(false);
  const activeLoadedEpisodeIdRef = useRef<string | null>(null);
  const isSwitchingEpisodeRef = useRef<boolean>(false);
  const hasAppliedDefaultAudioRef = useRef<boolean>(false);
  const hasAppliedSavedSubRef = useRef<boolean>(false);
  const savedPrefsRef = useRef<SavedTrackPreferences | null>(null);

  const [playbackConfig, setPlaybackConfig] = useState<ReadyPlaybackConfig | null>(null);
  const initialSeekTimeRef = useRef<number>(initialSeekTime);
  initialSeekTimeRef.current = initialSeekTime;
  const streamTokenRef = useRef<{ token: string; expires: number; user_id: string } | null>(null);

  const initialKnownDuration =
    (episode?.duration_seconds && episode.duration_seconds > 0 ? episode.duration_seconds : 0) ||
    (media?.runtime_minutes && media.runtime_minutes > 0 ? media.runtime_minutes * 60 : 0) ||
    0;

  // Sync store isPlaying to false when player unmounts
  useEffect(() => {
    return () => {
      setStoreIsPlaying(false);
    };
  }, [setStoreIsPlaying]);

  // Reset restored progress guard, playback config, and duration whenever active target changes
  useEffect(() => {
    hasRestoredProgressRef.current = false;
    hasAppliedDefaultAudioRef.current = false;
    hasAppliedSavedSubRef.current = false;
    savedPrefsRef.current = null;
    setPlaybackConfig(null);
    const known = (episode?.duration_seconds && episode.duration_seconds > 0 ? episode.duration_seconds : 0) ||
      (media?.runtime_minutes && media.runtime_minutes > 0 ? media.runtime_minutes * 60 : 0) ||
      0;
    if (known > 0) {
      setDuration(known);
    }
  }, [media?.id, episode?.id]);

  const [activeCueText, setActiveCueText] = useState<string>('');
  const vttCacheRef = useRef<Record<string, VttCue[]>>({});
  const activeCuesRef = useRef<VttCue[]>([]);

  const safePlay = useCallback(async (video: HTMLVideoElement | null) => {
    if (!video) return;
    try {
      hlsRef.current?.startLoad();
      await video.play();
      setIsPlaying(true);
      setStoreIsPlaying(true);
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        console.warn('Playback interrupted non-fatally:', err);
      }
    }
  }, [setStoreIsPlaying]);

  // Playback state
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState<number>(initialKnownDuration);
  const [bufferedEnd, setBufferedEnd] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showControls, setShowControls] = useState(true);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);

  // Stream data from Supabase
  const [streamInfo, setStreamInfo] = useState<MediaStream | null>(null);
  const streamInfoRef = useRef<MediaStream | null>(null);
  useEffect(() => {
    streamInfoRef.current = streamInfo;
  }, [streamInfo]);
  const [nextEpisode, setNextEpisode] = useState<Episode | null>(null);
  const [countdownSeconds, setCountdownSeconds] = useState<number | null>(null);

  // Modals & Selectors
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const [showQualityMenu, setShowQualityMenu] = useState(false);
  const [showAudioSubModal, setShowAudioSubModal] = useState(false);

  // Quality rungs
  const [availableQualities, setAvailableQualities] = useState<string[]>(['Auto']);
  const [currentQuality, setCurrentQuality] = useState<string>('Auto');

  // Track selection state (reconciled with HLS & Admin)
  const [audioTracksList, setAudioTracksList] = useState<PlayerAudioTrack[]>([]);
  const [subtitleTracksList, setSubtitleTracksList] = useState<PlayerSubtitleTrack[]>([]);
  const [activeAudioIndex, setActiveAudioIndex] = useState<number>(0);
  const [pendingAudioIndex, setPendingAudioIndex] = useState<number>(0);
  const [activeSubtitleIndex, setActiveSubtitleIndex] = useState<number>(-1); // -1 = Off
  const [pendingSubtitleIndex, setPendingSubtitleIndex] = useState<number>(-1);

  // Trailer Error state: falls back to backdrop poster if trailer is missing or fails
  const [trailerError, setTrailerError] = useState(false);

  useEffect(() => {
    setTrailerError(false);
  }, [media?.id]);

  // Lock body & html scroll to eliminate background homepage scrollbar and page shift
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

  // Catch YouTube player errors in full-screen trailer mode
  useEffect(() => {
    if (!isTrailer) return;

    const handleMessage = (event: MessageEvent) => {
      try {
        const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        if (
          data &&
          (data.event === 'onError' ||
           data.event === 'onPlayerError' ||
           (data.info && (data.info.error || [2, 5, 100, 101, 150].includes(data.info))))
        ) {
          setTrailerError(true);
        }
      } catch {
        if (
          typeof event.data === 'string' &&
          (event.data.includes('onError') ||
           event.data.includes('onPlayerError') ||
           event.data.includes('"error"'))
        ) {
          setTrailerError(true);
        }
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [isTrailer]);

  // 1. Resolve Stream Manifest URL, Token, Episode, and Seek Position Atomically
  useEffect(() => {
    if (isTrailer || !media) {
      setPlaybackConfig(null);
      return;
    }

    let isMounted = true;
    const currentTargetId = episode?.id || media.id;

    const applyStreamData = (actualId: string, tokenData: any, tracksData: any, seekSec: number, manifestUrl: string) => {
      if (!isMounted) return;
      streamTokenRef.current = tokenData;

      const dur = (tokenData.duration_seconds && tokenData.duration_seconds > 0)
        ? tokenData.duration_seconds
        : (tracksData?.duration_seconds && tracksData.duration_seconds > 0)
        ? tracksData.duration_seconds
        : (episode?.duration_seconds && episode.duration_seconds > 0)
        ? episode.duration_seconds
        : 0;

      if (dur > 0) {
        setDuration((prev) => (prev > 0 ? prev : dur));
      }

      if (tokenData.available_qualities && Array.isArray(tokenData.available_qualities)) {
        setAvailableQualities(tokenData.available_qualities);
      }

      const rawSubs = (tracksData?.subtitle_tracks && tracksData.subtitle_tracks.length > 0)
        ? tracksData.subtitle_tracks
        : tokenData.subtitle_tracks;

      if (rawSubs && Array.isArray(rawSubs)) {
        const mappedSubs = rawSubs.map((sub: any, idx: number) => {
          const rawPath = sub.url || sub.vtt_path || sub.vtt_filename || `subs/sub_${idx}_${sub.lang || 'en'}.vtt`;
          const cleanLabel = cleanTrackLabel(sub.label) || `Subtitle ${idx + 1}`;
          return {
            index: idx,
            id: sub.id || `sub_${idx}`,
            label: cleanLabel,
            lang: sub.lang || 'en',
            url: api.getSubtitleUrl(actualId, rawPath, tokenData.token, tokenData.expires, tokenData.user_id)
          };
        });
        setSubtitleTracksList(mappedSubs);
      }

      if (seekSec > 5 && tokenData?.saved_preferences) {
        savedPrefsRef.current = tokenData.saved_preferences;
      } else {
        savedPrefsRef.current = null;
        if (seekSec <= 5) {
          setActiveSubtitleIndex(-1);
          setPendingSubtitleIndex(-1);
          activeCuesRef.current = [];
          setActiveCueText('');
        }
      }

      setInitialSeekTime(seekSec);
      setCurrentTime(seekSec);
      setPlaybackConfig({
        targetId: actualId,
        manifestUrl,
        startAtSeconds: seekSec
      });
    };

    // FAST PATH: Check pre-warm cache
    const expectedUserId = user?.id || 'anonymous';
    const prewarmed = getPrewarmedStream(currentTargetId, user?.id);
    if (prewarmed && prewarmed.tokenData && prewarmed.tokenData.user_id === expectedUserId && prewarmed.tokenData.expires * 1000 > Date.now() + 60000) {
      if (media.type === 'series' && !episode && prewarmed.tokenData.resolved_episode) {
        const resumeSec = (startSeconds && startSeconds > 0)
          ? startSeconds
          : (prewarmed.tokenData.resume_position_seconds || 0);
        setInitialSeekTime(resumeSec);
        if (resumeSec > 0) setCurrentTime(resumeSec);
        playEpisode(prewarmed.tokenData.resolved_episode as Episode, resumeSec);
        return;
      }

      const actualId = prewarmed.tokenData.resolved_id || currentTargetId;
      const seekSec = (startSeconds && startSeconds > 0)
        ? startSeconds
        : (prewarmed.tokenData.resume_position_seconds && prewarmed.tokenData.resume_position_seconds > 5)
        ? prewarmed.tokenData.resume_position_seconds
        : 0;

      applyStreamData(actualId, prewarmed.tokenData, prewarmed.tracks, seekSec, prewarmed.manifestUrl);
      return;
    }

    // STANDARD PATH: Fetch token directly from backend
    api.getStreamToken(currentTargetId, user?.id).then((tokenData) => {
      if (!isMounted || !tokenData) return;

      if (media.type === 'series' && !episode && tokenData.resolved_episode) {
        const resumeSec = (startSeconds && startSeconds > 0)
          ? startSeconds
          : (tokenData.resume_position_seconds || 0);
        setInitialSeekTime(resumeSec);
        if (resumeSec > 0) setCurrentTime(resumeSec);
        playEpisode(tokenData.resolved_episode as Episode, resumeSec);
        return;
      }

      const actualId = tokenData.resolved_id || currentTargetId;
      const manifestUrl = api.getManifestUrl(actualId, tokenData.token, tokenData.expires, tokenData.user_id);
      const seekSec = (startSeconds && startSeconds > 0)
        ? startSeconds
        : (tokenData.resume_position_seconds && tokenData.resume_position_seconds > 5)
        ? tokenData.resume_position_seconds
        : 0;

      applyStreamData(actualId, tokenData, null, seekSec, manifestUrl);
    }).catch((err) => {
      console.error('Failed to resolve streaming token:', err);
    });

    return () => {
      isMounted = false;
    };
  }, [media?.id, episode?.id, isTrailer, user?.id, startSeconds, playEpisode]);

  // 1a. Fetch next episode for Series and streamInfo from Supabase
  useEffect(() => {
    if (isTrailer || !media || !episode) {
      setNextEpisode(null);
      return;
    }
    supabase
      .from('episodes')
      .select('*')
      .eq('media_id', media.id)
      .eq('season_number', episode.season_number)
      .eq('episode_number', episode.episode_number + 1)
      .maybeSingle()
      .then(({ data }) => {
        if (data) setNextEpisode(data as Episode);
        else setNextEpisode(null);
      });
  }, [media?.id, episode?.id, isTrailer]);

  useEffect(() => {
    if (isTrailer || !playbackConfig?.targetId) return;
    const matchCol = episode ? 'episode_id' : 'media_id';
    supabase
      .from('media_streams')
      .select('*')
      .eq(matchCol, playbackConfig.targetId)
      .eq('processing_status', 'ready')
      .maybeSingle()
      .then(({ data }) => {
        if (data) {
          setStreamInfo(data as MediaStream);
          if (data.available_qualities && Array.isArray(data.available_qualities)) {
            setAvailableQualities(data.available_qualities);
          }
        }
      });
  }, [playbackConfig?.targetId, isTrailer, Boolean(episode)]);

  // 1b. Update subtitle tracks when streamInfo loads from Supabase (Never touches playbackConfig)
  useEffect(() => {
    if (!playbackConfig?.targetId || !streamInfo?.subtitle_tracks || !streamTokenRef.current) return;
    const subs = streamInfo.subtitle_tracks;
    if (Array.isArray(subs) && subs.length > 0) {
      const mappedSubs = subs.map((sub: any, idx: number) => {
        const rawPath = sub.url || sub.vtt_path || sub.vtt_filename || `subs/sub_${idx}_${sub.lang || 'en'}.vtt`;
        const cleanLabel = cleanTrackLabel(sub.label) || `Subtitle ${idx + 1}`;
        return {
          index: idx,
          id: sub.id || `sub_${idx}`,
          label: cleanLabel,
          lang: sub.lang || 'en',
          url: api.getSubtitleUrl(playbackConfig.targetId, rawPath, streamTokenRef.current!.token, streamTokenRef.current!.expires, streamTokenRef.current!.user_id)
        };
      });
      setSubtitleTracksList(mappedSubs);
    }
  }, [playbackConfig?.targetId, streamInfo?.subtitle_tracks]);

  // 1c. Reconcile saved or default subtitle track when subtitleTracksList is available
  useEffect(() => {
    if (!playbackConfig?.targetId || subtitleTracksList.length === 0) return;
    if (hasAppliedSavedSubRef.current) return;

    hasAppliedSavedSubRef.current = true;
    const prefs = savedPrefsRef.current;

    // Convention: subtitle_index === -1 means user explicitly turned subtitles off
    if (prefs?.subtitle_index === -1) {
      setActiveSubtitleIndex(-1);
      setPendingSubtitleIndex(-1);
      activeCuesRef.current = [];
      setActiveCueText('');
      return;
    }

    if (prefs && (prefs.subtitle_label || (prefs.subtitle_index !== null && prefs.subtitle_index !== undefined && prefs.subtitle_index >= 0))) {
      const matchedIdx = findMatchingSubtitleIndex(subtitleTracksList, prefs);
      if (matchedIdx >= 0) {
        const sub = subtitleTracksList[matchedIdx];
        setActiveSubtitleIndex(matchedIdx);
        setPendingSubtitleIndex(matchedIdx);
        if (sub && sub.url) {
          if (vttCacheRef.current[sub.url]) {
            activeCuesRef.current = vttCacheRef.current[sub.url];
            const cur = videoRef.current ? videoRef.current.currentTime : 0;
            const matched = activeCuesRef.current.find(c => cur >= c.start && cur <= c.end);
            if (matched) setActiveCueText(matched.text);
          } else {
            fetch(sub.url)
              .then(res => res.text())
              .then(text => {
                const parsed = parseWebVTT(text);
                vttCacheRef.current[sub.url] = parsed;
                activeCuesRef.current = parsed;
                const cur = videoRef.current ? videoRef.current.currentTime : 0;
                const matched = parsed.find(c => cur >= c.start && cur <= c.end);
                if (matched) setActiveCueText(matched.text);
              })
              .catch(err => console.warn('Failed to pre-fetch saved subtitle VTT:', err));
          }
        }
        return;
      }
    }

    // Default: Subtitles Off (-1) if no saved preference or on completion reset
    setActiveSubtitleIndex(-1);
    setPendingSubtitleIndex(-1);
    activeCuesRef.current = [];
    setActiveCueText('');
  }, [subtitleTracksList, playbackConfig?.targetId]);

  // Accurate, Real-Time Buffered Progress Bar
  const updateBufferedProgress = useCallback(() => {
    const video = videoRef.current;
    if (!video || !video.buffered || video.buffered.length === 0) return;
    const cur = video.currentTime;
    let activeEnd = 0;
    for (let i = 0; i < video.buffered.length; i++) {
      const start = video.buffered.start(i);
      const end = video.buffered.end(i);
      if (cur >= start - 1 && cur <= end + 1) {
        activeEnd = end;
        break;
      }
    }
    if (activeEnd === 0) {
      activeEnd = video.buffered.end(video.buffered.length - 1);
    }
    setBufferedEnd(activeEnd);
  }, []);

  // 2. Initialize HLS.js with startPosition
  useEffect(() => {
    if (isTrailer || !playbackConfig || !videoRef.current) return;

    const video = videoRef.current;
    const { manifestUrl, startAtSeconds, targetId } = playbackConfig;

    if (Hls.isSupported()) {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }

      const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: false,
        startPosition: startAtSeconds > 5 ? startAtSeconds : -1,
        startFragPrefetch: true,
        startLevel: -1,
        capLevelToPlayerSize: false,
        abrEwmaDefaultEstimate: 2500000,
        abrBandWidthFactor: 0.75,
        abrBandWidthUpFactor: 0.5,
        maxStarvationDelay: 2,
        maxBufferLength: 30,             // Keep white bar strictly ~30s ahead of red playhead
        maxMaxBufferLength: 45,          // Hard ceiling: never buffer more than 45s ahead
        maxBufferSize: 25 * 1000 * 1000, // Hard 25 MB memory/bandwidth cap ahead of playhead
        backBufferLength: 15,            // Evict segments >15s behind playhead to free RAM
        maxBufferHole: 0.5,
        highBufferWatchdogPeriod: 1,
        nudgeOffset: 0.1,
        nudgeMaxRetry: 5,
        fragLoadingTimeOut: 15000,
        manifestLoadingTimeOut: 10000,
        manifestLoadingMaxRetry: 3,
        levelLoadingMaxRetry: 3,
        fragLoadingMaxRetry: 4,
        renderTextTracksNatively: false,
      });

      hlsRef.current = hls;
      setIsBuffering(true);
      hls.loadSource(manifestUrl);
      hls.attachMedia(video);

      hls.on(Hls.Events.FRAG_BUFFERED, () => {
        updateBufferedProgress();
        const v = videoRef.current;
        if (v && v.paused && v.buffered && v.buffered.length > 0) {
          let activeEnd = 0;
          for (let i = 0; i < v.buffered.length; i++) {
            if (v.currentTime >= v.buffered.start(i) - 1 && v.currentTime <= v.buffered.end(i) + 1) {
              activeEnd = v.buffered.end(i);
              break;
            }
          }
          if (activeEnd === 0) {
            activeEnd = v.buffered.end(v.buffered.length - 1);
          }
          if (activeEnd - v.currentTime >= 15) {
            hlsRef.current?.stopLoad();
          }
        }
        if (videoRef.current && videoRef.current.readyState >= 3) {
          setIsBuffering(false);
          if (needsClockNudgeRef.current) {
            needsClockNudgeRef.current = false;
            if (videoRef.current) {
              videoRef.current.currentTime = videoRef.current.currentTime;
            }
          }
        }
      });

      const syncAudioTracks = () => {
        if (!hls.audioTracks || hls.audioTracks.length === 0) return;

        const streamTracks = streamInfoRef.current?.audio_tracks || streamInfo?.audio_tracks || [];

        const mapped: PlayerAudioTrack[] = hls.audioTracks.map((t, idx) => {
          // Accurately match HLS rendition to Supabase stream track by playlist URI, id (audio_${idx}), or exact index
          const tUrl = (t as any).url || (t as any).uri || '';
          const streamTrack = streamTracks.find((st: any) => {
            if (st.playlist && tUrl && tUrl.includes(st.playlist)) return true;
            if (st.id && tUrl && (tUrl.includes(`/${st.id}/`) || tUrl.includes(`${st.id}/`) || tUrl.endsWith(st.id) || tUrl.includes(st.id))) return true;
            if (st.index !== undefined && tUrl && tUrl.includes(`audio_${st.index}`)) return true;
            if (st.index !== undefined && st.index === idx) return true;
            return false;
          }) || streamTracks[idx];

          const adminLabel = cleanTrackLabel(streamTrack?.label);
          const hlsName = cleanTrackLabel(t.name && !t.name.startsWith('audio_') ? t.name : '');
          const trackName = adminLabel || hlsName || `Audio ${idx + 1}`;
          const isDefault = Boolean(t.default || streamTrack?.is_default);
          return {
            index: idx,
            id: idx,
            label: trackName,
            lang: t.lang || streamTrack?.lang || 'und',
            is_default: isDefault,
          };
        });

        setAudioTracksList(mapped);

        // Only lock hasAppliedDefaultAudioRef.current = true once streamTracks.length > 0 (or when t.default === true in master.m3u8),
        // so if streamInfo arrives after MANIFEST_PARSED, the useEffect([streamInfo]) hook still switches hls.audioTrack to the is_default track!
        const hasDefInHls = mapped.some(t => t.is_default);
        const shouldLock = streamTracks.length > 0 || hasDefInHls || Boolean(savedPrefsRef.current);

        if (!hasAppliedDefaultAudioRef.current) {
          let targetIdx = findMatchingAudioIndex(mapped, savedPrefsRef.current);
          if (targetIdx === -1) {
            const preferredIdx = mapped.findIndex(t => t.is_default);
            targetIdx = preferredIdx !== -1 ? preferredIdx : (hls.audioTrack >= 0 ? hls.audioTrack : 0);
          }
          if (shouldLock) {
            hasAppliedDefaultAudioRef.current = true;
          }
          if (hls.audioTrack !== targetIdx && targetIdx < hls.audioTracks.length) {
            hls.audioTrack = targetIdx;
          }
          setActiveAudioIndex(targetIdx);
          setPendingAudioIndex(targetIdx);
        } else {
          const curIdx = hls.audioTrack >= 0 ? hls.audioTrack : 0;
          setActiveAudioIndex(curIdx);
          setPendingAudioIndex(curIdx);
        }
      };

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        syncAudioTracks();
        if (hls.levels && hls.levels.length > 1) {
          setCurrentQuality('Auto');
          hls.currentLevel = -1;
          const derived = ['Auto'];
          const heights = Array.from(new Set(hls.levels.map(l => l.height).filter(Boolean))).sort((a, b) => (b as number) - (a as number));
          heights.forEach(h => derived.push(`${h}p`));
          setAvailableQualities(derived);
        } else {
          setCurrentQuality('Original');
          setAvailableQualities(['Original']);
        }

        const onLoadedMeta = () => {
          if (!hasRestoredProgressRef.current) {
            hasRestoredProgressRef.current = true;
            if (startAtSeconds > 5 && Math.abs(video.currentTime - startAtSeconds) > 4) {
              video.currentTime = startAtSeconds;
            }
          }
        };
        if (video.readyState >= 1) {
          onLoadedMeta();
        } else {
          video.addEventListener('loadedmetadata', onLoadedMeta, { once: true });
        }

        // Release switching lock and register the active loaded episode
        isSwitchingEpisodeRef.current = false;
        if (episode?.id) {
          activeLoadedEpisodeIdRef.current = episode.id;
        } else if (targetId) {
          activeLoadedEpisodeIdRef.current = targetId;
        }

        safePlay(video);
      });

      hls.on(Hls.Events.AUDIO_TRACKS_UPDATED, () => {
        syncAudioTracks();
      });

      hls.on(Hls.Events.ERROR, (_, data) => {
        // Intercept 404 / 410 when media or playlist was removed from disk/library by admin
        const httpStatus = data.response?.code || (data.response as any)?.status;
        if (httpStatus === 404 || httpStatus === 410) {
          console.warn('[VideoPlayer] Stream returned 404/410 (title removed):', data);
          try {
            hls.destroy();
          } catch {}
          if (document.fullscreenElement) {
            document.exitFullscreen().catch(() => {});
          }
          closePlayer();
          useUIStore.getState().showToast('Playback Unavailable: This title was removed by the administrator.');
          queryClient.invalidateQueries({ queryKey: ['home_feed'] });
          queryClient.invalidateQueries({ queryKey: ['watch_progress'] });
          queryClient.invalidateQueries({ queryKey: ['watch_history'] });
          queryClient.invalidateQueries({ queryKey: ['all_published_media'] });
          triggerProgressRefresh();
          return;
        }

        if (
          data.details === Hls.ErrorDetails.BUFFER_STALLED_ERROR ||
          data.details === Hls.ErrorDetails.FRAG_LOAD_TIMEOUT
        ) {
          setIsBuffering(true);
          if (hls.currentLevel > 0 && currentQuality === 'Auto') {
            hls.nextLoadLevel = Math.max(0, hls.currentLevel - 1);
          }
          hls.startLoad();
          needsClockNudgeRef.current = true;
          if (videoRef.current) {
            videoRef.current.currentTime = videoRef.current.currentTime;
          }
        }
        if (data.fatal) {
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              hls.startLoad();
              break;
            case Hls.ErrorTypes.MEDIA_ERROR:
              hls.recoverMediaError();
              break;
            default:
              hls.destroy();
              break;
          }
        }
      });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Native Safari / iOS fallback
      video.src = manifestUrl;
      const onLoadedMeta = () => {
        if (!hasRestoredProgressRef.current) {
          hasRestoredProgressRef.current = true;
          if (startAtSeconds > 5 && Math.abs(video.currentTime - startAtSeconds) > 4) {
            video.currentTime = startAtSeconds;
          }
        }
        isSwitchingEpisodeRef.current = false;
        if (episode?.id) {
          activeLoadedEpisodeIdRef.current = episode.id;
        } else if (targetId) {
          activeLoadedEpisodeIdRef.current = targetId;
        }
        safePlay(video);
      };
      if (video.readyState >= 1) {
        onLoadedMeta();
      } else {
        video.addEventListener('loadedmetadata', onLoadedMeta, { once: true });
      }
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [playbackConfig?.manifestUrl, isTrailer, safePlay, updateBufferedProgress]);

  // 2a. Re-sync audio tracks when streamInfo loads from Supabase
  useEffect(() => {
    if (hlsRef.current && hlsRef.current.audioTracks && hlsRef.current.audioTracks.length > 0) {
      const hls = hlsRef.current;
      const streamTracks = streamInfo?.audio_tracks || [];
      if (streamTracks.length === 0) return;

      const mapped: PlayerAudioTrack[] = hls.audioTracks.map((t, idx) => {
        const tUrl = (t as any).url || (t as any).uri || '';
        const streamTrack = streamTracks.find((st: any) => {
          if (st.playlist && tUrl && tUrl.includes(st.playlist)) return true;
          if (st.id && tUrl && (tUrl.includes(`/${st.id}/`) || tUrl.includes(`${st.id}/`) || tUrl.endsWith(st.id) || tUrl.includes(st.id))) return true;
          if (st.index !== undefined && tUrl && tUrl.includes(`audio_${st.index}`)) return true;
          if (st.index !== undefined && st.index === idx) return true;
          return false;
        }) || streamTracks[idx];

        const adminLabel = cleanTrackLabel(streamTrack?.label);
        const hlsName = cleanTrackLabel(t.name && !t.name.startsWith('audio_') ? t.name : '');
        const trackName = adminLabel || hlsName || `Audio ${idx + 1}`;
        const isDefault = Boolean(t.default || streamTrack?.is_default);
        return {
          index: idx,
          id: idx,
          label: trackName,
          lang: t.lang || streamTrack?.lang || 'und',
          is_default: isDefault,
        };
      });

      setAudioTracksList(mapped);

      if (!hasAppliedDefaultAudioRef.current) {
        let targetIdx = findMatchingAudioIndex(mapped, savedPrefsRef.current);
        if (targetIdx === -1) {
          const preferredIdx = mapped.findIndex(t => t.is_default);
          targetIdx = preferredIdx !== -1 ? preferredIdx : (hls.audioTrack >= 0 ? hls.audioTrack : 0);
        }
        hasAppliedDefaultAudioRef.current = true;
        if (hls.audioTrack !== targetIdx && targetIdx < hls.audioTracks.length) {
          hls.audioTrack = targetIdx;
        }
        setActiveAudioIndex(targetIdx);
        setPendingAudioIndex(targetIdx);
      }
    }
  }, [streamInfo]);

  // 2b. If in video trailer mode, load trailer video directly
  useEffect(() => {
    if (!isTrailer || !trailer || trailer.type !== 'video' || !videoRef.current) return;
    const video = videoRef.current;
    video.play().catch(() => {
      setIsPlaying(false);
      setStoreIsPlaying(false);
    });
  }, [isTrailer, trailer, setStoreIsPlaying]);

  // 3. Heartbeat Watch Progress Synchronization (Guarded against episode cross-pollution)
  const syncProgress = useCallback(async () => {
    if (!videoRef.current || !media || !user || isTrailer) return;
    // Strictly abort if currently transitioning episodes
    if (isSwitchingEpisodeRef.current) return;

    // For series: ensure the loaded episode matches the current target episode
    const currentEpId = episode?.id || null;
    if (media.type === 'series') {
      if (!currentEpId || activeLoadedEpisodeIdRef.current !== currentEpId) {
        return;
      }
    }

    const pos = videoRef.current.currentTime;
    const dur = videoRef.current.duration || 0;
    // Guard: Only save if video.currentTime > 3 and video.duration > 0 so opening doesn't overwrite saved progress with 0:00!
    if (pos <= 3 || dur <= 0) return;

    const targetEpisodeId = media.type === 'series' ? activeLoadedEpisodeIdRef.current : null;
    if (media.type === 'series' && !targetEpisodeId) return;

    const chosenAudio = audioTracksList[activeAudioIndex];
    const chosenSub = activeSubtitleIndex >= 0 ? subtitleTracksList[activeSubtitleIndex] : null;

    try {
      await supabase.rpc('sync_watch_progress', {
        p_media_id: media.id,
        p_episode_id: targetEpisodeId,
        p_season_number: episode?.season_number || null,
        p_episode_number: episode?.episode_number || null,
        p_position_seconds: pos,
        p_duration_seconds: dur,
        p_audio_index: audioTracksList.length > 0 ? activeAudioIndex : null,
        p_audio_lang: chosenAudio?.lang || null,
        p_audio_label: chosenAudio?.label || null,
        p_subtitle_index: activeSubtitleIndex,
        p_subtitle_lang: chosenSub?.lang || null,
        p_subtitle_label: chosenSub?.label || null
      });
    } catch (e) {
      console.warn('Progress sync failed non-fatally:', e);
    }
  }, [media, episode, user, isTrailer, activeAudioIndex, activeSubtitleIndex, audioTracksList, subtitleTracksList]);

  useEffect(() => {
    if (!isPlaying || isTrailer) return;
    const interval = setInterval(syncProgress, 5000);
    const handleBeforeUnload = () => {
      if (!isSwitchingEpisodeRef.current) {
        syncProgress();
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      clearInterval(interval);
      window.removeEventListener('beforeunload', handleBeforeUnload);
      if (!isSwitchingEpisodeRef.current) {
        syncProgress();
      }
    };
  }, [isPlaying, isTrailer, syncProgress]);

  const handleExitPlayer = async () => {
    if (document.fullscreenElement || (document as any).webkitFullscreenElement) {
      try {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if ((document as any).webkitExitFullscreen) (document as any).webkitExitFullscreen();
      } catch {}
    }
    if (window.screen?.orientation?.unlock) {
      try {
        window.screen.orientation.unlock();
      } catch {}
    }
    if (!isSwitchingEpisodeRef.current) {
      await syncProgress();
    }
    if (playbackConfig?.targetId) {
      invalidatePrewarmCache(playbackConfig.targetId);
    } else {
      invalidatePrewarmCache();
    }
    queryClient.invalidateQueries({ queryKey: ['home_feed'] });
    queryClient.invalidateQueries({ queryKey: ['watch_progress'] });
    queryClient.invalidateQueries({ queryKey: ['watch_history'] });
    triggerProgressRefresh();
    closePlayer();
  };

  // Safe Episode Switcher: Prevents timestamp bleed and cross-episode watch_progress pollution
  const handleSwitchToEpisode = async (targetNextEp: Episode) => {
    if (!targetNextEp) return;

    // 1. Explicitly save current episode progress if valid before switching
    if (activeLoadedEpisodeIdRef.current && videoRef.current && !isSwitchingEpisodeRef.current) {
      const curPos = videoRef.current.currentTime;
      const curDur = videoRef.current.duration || 0;
      if (curPos > 3 && curDur > 0 && user && media) {
        const chosenAudio = audioTracksList[activeAudioIndex];
        const chosenSub = activeSubtitleIndex >= 0 ? subtitleTracksList[activeSubtitleIndex] : null;
        try {
          await supabase.rpc('sync_watch_progress', {
            p_media_id: media.id,
            p_episode_id: activeLoadedEpisodeIdRef.current,
            p_season_number: episode?.season_number || null,
            p_episode_number: episode?.episode_number || null,
            p_position_seconds: curPos,
            p_duration_seconds: curDur,
            p_audio_index: audioTracksList.length > 0 ? activeAudioIndex : null,
            p_audio_lang: chosenAudio?.lang || null,
            p_audio_label: chosenAudio?.label || null,
            p_subtitle_index: activeSubtitleIndex,
            p_subtitle_lang: chosenSub?.lang || null,
            p_subtitle_label: chosenSub?.label || null
          });
        } catch (e) {
          console.warn('Pre-switch progress sync error:', e);
        }
      }
    }

    // 2. Lock switching and invalidate active loaded episode
    if (activeLoadedEpisodeIdRef.current) {
      invalidatePrewarmCache(activeLoadedEpisodeIdRef.current);
    }
    isSwitchingEpisodeRef.current = true;
    activeLoadedEpisodeIdRef.current = null;

    // 3. Pause video and destroy HLS instance immediately
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = 0;
    }
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    // 4. Synchronously reset seek time and flags
    setIsBuffering(true);
    setPlaybackConfig(null);
    setInitialSeekTime(0);
    hasRestoredProgressRef.current = false;
    hasAppliedDefaultAudioRef.current = false;
    hasAppliedSavedSubRef.current = false;
    savedPrefsRef.current = null;
    setActiveSubtitleIndex(-1);
    setPendingSubtitleIndex(-1);
    activeCuesRef.current = [];
    setActiveCueText('');
    setCurrentTime(0);
    const nextEpDur = (targetNextEp.duration_seconds && targetNextEp.duration_seconds > 0) ? targetNextEp.duration_seconds : 0;
    setDuration(nextEpDur);
    setCountdownSeconds(null);

    // 5. Query prewarm cache or Supabase watch_progress specifically for next episode
    let resumeTime = 0;
    const pre = getPrewarmedStream(targetNextEp.id, user?.id);
    if (pre && pre.tokenData && pre.tokenData.user_id === (user?.id || 'anonymous') && pre.tokenData.resume_position_seconds !== undefined) {
      resumeTime = pre.tokenData.resume_position_seconds;
      if (resumeTime > 5 && pre.tokenData.saved_preferences) {
        savedPrefsRef.current = pre.tokenData.saved_preferences;
      }
    } else if (user) {
      try {
        const { data: record } = await supabase
          .from('watch_progress')
          .select('position_seconds, duration_seconds, is_completed, preferred_audio_index, preferred_audio_lang, preferred_audio_label, preferred_subtitle_index, preferred_subtitle_lang, preferred_subtitle_label')
          .eq('user_id', user.id)
          .eq('episode_id', targetNextEp.id)
          .maybeSingle();

        if (record) {
          const dur = record.duration_seconds || 0;
          if (dur > 0) {
            setDuration((prev) => (prev > 0 ? prev : dur));
          }
          const isCompleted = record.is_completed || (dur > 0 && (record.position_seconds >= dur - 30 || record.position_seconds / dur >= 0.95));
          if (!isCompleted && record.position_seconds > 5) {
            resumeTime = record.position_seconds;
            if (record.preferred_audio_index !== null || record.preferred_subtitle_index !== null || record.preferred_audio_label || record.preferred_subtitle_label) {
              savedPrefsRef.current = {
                audio_index: record.preferred_audio_index ?? null,
                audio_lang: record.preferred_audio_lang ?? null,
                audio_label: record.preferred_audio_label ?? null,
                subtitle_index: record.preferred_subtitle_index ?? null,
                subtitle_lang: record.preferred_subtitle_lang ?? null,
                subtitle_label: record.preferred_subtitle_label ?? null,
              };
            }
          }
        }
      } catch (err) {
        console.warn('Failed to fetch next episode watch progress:', err);
      }
    }

    setInitialSeekTime(resumeTime);
    if (resumeTime > 0) setCurrentTime(resumeTime);
    playEpisode(targetNextEp, resumeTime);
  };

  // 4. Video Event Handlers & Auto-advance Countdown
  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    const cur = videoRef.current.currentTime;
    const dur = videoRef.current.duration || 0;

    const targetStart = playbackConfig?.startAtSeconds || initialSeekTimeRef.current;
    // Guard: while isBuffering is true and targetStart > 5 and cur < 2,
    // keep displaying targetStart so the UI timer never flashes 0:00 before jumping to resume timestamp
    if (isBuffering && targetStart > 5 && cur < 2) {
      setCurrentTime(targetStart);
    } else {
      setCurrentTime(cur);
    }

    if (dur > 0 && !isNaN(dur) && isFinite(dur)) {
      setDuration(dur);
    }

    // Sync WebVTT subtitles overlay
    if (activeSubtitleIndex >= 0 && activeCuesRef.current.length > 0) {
      const matchedCue = activeCuesRef.current.find(c => cur >= c.start && cur <= c.end);
      const text = matchedCue ? matchedCue.text : '';
      setActiveCueText(prev => (prev !== text ? text : prev));
    } else if (activeCueText) {
      setActiveCueText('');
    }

    // Buffer range
    updateBufferedProgress();

    // Auto-advance Countdown during last 15 seconds for series
    if (nextEpisode && dur > 30 && dur - cur <= 15) {
      setCountdownSeconds(Math.max(1, Math.round(dur - cur)));
    } else {
      setCountdownSeconds(null);
    }
  };

  const clearControlsTimer = useCallback(() => {
    if (controlsTimeoutRef.current) {
      clearTimeout(controlsTimeoutRef.current);
      controlsTimeoutRef.current = null;
    }
  }, []);

  const scheduleControlsHide = useCallback(() => {
    clearControlsTimer();
    controlsTimeoutRef.current = setTimeout(() => {
      if (!showAudioSubModal && !showQualityMenu && !showSpeedMenu && !isScrubbingRef.current) {
        setShowControls(false);
      }
    }, 3500);
  }, [clearControlsTimer, showAudioSubModal, showQualityMenu, showSpeedMenu]);

  const togglePlay = () => {
    scheduleControlsHide();
    if (!videoRef.current) return;
    if (videoRef.current.paused) {
      hlsRef.current?.startLoad();
      safePlay(videoRef.current);
    } else {
      videoRef.current.pause();
      setIsPlaying(false);
      setStoreIsPlaying(false);
      const v = videoRef.current;
      if (v && v.buffered && v.buffered.length > 0) {
        let activeEnd = 0;
        for (let i = 0; i < v.buffered.length; i++) {
          if (v.currentTime >= v.buffered.start(i) - 1 && v.currentTime <= v.buffered.end(i) + 1) {
            activeEnd = v.buffered.end(i);
            break;
          }
        }
        if (activeEnd === 0) {
          activeEnd = v.buffered.end(v.buffered.length - 1);
        }
        if (activeEnd - v.currentTime >= 15) {
          hlsRef.current?.stopLoad();
        }
      }
      if (!isSwitchingEpisodeRef.current) {
        syncProgress();
      }
    }
  };

  const handleSeek = (newTime: number) => {
    scheduleControlsHide();
    if (!videoRef.current) return;
    hlsRef.current?.startLoad();
    videoRef.current.currentTime = newTime;
    setCurrentTime(newTime);
  };

  const skipSeconds = (seconds: number) => {
    scheduleControlsHide();
    if (!videoRef.current) return;
    hlsRef.current?.startLoad();
    videoRef.current.currentTime = Math.max(0, Math.min(videoRef.current.duration || 0, videoRef.current.currentTime + seconds));
  };

  const toggleMute = () => {
    scheduleControlsHide();
    if (!videoRef.current) return;
    videoRef.current.muted = !videoRef.current.muted;
    setIsMuted(videoRef.current.muted);
  };

  const handleVolumeChange = (newVol: number) => {
    scheduleControlsHide();
    if (!videoRef.current) return;
    videoRef.current.volume = newVol;
    setVolume(newVol);
    videoRef.current.muted = newVol === 0;
    setIsMuted(newVol === 0);
  };

  const handleSpeedSelect = (speed: number) => {
    if (!videoRef.current) return;
    videoRef.current.playbackRate = speed;
    setPlaybackSpeed(speed);
    setShowSpeedMenu(false);
  };

  const isMultiBitrate = Boolean(hlsRef.current && hlsRef.current.levels && hlsRef.current.levels.length > 1);
  const displayQualities = isMultiBitrate ? availableQualities : ['Original'];
  const effectiveQuality = !isMultiBitrate ? 'Original' : currentQuality;

  const handleQualitySelect = (quality: string) => {
    setCurrentQuality(quality);
    setShowQualityMenu(false);
    if (!hlsRef.current || !isMultiBitrate) return;
    setIsBuffering(true);

    if (quality === 'Auto') {
      hlsRef.current.currentLevel = -1; // ABR enabled
      hlsRef.current.loadLevel = -1;
    } else {
      // Find matching level height
      const targetHeight = parseInt(quality.replace('p', ''), 10);
      const levelIdx = hlsRef.current.levels.findIndex(l => l.height === targetHeight);
      if (levelIdx !== -1) {
        hlsRef.current.currentLevel = levelIdx; // Instant quality switch (flushes old buffer, loads new resolution at playhead)
      }
    }
  };

  // JioOS / Android TV Remote Control & D-pad Navigation in Video Player
  useEffect(() => {
    const handleRemoteKeyDown = (e: KeyboardEvent) => {
      // Don't intercept if an input is focused
      const targetTag = (e.target as HTMLElement)?.tagName?.toUpperCase();
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(targetTag)) {
        return;
      }

      switch (e.key) {
        case 'ArrowLeft':
          e.preventDefault();
          skipSeconds(-10);
          setShowControls(true);
          break;
        case 'ArrowRight':
          e.preventDefault();
          skipSeconds(10);
          setShowControls(true);
          break;
        case 'ArrowUp':
        case 'ArrowDown':
          e.preventDefault();
          setShowControls(true);
          scheduleControlsHide();
          break;
        case ' ':
        case 'Enter':
        case 'MediaPlayPause':
        case 'PlayPause':
          // If a focused element is a button inside controls, let Enter activate it; otherwise toggle playback
          if (targetTag !== 'BUTTON') {
            e.preventDefault();
            togglePlay();
          }
          break;
        case 'MediaPlay':
          e.preventDefault();
          if (videoRef.current?.paused) togglePlay();
          break;
        case 'MediaPause':
          e.preventDefault();
          if (!videoRef.current?.paused) togglePlay();
          break;
        case 'MediaFastForward':
          e.preventDefault();
          skipSeconds(10);
          break;
        case 'MediaRewind':
          e.preventDefault();
          skipSeconds(-10);
          break;
        case 'Escape':
        case 'GoBack':
        case 'BrowserBack':
          e.preventDefault();
          if (showAudioSubModal) {
            setShowAudioSubModal(false);
          } else if (showQualityMenu) {
            setShowQualityMenu(false);
          } else if (showSpeedMenu) {
            setShowSpeedMenu(false);
          } else {
            handleExitPlayer();
          }
          break;
      }
    };

    window.addEventListener('keydown', handleRemoteKeyDown, { passive: false });
    return () => {
      window.removeEventListener('keydown', handleRemoteKeyDown);
    };
  }, [togglePlay, skipSeconds, scheduleControlsHide, showAudioSubModal, showQualityMenu, showSpeedMenu, handleExitPlayer]);

  // Staged Audio & Subtitles Apply
  const openAudioSubModal = () => {
    setPendingAudioIndex(activeAudioIndex);
    setPendingSubtitleIndex(activeSubtitleIndex);
    setShowAudioSubModal(true);
    setShowQualityMenu(false);
    setShowSpeedMenu(false);
  };

  const cancelAudioSubChanges = () => {
    setPendingAudioIndex(activeAudioIndex);
    setPendingSubtitleIndex(activeSubtitleIndex);
    setShowAudioSubModal(false);
  };

  const applyAudioAndSubtitles = () => {
    const video = videoRef.current;
    const hls = hlsRef.current;
    if (!video) return;

    // 1. Switch HLS Audio Track cleanly without re-seeking or re-playing in a loop
    if (hls && pendingAudioIndex !== hls.audioTrack && pendingAudioIndex >= 0 && pendingAudioIndex < hls.audioTracks.length) {
      setIsBuffering(true);
      hls.audioTrack = pendingAudioIndex;
      setActiveAudioIndex(pendingAudioIndex);
    }

    // 2. Switch Active WebVTT Subtitle Track (custom deterministic overlay)
    if (pendingSubtitleIndex >= 0 && pendingSubtitleIndex < subtitleTracksList.length) {
      const sub = subtitleTracksList[pendingSubtitleIndex];
      setActiveSubtitleIndex(pendingSubtitleIndex);
      if (sub && sub.url) {
        if (vttCacheRef.current[sub.url]) {
          activeCuesRef.current = vttCacheRef.current[sub.url];
          const cur = video.currentTime;
          const matched = activeCuesRef.current.find(c => cur >= c.start && cur <= c.end);
          setActiveCueText(matched ? matched.text : '');
        } else {
          fetch(sub.url)
            .then(res => res.text())
            .then(text => {
              const parsed = parseWebVTT(text);
              vttCacheRef.current[sub.url] = parsed;
              activeCuesRef.current = parsed;
              const cur = video.currentTime;
              const matched = parsed.find(c => cur >= c.start && cur <= c.end);
              setActiveCueText(matched ? matched.text : '');
            })
            .catch(err => {
              console.warn('Failed to fetch subtitle VTT:', err);
            });
        }
      }
    } else {
      setActiveSubtitleIndex(-1);
      activeCuesRef.current = [];
      setActiveCueText('');
    }

    // 3. Persist chosen preferences immediately
    const chosenAudio = audioTracksList[pendingAudioIndex];
    const chosenSub = pendingSubtitleIndex >= 0 ? subtitleTracksList[pendingSubtitleIndex] : null;

    savedPrefsRef.current = {
      audio_index: pendingAudioIndex >= 0 ? pendingAudioIndex : null,
      audio_lang: chosenAudio?.lang || null,
      audio_label: chosenAudio?.label || null,
      subtitle_index: pendingSubtitleIndex,
      subtitle_lang: chosenSub?.lang || null,
      subtitle_label: chosenSub?.label || null,
    };

    if (playbackConfig?.targetId) {
      invalidatePrewarmCache(playbackConfig.targetId);
    }

    if (media && user && !isTrailer) {
      const pos = video.currentTime;
      const dur = video.duration || 0;
      if (pos > 0 && dur > 0) {
        const targetEpisodeId = media.type === 'series' ? activeLoadedEpisodeIdRef.current : null;
        Promise.resolve(
          supabase.rpc('sync_watch_progress', {
            p_media_id: media.id,
            p_episode_id: targetEpisodeId,
            p_season_number: episode?.season_number || null,
            p_episode_number: episode?.episode_number || null,
            p_position_seconds: pos,
            p_duration_seconds: dur,
            p_audio_index: pendingAudioIndex >= 0 ? pendingAudioIndex : null,
            p_audio_lang: chosenAudio?.lang || null,
            p_audio_label: chosenAudio?.label || null,
            p_subtitle_index: pendingSubtitleIndex,
            p_subtitle_lang: chosenSub?.lang || null,
            p_subtitle_label: chosenSub?.label || null,
          })
        ).catch((err: any) => console.warn('Failed to immediately save track preferences:', err));
      }
    }

    setShowAudioSubModal(false);
  };

  // True Native Fullscreen API + Screen Orientation Landscape Lock
  // True Native Fullscreen API + Screen Orientation Landscape Lock (Desktop avoids browser alert popup)
  const toggleFullscreen = async (e?: React.MouseEvent | React.TouchEvent) => {
    e?.stopPropagation();
    scheduleControlsHide();
    const container = containerRef.current;
    const video = videoRef.current;
    if (!container) return;

    // Desktop browsers (width >= 1024) toggle fullscreen mode without triggering the browser's "Press Esc" alert popup
    const isDesktop = typeof window !== 'undefined' && window.innerWidth >= 1024;
    if (isDesktop) {
      setIsFullscreen(prev => !prev);
      return;
    }

    const isFull = Boolean(
      document.fullscreenElement ||
      (document as any).webkitFullscreenElement
    );

    try {
      if (!isFull) {
        if (container.requestFullscreen) {
          await container.requestFullscreen({ navigationUI: 'hide' });
        } else if ((container as any).webkitRequestFullscreen) {
          (container as any).webkitRequestFullscreen();
        } else if (video && (video as any).webkitEnterFullscreen) {
          // iOS Safari native fallback
          (video as any).webkitEnterFullscreen();
          return;
        }
        // Lock mobile hardware orientation to true landscape once fullscreen is active
        if (window.screen?.orientation && (window.screen.orientation as any).lock) {
          await (window.screen.orientation as any).lock('landscape').catch(() => {});
        }
      } else {
        if (window.screen?.orientation?.unlock) {
          try {
            window.screen.orientation.unlock();
          } catch {}
        }
        if (document.exitFullscreen) {
          await document.exitFullscreen();
        } else if ((document as any).webkitExitFullscreen) {
          (document as any).webkitExitFullscreen();
        }
      }
    } catch (err) {
      console.warn('Fullscreen toggle warning:', err);
    }
  };

  // Synchronize fullscreen state & automatic orientation adaptation on mobile
  useEffect(() => {
    const handleFullscreenChange = () => {
      const isFull = Boolean(
        document.fullscreenElement ||
        (document as any).webkitFullscreenElement
      );
      if (typeof window !== 'undefined' && window.innerWidth < 1024) {
        setIsFullscreen(isFull);
      }
      if (!isFull && window.screen?.orientation?.unlock) {
        try {
          window.screen.orientation.unlock();
        } catch {}
      }
    };

    const handleOrientationChange = () => {
      if (typeof window !== 'undefined' && window.innerWidth < 1024 && window.screen?.orientation) {
        const isLandscape = window.screen.orientation.type.includes('landscape');
        setIsFullscreen(isLandscape);
      }
    };

    document.addEventListener('fullscreenchange', handleFullscreenChange);
    document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
    if (window.screen?.orientation?.addEventListener) {
      window.screen.orientation.addEventListener('change', handleOrientationChange);
    }

    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
      document.removeEventListener('webkitfullscreenchange', handleFullscreenChange);
      if (window.screen?.orientation?.removeEventListener) {
        window.screen.orientation.removeEventListener('change', handleOrientationChange);
      }
      if (window.screen?.orientation?.unlock) {
        try {
          window.screen.orientation.unlock();
        } catch {}
      }
      clearControlsTimer();
    };
  }, [clearControlsTimer]);

  // Touch & Pointer Timeline Scrubbing
  const handleTimelineScrub = (clientX: number) => {
    scheduleControlsHide();
    const bar = progressBarRef.current;
    const video = videoRef.current;
    if (!bar || !video || !duration) return;
    hlsRef.current?.startLoad();
    const rect = bar.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const targetTime = ratio * duration;
    setIsBuffering(true);
    setCurrentTime(targetTime);
    video.currentTime = targetTime;
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.stopPropagation();
    scheduleControlsHide();
    isScrubbingRef.current = true;
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {}
    handleTimelineScrub(e.clientX);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isScrubbingRef.current) return;
    e.stopPropagation();
    handleTimelineScrub(e.clientX);
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (isScrubbingRef.current) {
      e.stopPropagation();
      scheduleControlsHide();
      isScrubbingRef.current = false;
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {}
    }
  };

  // One-Click Toggle on Empty Screen (Both Desktop & Mobile, Playing & Paused)
  const handleScreenClick = (e: React.MouseEvent<HTMLDivElement>) => {
    // Ignore if any modal/dropdown is open
    if (showAudioSubModal || showQualityMenu || showSpeedMenu) {
      setShowQualityMenu(false);
      setShowSpeedMenu(false);
      return;
    }
    setShowControls(prev => {
      const next = !prev;
      if (next) {
        scheduleControlsHide();
      } else {
        clearControlsTimer();
      }
      return next;
    });
  };

  const formatTime = (secs: number) => {
    const s = Math.floor(secs);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    const rM = m % 60;
    const rS = s % 60;
    if (h > 0) {
      return `${h}:${rM < 10 ? '0' : ''}${rM}:${rS < 10 ? '0' : ''}${rS}`;
    }
    return `${rM}:${rS < 10 ? '0' : ''}${rS}`;
  };

  // Fullscreen trailer viewer with native player controls or fallback to backdrop poster
  if (isTrailer) {
    if (trailer && !trailerError) {
      return (
        <div className="fixed -inset-1 z-[60] bg-black flex flex-col select-none overflow-hidden">
          {/* Top Header Bar */}
          <div className="absolute top-0 left-0 right-0 z-40 p-4 sm:p-6 bg-gradient-to-b from-black/95 via-black/60 to-transparent flex items-center justify-between pointer-events-auto">
            <div className="flex items-center gap-4">
              <button
                onClick={closePlayer}
                className="p-2 sm:px-4 sm:py-2 rounded-full bg-black/75 hover:bg-black text-white backdrop-blur-md border border-white/20 transition cursor-pointer flex items-center gap-2 group shadow-xl"
                title="Back"
              >
                <ArrowLeft className="w-5 h-5 group-hover:-translate-x-0.5 transition-transform" />
                <span className="text-xs font-bold pr-1">Back</span>
              </button>
              <div>
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 bg-red-600 text-white text-[10px] font-black uppercase rounded tracking-wider shadow">
                    TRAILER
                  </span>
                  <h2 className="text-white font-bold text-base sm:text-lg drop-shadow-md line-clamp-1">
                    {media?.title}
                  </h2>
                </div>
                <p className="text-neutral-400 text-xs mt-0.5">Official Trailer</p>
              </div>
            </div>
          </div>

          {/* Embedded Trailer with Full Native Controls */}
          <div className="w-full h-full flex items-center justify-center bg-black">
            {trailer.type === 'youtube' ? (
              <iframe
                className="w-full h-full border-0"
                src={`https://www.youtube-nocookie.com/embed/${trailer.src}?autoplay=1&controls=1&modestbranding=1&rel=0&playsinline=1&enablejsapi=1&origin=${typeof window !== 'undefined' ? window.location.origin : ''}`}
                title={`${media?.title} Official Trailer`}
                referrerPolicy="origin"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
                onError={() => setTrailerError(true)}
              />
            ) : (
              <video
                src={trailer.src}
                controls
                autoPlay
                className="w-full h-full object-contain"
                onError={() => setTrailerError(true)}
              />
            )}
          </div>
        </div>
      );
    }

    // Fallback: Trailer is unavailable or can't play -> show backdrop poster for this title!
    return (
      <div className="fixed -inset-1 z-[60] bg-black flex flex-col items-center justify-center select-none overflow-hidden animate-fade-in">
        {/* Underlying Backdrop Poster */}
        <div className="absolute inset-0 w-full h-full">
          {media ? (
            <MediaImage
              title={media.title}
              posterUrl={media.poster_url}
              backdropUrl={media.backdrop_url}
              aspect="backdrop"
              fallbackYear={media.release_year}
              loading="eager"
            />
          ) : (
            <div className="w-full h-full bg-[#141414]" />
          )}
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
          <div className="absolute inset-0 bg-gradient-to-t from-black via-black/50 to-black/80" />
        </div>

        {/* Top Header Bar */}
        <div className="absolute top-0 left-0 right-0 z-40 p-4 sm:p-6 bg-gradient-to-b from-black/95 via-black/60 to-transparent flex items-center justify-between pointer-events-auto">
          <button
            onClick={closePlayer}
            className="p-2 sm:px-4 sm:py-2 rounded-full bg-black/75 hover:bg-black text-white backdrop-blur-md border border-white/20 transition cursor-pointer flex items-center gap-2 group shadow-xl"
            title="Back"
          >
            <ArrowLeft className="w-5 h-5 group-hover:-translate-x-0.5 transition-transform" />
            <span className="text-xs font-bold pr-1">Back</span>
          </button>
        </div>

        {/* Backdrop Context Card */}
        <div className="relative z-30 max-w-md w-full mx-4 p-8 bg-neutral-900/90 border border-neutral-700/80 rounded-2xl shadow-2xl backdrop-blur-xl text-center space-y-4">
          <div className="w-14 h-14 mx-auto rounded-full bg-red-600/20 border border-red-500/40 flex items-center justify-center text-red-500">
            <Film className="w-7 h-7" />
          </div>
          <div>
            <h3 className="text-white font-extrabold text-xl mb-1.5">{media?.title || 'Title'}</h3>
            <p className="text-red-400 font-semibold text-xs tracking-wider uppercase mb-2">Trailer Unavailable</p>
            <p className="text-neutral-300 text-xs sm:text-sm leading-relaxed">
              The official trailer for this title is currently unavailable or cannot be played.
            </p>
          </div>
          <div className="flex flex-col sm:flex-row gap-3 pt-2 justify-center">
            {media && (
              <button
                onClick={() => {
                  openPlayer(media, null, 0);
                }}
                className="px-5 py-2.5 bg-white hover:bg-neutral-200 text-black font-extrabold text-xs sm:text-sm rounded-xl transition flex items-center justify-center gap-2 shadow-lg cursor-pointer"
              >
                <Play className="w-4 h-4 fill-current ml-0.5" />
                <span>Play Title</span>
              </button>
            )}
            <button
              onClick={closePlayer}
              className="px-5 py-2.5 bg-neutral-800 hover:bg-neutral-700 text-white font-bold text-xs sm:text-sm rounded-xl transition cursor-pointer border border-neutral-700"
            >
              Back to Title
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onClick={handleScreenClick}
      className="fixed inset-0 bg-black z-[60] flex items-center justify-center select-none overflow-hidden"
    >
      {/* Video Element */}
      <video
        ref={videoRef}
        crossOrigin="anonymous"
        playsInline
        className="w-full h-full object-contain cursor-pointer bg-black"
        onLoadedMetadata={() => {
          if (!hasRestoredProgressRef.current && playbackConfig) {
            hasRestoredProgressRef.current = true;
            if (playbackConfig.startAtSeconds > 5 && Math.abs((videoRef.current?.currentTime || 0) - playbackConfig.startAtSeconds) > 4) {
              if (videoRef.current) {
                videoRef.current.currentTime = playbackConfig.startAtSeconds;
              }
            }
          }
        }}
        onProgress={updateBufferedProgress}
        onTimeUpdate={handleTimeUpdate}
        onWaiting={() => setIsBuffering(true)}
        onSeeking={() => setIsBuffering(true)}
        onSeeked={() => {
          if (videoRef.current && videoRef.current.readyState >= 3) {
            setIsBuffering(false);
          }
        }}
        onCanPlay={() => {
          if (videoRef.current && videoRef.current.readyState >= 3) {
            setIsBuffering(false);
          }
        }}
        onPlay={() => {
          hlsRef.current?.startLoad();
          setIsPlaying(true);
          setStoreIsPlaying(true);
        }}
        onPlaying={() => {
          hlsRef.current?.startLoad();
          setIsBuffering(false);
          setIsPlaying(true);
          setStoreIsPlaying(true);
        }}
        onPause={() => {
          setIsBuffering(false);
          setIsPlaying(false);
          setStoreIsPlaying(false);
          const video = videoRef.current;
          if (video && video.buffered && video.buffered.length > 0) {
            let activeEnd = 0;
            for (let i = 0; i < video.buffered.length; i++) {
              if (video.currentTime >= video.buffered.start(i) - 1 && video.currentTime <= video.buffered.end(i) + 1) {
                activeEnd = video.buffered.end(i);
                break;
              }
            }
            if (activeEnd === 0) {
              activeEnd = video.buffered.end(video.buffered.length - 1);
            }
            if (activeEnd - video.currentTime >= 15) {
              hlsRef.current?.stopLoad();
            }
          }
          syncProgress();
        }}
      >
        {subtitleTracksList.map((sub, idx) => (
          <track
            key={sub.url || idx}
            kind="subtitles"
            src={sub.url}
            srcLang={sub.lang}
            label={sub.label}
            default={false}
          />
        ))}
      </video>

      {/* Centered Netflix-style Loading & Buffering Spinner */}
      {isBuffering && (
        <div className="absolute inset-0 z-35 flex flex-col items-center justify-center pointer-events-none">
          <div className="w-14 h-14 border-4 border-red-600 border-t-transparent rounded-full animate-spin drop-shadow-[0_4px_12px_rgba(0,0,0,0.9)]" />
          <p className="text-white text-xs font-semibold mt-3 tracking-wider drop-shadow-[0_2px_4px_rgba(0,0,0,0.95)]">
            Loading...
          </p>
        </div>
      )}

      {/* High-Contrast Deterministic Subtitle Overlay */}
      {activeSubtitleIndex >= 0 && activeCueText && (
        <div
          className={`pointer-events-none absolute inset-x-0 bottom-4 sm:bottom-6 z-30 flex justify-center px-4 sm:px-8 text-center transition-transform duration-300 ease-out will-change-transform ${
            showControls ? '-translate-y-20 sm:-translate-y-24' : 'translate-y-0'
          }`}
        >
          <span
            className="inline-block max-w-[92%] sm:max-w-[80%] rounded-md bg-black/75 px-3.5 py-1.5 text-sm sm:text-lg md:text-xl font-semibold text-white shadow-lg select-none leading-snug"
            style={{ textShadow: '0 2px 4px rgba(0,0,0,0.95)' }}
          >
            {activeCueText.split('\n').map((line, lIdx, arr) => (
              <React.Fragment key={lIdx}>
                {line}
                {lIdx < arr.length - 1 && <br />}
              </React.Fragment>
            ))}
          </span>
        </div>
      )}

      {/* Auto-advance Countdown Overlay (Last 15s of Episode) */}
      {countdownSeconds !== null && nextEpisode && (
        <div
          className={`absolute right-4 sm:right-8 z-40 bg-neutral-900/95 border border-neutral-700 p-5 rounded-2xl shadow-2xl backdrop-blur-md max-w-sm animate-fade-in pointer-events-auto transition-all duration-300 ease-out ${
            showControls ? 'bottom-28' : 'bottom-8'
          }`}
        >
          <p className="text-neutral-400 text-xs font-semibold uppercase tracking-wider mb-1">Up Next in {countdownSeconds}s</p>
          <h4 className="text-white font-bold text-sm line-clamp-1 mb-3">
            S{nextEpisode.season_number}:E{nextEpisode.episode_number} "{nextEpisode.title}"
          </h4>
          <div className="flex gap-2">
            <button
              onClick={(e) => {
                e.stopPropagation();
                handleSwitchToEpisode(nextEpisode);
              }}
              className="flex-1 py-2 bg-red-600 hover:bg-red-700 text-white font-semibold rounded-lg text-xs transition duration-150 flex items-center justify-center gap-1.5 cursor-pointer"
            >
              <FastForward className="w-4 h-4" /> Play Now
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setCountdownSeconds(null);
              }}
              className="px-3 py-2 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 rounded-lg text-xs font-medium transition cursor-pointer"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Controls Overlay */}
      <div className="absolute inset-0 flex flex-col justify-between overflow-hidden pointer-events-none">
        {/* Background Vignette Gradient */}
        <div
          className={`absolute inset-0 bg-gradient-to-t from-black/90 via-black/20 to-black/80 transition-opacity duration-300 ease-out pointer-events-none ${
            showControls ? 'opacity-100' : 'opacity-0'
          }`}
        />

        {/* Top Bar — Smooth Swipe-Down on Activate / Swipe-Up on Deactivate */}
        <div
          className={`relative z-10 flex items-center justify-between p-4 sm:p-6 transition-all duration-300 ease-out transform-gpu will-change-transform ${
            showControls
              ? 'translate-y-0 opacity-100 pointer-events-auto'
              : '-translate-y-full opacity-0 pointer-events-none'
          }`}
        >
          <div className="flex items-center gap-3 sm:gap-4 min-w-0">
            <button
              onClick={(e) => {
                e.stopPropagation();
                handleExitPlayer();
              }}
              aria-label="Back"
              title="Exit Player"
              className="p-2 hover:bg-white/10 rounded-full transition text-white cursor-pointer shrink-0"
            >
              <ArrowLeft className="w-6 h-6" />
            </button>
            <div className="min-w-0">
              <h2 className="text-white font-bold text-base md:text-lg tracking-wide truncate">
                {media?.title}
              </h2>
              {episode && (
                <p className="text-neutral-400 text-xs md:text-sm truncate">
                  S{episode.season_number}:E{episode.episode_number} "{episode.title}"
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Center Controls (-10s, Play/Pause, +10s) — Instant Blur & Smooth Scale/Fade */}
        <div className="relative z-10 flex items-center justify-center gap-8 sm:gap-12 pointer-events-none">
          <button
            onClick={(e) => {
              e.stopPropagation();
              skipSeconds(-10);
              scheduleControlsHide();
            }}
            className={`p-3 text-white/85 hover:text-white hover:bg-white/10 rounded-full transition-all duration-200 ease-out transform-gpu ${
              showControls ? 'scale-100 opacity-100 pointer-events-auto' : 'scale-90 opacity-0 pointer-events-none'
            }`}
            title="Rewind 10 seconds"
          >
            <RotateCcw className="w-7 h-7 sm:w-8 sm:h-8" />
          </button>

          <button
            onClick={(e) => {
              e.stopPropagation();
              togglePlay();
              scheduleControlsHide();
            }}
            style={{ WebkitBackdropFilter: 'blur(12px)', backdropFilter: 'blur(12px)', transform: 'translateZ(0)' }}
            className={`p-4 sm:p-5 bg-white/20 hover:bg-white/30 border border-white/15 text-white rounded-full shadow-2xl transition-all duration-200 ease-out transform-gpu will-change-transform ${
              showControls ? 'scale-100 opacity-100 pointer-events-auto hover:scale-105' : 'scale-90 opacity-0 pointer-events-none'
            }`}
            title={isPlaying ? "Pause" : "Play"}
          >
            {isPlaying ? (
              <Pause className="w-8 h-8 sm:w-10 sm:h-10 fill-current" />
            ) : (
              <Play className="w-8 h-8 sm:w-10 sm:h-10 fill-current ml-0.5 sm:ml-1" />
            )}
          </button>

          <button
            onClick={(e) => {
              e.stopPropagation();
              skipSeconds(10);
              scheduleControlsHide();
            }}
            className={`p-3 text-white/85 hover:text-white hover:bg-white/10 rounded-full transition-all duration-200 ease-out transform-gpu ${
              showControls ? 'scale-100 opacity-100 pointer-events-auto' : 'scale-90 opacity-0 pointer-events-none'
            }`}
            title="Forward 10 seconds"
          >
            <RotateCw className="w-7 h-7 sm:w-8 sm:h-8" />
          </button>
        </div>

        {/* Bottom Controls Bar — Smooth Swipe-Up on Activate / Swipe-Down on Deactivate */}
        <div
          className={`relative z-10 p-4 sm:p-6 space-y-2 sm:space-y-3 transition-all duration-300 ease-out transform-gpu will-change-transform ${
            showControls
              ? 'translate-y-0 opacity-100 pointer-events-auto'
              : 'translate-y-full opacity-0 pointer-events-none'
          }`}
        >
          {/* Scrubbable Timeline */}
          <div
            ref={progressBarRef}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onClick={(e) => {
              e.stopPropagation();
              handleTimelineScrub(e.clientX);
            }}
            className="relative group w-full h-7 flex items-center cursor-pointer touch-none select-none"
          >
            <div className="w-full h-1.5 group-hover:h-2.5 bg-neutral-700/80 rounded-full relative overflow-hidden transition-all">
              {/* Buffered Progress */}
              <div
                className="absolute top-0 bottom-0 left-0 bg-white/45 transition-[width] duration-300 rounded-full"
                style={{ width: `${duration > 0 ? (bufferedEnd / duration) * 100 : 0}%` }}
              />
              {/* Watched Progress */}
              <div
                className="absolute top-0 bottom-0 left-0 bg-red-600 rounded-full"
                style={{ width: `${duration > 0 ? (currentTime / duration) * 100 : 0}%` }}
              />
            </div>
          </div>

          {/* Controls Strip */}
          <div className="flex items-center justify-between text-white text-sm px-1">
            <div className="flex items-center gap-1.5 sm:gap-3 min-w-0">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  togglePlay();
                }}
                className="hover:text-red-500 transition cursor-pointer p-1 shrink-0"
                title={isPlaying ? "Pause" : "Play"}
              >
                {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5" />}
              </button>

              {/* Volume Slider - Desktop Only */}
              <div className="hidden sm:flex items-center gap-2 group">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleMute();
                  }}
                  className="hover:text-red-500 transition cursor-pointer"
                  title={isMuted || volume === 0 ? "Unmute" : "Mute"}
                >
                  {isMuted || volume === 0 ? <VolumeX className="w-5 h-5" /> : <Volume2 className="w-5 h-5" />}
                </button>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={isMuted ? 0 : volume}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => {
                    e.stopPropagation();
                    handleVolumeChange(parseFloat(e.target.value));
                  }}
                  className="w-20 accent-red-600 h-1.5 cursor-pointer"
                />
              </div>

              {/* Mobile Mute Button */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  toggleMute();
                }}
                className="sm:hidden hover:text-red-500 transition cursor-pointer p-1 shrink-0"
                title={isMuted || volume === 0 ? "Unmute" : "Mute"}
              >
                {isMuted || volume === 0 ? <VolumeX className="w-5 h-5" /> : <Volume2 className="w-5 h-5" />}
              </button>

              <span className="text-[10px] sm:text-xs text-neutral-400 font-mono whitespace-nowrap">
                {formatTime(currentTime)} / {duration > 0 ? formatTime(duration) : '--:--'}
              </span>
            </div>

            <div className="flex items-center gap-1.5 sm:gap-3 relative shrink-0">
              {/* Next Episode Button */}
              {nextEpisode && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleSwitchToEpisode(nextEpisode);
                  }}
                  className="hover:text-red-500 transition flex items-center gap-1 text-xs font-semibold p-1 cursor-pointer"
                  title="Next Episode"
                >
                  <FastForward className="w-4 h-4" />
                  <span className="hidden sm:inline">Next Ep</span>
                </button>
              )}

              {/* Audio & Subtitles Modal Trigger */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  openAudioSubModal();
                }}
                className="hover:text-red-500 transition p-1.5 rounded-lg hover:bg-white/10 cursor-pointer"
                title="Audio & Subtitles"
              >
                <MessageSquare className="w-5 h-5" />
              </button>

              {/* Speed Selector */}
              <div className="relative" onClick={(e) => e.stopPropagation()}>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowSpeedMenu(!showSpeedMenu);
                    setShowQualityMenu(false);
                  }}
                  className="hover:text-red-500 transition text-xs font-bold px-1.5 sm:px-2 py-1 bg-neutral-800/80 rounded-md cursor-pointer"
                >
                  {playbackSpeed}x
                </button>
                {showSpeedMenu && (
                  <div className="absolute bottom-10 right-0 bg-neutral-900 border border-neutral-700 rounded-xl p-2 w-32 shadow-2xl backdrop-blur-md z-50">
                    <p className="text-[10px] text-neutral-400 font-bold uppercase tracking-wider mb-1 px-2">Speed</p>
                    {[0.5, 0.75, 1, 1.25, 1.5, 2].map((s) => (
                      <button
                        key={s}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleSpeedSelect(s);
                        }}
                        className={`w-full text-left px-2 py-1.5 rounded text-xs transition flex items-center justify-between cursor-pointer ${
                          playbackSpeed === s ? 'text-red-500 font-bold bg-neutral-800' : 'text-neutral-300 hover:bg-neutral-800'
                        }`}
                      >
                        <span>{s === 1 ? '1x (Normal)' : `${s}x`}</span>
                        {playbackSpeed === s && <Check className="w-3.5 h-3.5" />}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Quality Selector */}
              <div className="relative" onClick={(e) => e.stopPropagation()}>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowQualityMenu(!showQualityMenu);
                    setShowSpeedMenu(false);
                  }}
                  className="hover:text-red-500 transition text-xs font-bold px-1.5 sm:px-2 py-1 bg-neutral-800/80 rounded-md flex items-center gap-1 cursor-pointer"
                >
                  <Settings className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">{effectiveQuality}</span>
                </button>
                {showQualityMenu && (
                  <div className="absolute bottom-10 right-0 bg-neutral-900 border border-neutral-700 rounded-xl p-2 w-36 shadow-2xl backdrop-blur-md z-50">
                    <p className="text-[10px] text-neutral-400 font-bold uppercase tracking-wider mb-1 px-2">Quality</p>
                    {displayQualities.map((q) => (
                      <button
                        key={q}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleQualitySelect(q);
                        }}
                        className={`w-full text-left px-2 py-1.5 rounded text-xs transition flex items-center justify-between cursor-pointer ${
                          (!isMultiBitrate && q === 'Original') || currentQuality === q ? 'text-red-500 font-bold bg-neutral-800' : 'text-neutral-300 hover:bg-neutral-800'
                        }`}
                      >
                        <span>{q}</span>
                        {((!isMultiBitrate && q === 'Original') || currentQuality === q) && <Check className="w-3.5 h-3.5" />}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Fullscreen Button - Rendered on both Mobile & Desktop */}
              <button 
                onClick={toggleFullscreen} 
                className="hover:text-red-500 transition p-1 cursor-pointer shrink-0"
                title={isFullscreen ? "Exit Fullscreen" : "Fullscreen"}
                aria-label={isFullscreen ? "Exit Fullscreen" : "Fullscreen"}
              >
                {isFullscreen ? <Minimize className="w-5 h-5" /> : <Maximize className="w-5 h-5" />}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Audio & Subtitles Modal (Two-Column with Apply & Cancel buttons) */}
      {showAudioSubModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-md z-[70] flex items-center justify-center p-4">
          <div className="bg-[#141414] border border-neutral-800 rounded-2xl w-full max-w-xl p-6 shadow-2xl animate-fade-in">
            <div className="flex items-center justify-between pb-4 border-b border-neutral-800 mb-6">
              <h3 className="text-white font-bold text-lg">Audio & Subtitles</h3>
              <button
                onClick={cancelAudioSubChanges}
                className="text-neutral-400 hover:text-white transition cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-6 mb-8 max-h-80 overflow-y-auto pr-2">
              {/* Column 1: Audio */}
              <div>
                <h4 className="text-xs font-bold text-neutral-400 uppercase tracking-wider mb-3">Audio</h4>
                <div className="space-y-1">
                  {(audioTracksList.length > 0
                    ? audioTracksList
                    : (streamInfo?.audio_tracks && streamInfo.audio_tracks.length > 0
                        ? streamInfo.audio_tracks.map((a, i) => ({ index: i, id: i, label: a.label, lang: a.lang, is_default: a.is_default }))
                        : [{ index: 0, id: 0, label: 'Original Audio (Stereo)', lang: 'en', is_default: true }]
                      )
                  ).map((a) => (
                    <button
                      key={a.index}
                      type="button"
                      onClick={() => setPendingAudioIndex(a.index)}
                      className={`w-full text-left px-3 py-2 rounded-xl text-xs font-medium transition flex items-center justify-between cursor-pointer ${
                        pendingAudioIndex === a.index ? 'bg-red-600/20 text-red-500 border border-red-500/40' : 'text-neutral-300 hover:bg-neutral-800'
                      }`}
                    >
                      <span className="line-clamp-1 flex items-center">
                        {a.label}
                        {a.is_default && (
                          <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-neutral-300 font-semibold flex-shrink-0">
                            Default
                          </span>
                        )}
                      </span>
                      {pendingAudioIndex === a.index && <Check className="w-3.5 h-3.5 flex-shrink-0" />}
                    </button>
                  ))}
                </div>
              </div>

              {/* Column 2: Subtitles */}
              <div>
                <h4 className="text-xs font-bold text-neutral-400 uppercase tracking-wider mb-3">Subtitles</h4>
                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={() => setPendingSubtitleIndex(-1)}
                    className={`w-full text-left px-3 py-2 rounded-xl text-xs font-medium transition flex items-center justify-between cursor-pointer ${
                      pendingSubtitleIndex === -1 ? 'bg-red-600/20 text-red-500 border border-red-500/40' : 'text-neutral-300 hover:bg-neutral-800'
                    }`}
                  >
                    <span>Off</span>
                    {pendingSubtitleIndex === -1 && <Check className="w-3.5 h-3.5 flex-shrink-0" />}
                  </button>
                  {subtitleTracksList.map((sub) => (
                    <button
                      key={sub.index}
                      type="button"
                      onClick={() => setPendingSubtitleIndex(sub.index)}
                      className={`w-full text-left px-3 py-2 rounded-xl text-xs font-medium transition flex items-center justify-between cursor-pointer ${
                        pendingSubtitleIndex === sub.index ? 'bg-red-600/20 text-red-500 border border-red-500/40' : 'text-neutral-300 hover:bg-neutral-800'
                      }`}
                    >
                      <span className="line-clamp-1">{sub.label}</span>
                      {pendingSubtitleIndex === sub.index && <Check className="w-3.5 h-3.5 flex-shrink-0" />}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Modal Bottom Actions: Apply and Cancel */}
            <div className="flex items-center justify-end gap-3 pt-4 border-t border-neutral-800">
              <button
                type="button"
                onClick={cancelAudioSubChanges}
                className="px-5 py-2.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 rounded-xl text-xs font-semibold transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={applyAudioAndSubtitles}
                className="px-6 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold shadow-lg shadow-red-600/30 transition cursor-pointer"
              >
                Apply
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
