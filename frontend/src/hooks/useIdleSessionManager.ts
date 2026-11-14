import { useEffect, useRef, useCallback } from 'react';
import { supabase } from '../lib/supabase';
import { useAuthStore } from '../store/useAuthStore';
import { usePlayerStore } from '../store/usePlayerStore';
import {
  startTelemetrySession,
  sendTelemetryHeartbeat,
  getCachedSessionLogId
} from '../lib/telemetry';

const IDLE_LOGOUT_TIMEOUT_MS = 60 * 60 * 1000; // 1 hour of true inactivity
const ACTIVITY_THROTTLE_MS = 3000;             // Throttle DOM events to once per 3s
const TOKEN_KEEPALIVE_MS = 15 * 60 * 1000;     // Refresh JWT every 15m while active/playing
const HEARTBEAT_FLUSH_INTERVAL_SEC = 10;       // Flush heartbeat every 10s for real-time telemetry

export function useIdleSessionManager() {
  const { user, session, signOut } = useAuthStore();

  const lastActivityRef = useRef<number>(Date.now());
  const lastTokenRefreshRef = useRef<number>(Date.now());
  const lastFlushTimeRef = useRef<number>(Date.now());
  const sessionInitializedRef = useRef<string | null>(null);
  const isFlushingRef = useRef<boolean>(false);

  // Helper to format active media title
  const getActiveMediaTitle = useCallback((): string | null => {
    const player = usePlayerStore.getState();
    if (player.isOpen && player.isPlaying && player.media) {
      if (player.episode) {
        return `${player.media.title} S${player.episode.season_number}:E${player.episode.episode_number} ${player.episode.title || ''}`.trim();
      }
      return player.media.title;
    }
    return null;
  }, []);

  // 1. Flush Helper (Using exact wall-clock elapsed time)
  const flushTelemetry = useCallback(async (forcedTitle?: string | null) => {
    if (isFlushingRef.current) return;
    const token = session?.access_token;
    if (!token || !user) return;

    const now = Date.now();
    const rawElapsedSec = Math.round((now - lastFlushTimeRef.current) / 1000);
    // Ignore micro-ticks (< 1s), cap at 60s max to prevent laptop sleep jumps
    const elapsedSec = Math.max(0, Math.min(60, rawElapsedSec));
    lastFlushTimeRef.current = now;

    const player = usePlayerStore.getState();
    const isWatching = player.isOpen && player.isPlaying;
    const isVisible = typeof document !== 'undefined' && document.visibilityState === 'visible';
    const isUserActive = (now - lastActivityRef.current) < 120000; // Active within last 2 minutes

    let deltaSite = 0;
    let deltaWatch = 0;
    let mediaTitle = forcedTitle !== undefined ? forcedTitle : getActiveMediaTitle();

    if (isWatching) {
      // CRITICAL RULE: If playing video/audio, user is actively streaming even in background tab
      deltaWatch = elapsedSec;
      deltaSite = elapsedSec;
      if (!mediaTitle) mediaTitle = getActiveMediaTitle();
    } else if (isVisible && isUserActive) {
      // Online and browsing actively
      deltaSite = elapsedSec;
      deltaWatch = 0;
      mediaTitle = 'Online — Browsing';
    } else if (isVisible) {
      // Tab is visible but user hasn't clicked/scrolled recently
      deltaSite = Math.min(elapsedSec, 5);
      deltaWatch = 0;
      mediaTitle = 'Online — Browsing';
    } else {
      // Hidden background tab and not watching
      deltaSite = 0;
      deltaWatch = 0;
    }

    // Always send heartbeat if at least 1 second passed or state changed
    isFlushingRef.current = true;
    try {
      await sendTelemetryHeartbeat(token, {
        session_log_id: getCachedSessionLogId(),
        delta_site_seconds: deltaSite,
        delta_watch_seconds: deltaWatch,
        active_media_title: mediaTitle || (isWatching ? 'Watching Video' : 'Online — Browsing')
      });
    } finally {
      isFlushingRef.current = false;
    }
  }, [session, user, getActiveMediaTitle]);

  // 2. Start or Resume Telemetry Session on Authenticated Mount
  useEffect(() => {
    if (!user || !session?.access_token) return;

    if (sessionInitializedRef.current !== user.id) {
      sessionInitializedRef.current = user.id;
      lastFlushTimeRef.current = Date.now();

      const existingSessionId = getCachedSessionLogId();
      if (!existingSessionId) {
        // First time opening the tab: start official telemetry session
        startTelemetrySession(session.access_token, user.email || undefined).then(() => {
          // Send instant 1s heartbeat so user appears 🟢 Online within 1 second
          flushTelemetry('Online — Browsing');
        });
      } else {
        // Tab refreshed with existing session ID: immediately touch heartbeat
        sendTelemetryHeartbeat(session.access_token, {
          session_log_id: existingSessionId,
          delta_site_seconds: 1,
          delta_watch_seconds: 0,
          active_media_title: 'Online — Browsing'
        });
      }
    }
  }, [user, session, flushTelemetry]);

  // 3. Track DOM User Interactions (Throttled)
  useEffect(() => {
    if (!user) return;
    lastActivityRef.current = Date.now();

    const updateActivity = () => {
      const now = Date.now();
      if (now - lastActivityRef.current >= ACTIVITY_THROTTLE_MS) {
        lastActivityRef.current = now;
      }
    };

    const events: (keyof WindowEventMap)[] = [
      'mousemove',
      'mousedown',
      'keydown',
      'touchstart',
      'touchmove',
      'wheel',
      'scroll',
      'click',
    ];

    events.forEach((evt) => window.addEventListener(evt, updateActivity, { passive: true }));
    return () => {
      events.forEach((evt) => window.removeEventListener(evt, updateActivity));
    };
  }, [user]);

  // 4. Instant Flush on Video Play / Pause / Close
  useEffect(() => {
    if (!user) return;

    const unsub = usePlayerStore.subscribe((state, prevState) => {
      const wasPlaying = prevState.isOpen && prevState.isPlaying;
      const isPlaying = state.isOpen && state.isPlaying;

      if (wasPlaying !== isPlaying || (state.isOpen !== prevState.isOpen)) {
        // Playback state flipped: flush immediately with new media title
        lastActivityRef.current = Date.now();
        let nextTitle: string | null = null;
        if (isPlaying && state.media) {
          if (state.episode) {
            nextTitle = `${state.media.title} S${state.episode.season_number}:E${state.episode.episode_number} ${state.episode.title || ''}`.trim();
          } else {
            nextTitle = state.media.title;
          }
        }
        flushTelemetry(isPlaying ? nextTitle : 'Online — Browsing');
      }
    });

    return () => unsub();
  }, [user, flushTelemetry]);

  // 5. Regular Periodic Heartbeat Loop (10-second timer)
  useEffect(() => {
    if (!user) return;

    const intervalId = setInterval(async () => {
      const now = Date.now();
      const player = usePlayerStore.getState();
      const isWatching = player.isOpen && player.isPlaying;

      if (isWatching) {
        lastActivityRef.current = now;
      }

      const idleDuration = now - lastActivityRef.current;

      // True Idle auto-logout after 1 hour of zero input and no video playing
      if (!isWatching && idleDuration >= IDLE_LOGOUT_TIMEOUT_MS) {
        try {
          await flushTelemetry();
          await signOut();
        } catch (err) {
          console.warn('Idle sign-out warning:', err);
        }
        return;
      }

      // Flush periodic heartbeat
      await flushTelemetry();

      // Proactive JWT token refresh every 15 minutes
      if (now - lastTokenRefreshRef.current >= TOKEN_KEEPALIVE_MS) {
        lastTokenRefreshRef.current = now;
        try {
          const { data } = await supabase.auth.getSession();
          if (data?.session) {
            const expiresAtMs = (data.session.expires_at || 0) * 1000;
            if (expiresAtMs - now < 20 * 60 * 1000) {
              await supabase.auth.refreshSession();
            }
          }
        } catch (err) {
          console.warn('Background token keep-alive warning:', err);
        }
      }
    }, HEARTBEAT_FLUSH_INTERVAL_SEC * 1000);

    // 6. Visibility Change & Pagehide / Unload Handlers
    const handleVisibilityChange = () => {
      flushTelemetry();
    };

    const handleBeforeUnload = () => {
      // Flush pending watch deltas on tab close/refresh without invalidating active auth session
      flushTelemetry();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('pagehide', handleBeforeUnload);
    window.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('pagehide', handleBeforeUnload);
      window.removeEventListener('beforeunload', handleBeforeUnload);
    };
  }, [user, session, signOut, flushTelemetry]);
}
