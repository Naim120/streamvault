import React, { useEffect, useRef, useState, useCallback, Suspense } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuthStore, cleanAuthUrlParams } from './store/useAuthStore';
import { useUIStore } from './store/useUIStore';
import { usePlayerStore } from './store/usePlayerStore';
import { useIdleSessionManager } from './hooks/useIdleSessionManager';
import { useTVNavigation } from './hooks/useTVNavigation';
import { Navbar } from './components/layout/Navbar';
import { supabase } from './lib/supabase';
import { MediaItem } from './types';
import { onCatalogVersionChange } from './lib/telemetry';
import { NotFoundScreen } from './components/common/NotFoundScreen';
import { CookieConsentBanner } from './components/legal/CookieConsentBanner';
import { updatePageSeo } from './lib/seo';

function lazyWithRetry<T extends React.ComponentType<any>>(
  factory: () => Promise<{ default: T }>
) {
  return React.lazy(async () => {
    try {
      return await factory();
    } catch (error) {
      if (typeof window !== 'undefined') {
        const key = 'sv_chunk_reload_attempt';
        const lastAttempt = sessionStorage.getItem(key);
        if (!lastAttempt || Date.now() - parseInt(lastAttempt, 10) > 10000) {
          sessionStorage.setItem(key, Date.now().toString());
          window.location.reload();
        }
      }
      throw error;
    }
  });
}

// Route & Component Code-Splitting: Heavy modules are deferred so initial paint loads instantly
const HomeScreen = lazyWithRetry(() => import('./components/home/HomeScreen').then(m => ({ default: m.HomeScreen })));
const SearchScreen = lazyWithRetry(() => import('./components/search/SearchScreen').then(m => ({ default: m.SearchScreen })));
const ProfileScreen = lazyWithRetry(() => import('./components/profile/ProfileScreen').then(m => ({ default: m.ProfileScreen })));
const DetailModal = lazyWithRetry(() => import('./components/detail/DetailModal').then(m => ({ default: m.DetailModal })));
const VideoPlayer = lazyWithRetry(() => import('./components/player/VideoPlayer').then(m => ({ default: m.VideoPlayer })));
const ResetPasswordScreen = lazyWithRetry(() => import('./components/auth/ResetPasswordScreen').then(m => ({ default: m.ResetPasswordScreen })));
const VerifyEmailScreen = lazyWithRetry(() => import('./components/auth/VerifyEmailScreen').then(m => ({ default: m.VerifyEmailScreen })));
const LandingPage = lazyWithRetry(() => import('./components/landing/LandingPage').then(m => ({ default: m.LandingPage })));
const LegalPage = lazyWithRetry(() => import('./components/legal/LegalPage').then(m => ({ default: m.LegalPage })));

const ViewFallback: React.FC = () => (
  <div className="min-h-screen bg-black flex flex-col items-center justify-center space-y-4">
    <div className="w-8 h-8 border-2 border-red-600/30 border-t-red-600 rounded-full animate-spin" />
  </div>
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 3, // 3 minutes cache
      refetchOnWindowFocus: false
    }
  }
});

function MainApp() {
  const { user, loading, isEmailConfirmed, isPasswordRecovery, pendingVerificationEmail, initAuth } = useAuthStore();
  const { activeTab, detailMedia, toastMessage, openDetail, closeDetail } = useUIStore();
  const { isOpen: isPlayerOpen, media: playerMedia, episode: playerEpisode } = usePlayerStore();

  const [currentPath, setCurrentPath] = useState<string>(() =>
    typeof window !== 'undefined' ? window.location.pathname : '/'
  );
  const [isNotFound, setIsNotFound] = useState(false);
  const [authPromptNotice, setAuthPromptNotice] = useState<string | null>(null);

  // Initialize Auth
  useEffect(() => {
    initAuth();
  }, [initAuth]);

  // JioOS / Android TV 10-foot spatial navigation & hardware back routing
  useTVNavigation();

  // Patch history pushState and replaceState to dispatch a navigation event so React stays in sync
  useEffect(() => {
    if (typeof window === 'undefined') return;

    const origPush = window.history.pushState;
    window.history.pushState = function (...args) {
      const res = origPush.apply(this, args);
      window.dispatchEvent(new Event('sv:navigation'));
      return res;
    };

    const origReplace = window.history.replaceState;
    window.history.replaceState = function (...args) {
      const res = origReplace.apply(this, args);
      window.dispatchEvent(new Event('sv:navigation'));
      return res;
    };
  }, []);

  // Real-time item removal handlers
  const handleMediaRemoved = useCallback((removedMediaId?: string) => {
    if (!removedMediaId) return;

    const currentDetail = useUIStore.getState().detailMedia;
    if (currentDetail && currentDetail.id === removedMediaId) {
      useUIStore.getState().closeDetail(false);
      window.history.replaceState({}, '', '/home');
      setCurrentPath('/home');
      useUIStore.getState().showToast('Title Removed: This title is no longer available in the StreamVault library.');
    }

    const playerState = usePlayerStore.getState();
    if (playerState.isOpen && playerState.media?.id === removedMediaId) {
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      }
      playerState.closePlayer();
      useUIStore.getState().showToast('Playback Unavailable: This title was removed by the administrator.');
    }
  }, []);

  const handleEpisodeRemoved = useCallback((removedEpisodeId?: string) => {
    if (!removedEpisodeId) return;

    const playerState = usePlayerStore.getState();
    if (playerState.isOpen && playerState.episode?.id === removedEpisodeId) {
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      }
      playerState.closePlayer();
      useUIStore.getState().showToast('Playback Unavailable: This episode was removed by the administrator.');
    }
  }, []);

  // Invalidate all catalog queries & verify if currently displayed items are still published
  const invalidateAllCatalogQueries = useCallback(async () => {
    queryClient.invalidateQueries({ queryKey: ['home_feed'] });
    queryClient.invalidateQueries({ queryKey: ['watch_progress'] });
    queryClient.invalidateQueries({ queryKey: ['watch_history'] });
    queryClient.invalidateQueries({ queryKey: ['custom_lists'] });
    queryClient.invalidateQueries({ queryKey: ['all_published_media'] });

    const currentDetail = useUIStore.getState().detailMedia;
    if (currentDetail?.id) {
      const { data } = await supabase
        .from('media_items')
        .select('id, is_published')
        .eq('id', currentDetail.id)
        .maybeSingle();
      if (!data || !data.is_published) {
        handleMediaRemoved(currentDetail.id);
      }
    }

    const playerState = usePlayerStore.getState();
    if (playerState.isOpen && playerState.media?.id) {
      const { data } = await supabase
        .from('media_items')
        .select('id, is_published')
        .eq('id', playerState.media.id)
        .maybeSingle();
      if (!data || !data.is_published) {
        handleMediaRemoved(playerState.media.id);
      } else if (playerState.episode?.id) {
        const { data: epData } = await supabase
          .from('episodes')
          .select('id')
          .eq('id', playerState.episode.id)
          .maybeSingle();
        if (!epData) {
          handleEpisodeRemoved(playerState.episode.id);
        }
      }
    }
  }, [handleMediaRemoved, handleEpisodeRemoved]);

  // Telemetry heartbeat catalog version listener
  useEffect(() => {
    return onCatalogVersionChange(() => {
      invalidateAllCatalogQueries();
    });
  }, [invalidateAllCatalogQueries]);

  // Supabase Realtime channel for live DELETE / UPDATE on media_items and episodes
  useEffect(() => {
    if (!user) return;

    const channel = supabase
      .channel('catalog-realtime-sync')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'media_items' },
        (payload) => {
          invalidateAllCatalogQueries();
          if (payload.eventType === 'DELETE') {
            const oldId = (payload.old as any)?.id;
            if (oldId) handleMediaRemoved(oldId);
          } else if (payload.eventType === 'UPDATE') {
            const updated = payload.new as any;
            if (updated && !updated.is_published) {
              handleMediaRemoved(updated.id);
            }
          }
        }
      )
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'episodes' },
        (payload) => {
          invalidateAllCatalogQueries();
          if (payload.eventType === 'DELETE') {
            const oldId = (payload.old as any)?.id;
            if (oldId) handleEpisodeRemoved(oldId);
          }
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [user, invalidateAllCatalogQueries, handleMediaRemoved, handleEpisodeRemoved]);

  // Smart Activity & Video-Playback-Aware Auto-Logout
  useIdleSessionManager();

  // Dynamic Document Title based on active playback / recovery state
  useEffect(() => {
    if (isPasswordRecovery) {
      document.title = 'Reset Password | StreamVault';
      cleanAuthUrlParams();
    } else if (isPlayerOpen && playerMedia) {
      const epStr = playerEpisode
        ? ` - S${playerEpisode.season_number}E${playerEpisode.episode_number}: ${playerEpisode.title}`
        : '';
      document.title = `▶ ${playerMedia.title}${epStr} | StreamVault`;
    }
  }, [isPasswordRecovery, isPlayerOpen, playerMedia, playerEpisode]);

  // Preserve homepage / search scroll position across player open / close
  const prevIsPlayerOpenRef = useRef<boolean>(false);
  useEffect(() => {
    if (!isPlayerOpen && prevIsPlayerOpenRef.current) {
      const restoreY = usePlayerStore.getState().savedScrollY || 0;
      requestAnimationFrame(() => {
        window.scrollTo({ top: restoreY, behavior: 'instant' });
      });
    }
    prevIsPlayerOpenRef.current = isPlayerOpen;
  }, [isPlayerOpen]);

  // Handle post-login redirection when user becomes authenticated
  useEffect(() => {
    if (user && isEmailConfirmed && !isPasswordRecovery) {
      const pendingRedirect = sessionStorage.getItem('sv_post_login_redirect');
      if (pendingRedirect) {
        sessionStorage.removeItem('sv_post_login_redirect');
        window.history.replaceState({}, '', pendingRedirect);
        setCurrentPath(pendingRedirect.split('?')[0]);
      } else if (window.location.pathname === '/' || window.location.pathname === '') {
        window.history.replaceState({}, '', '/home');
        setCurrentPath('/home');
      }
    }
  }, [user, isEmailConfirmed, isPasswordRecovery]);

  // Master Path-Based Router Synchronizer
  const syncRouterState = useCallback(async () => {
    if (typeof window === 'undefined') return;

    // If player is currently open, let player's dedicated exit interceptor handle navigation
    if (usePlayerStore.getState().isOpen) return;

    const pathname = window.location.pathname;
    const search = window.location.search;
    const params = new URLSearchParams(search);

    // Backward Compatibility Check: ?title=<uuid> or ?media=<uuid>
    const legacyTitleId = params.get('title') || params.get('media');
    if (legacyTitleId) {
      params.delete('title');
      params.delete('media');
      const cleanSearch = params.toString();
      const newTarget = `/home/${legacyTitleId}${cleanSearch ? `?${cleanSearch}` : ''}`;
      window.history.replaceState({}, '', newTarget);
      setCurrentPath(`/home/${legacyTitleId}`);
      return;
    }

    setCurrentPath(pathname);

    // Route 1: Legal Pages (/legal/:slug)
    if (pathname.startsWith('/legal')) {
      setIsNotFound(false);
      return;
    }

    // Route 2: Commercial Landing Page (/)
    if (pathname === '/' || pathname === '') {
      setIsNotFound(false);
      if (user && isEmailConfirmed && !isPasswordRecovery) {
        window.history.replaceState({}, '', '/home');
        setCurrentPath('/home');
      }
      return;
    }

    // Route 3: Protected Catalog Home (/home) or Deep Link (/home/:mediaId)
    if (pathname.startsWith('/home')) {
      if (!user) {
        // Unauthenticated access to protected route: save redirect and prompt login on /
        sessionStorage.setItem('sv_post_login_redirect', pathname + (search || ''));
        setAuthPromptNotice(
          pathname === '/home'
            ? 'Please sign in to access the StreamVault catalog.'
            : 'Please sign in to watch this title.'
        );
        window.history.replaceState({}, '', '/');
        setCurrentPath('/');
        return;
      }

      // Authenticated User on /home or /home/:mediaId
      const pathSegments = pathname.split('/').filter(Boolean);
      const mediaId = pathSegments[1]; // /home/:mediaId

      if (mediaId) {
        // Deep link to title
        const currentDetail = useUIStore.getState().detailMedia;
        if (!currentDetail || currentDetail.id !== mediaId) {
          const { data } = await supabase
            .from('media_items')
            .select('*')
            .eq('id', mediaId)
            .eq('is_published', true)
            .maybeSingle();

          if (data) {
            setIsNotFound(false);
            openDetail(data as MediaItem, false);
            updatePageSeo({
              title: `Watch ${data.title}${data.release_year ? ` (${data.release_year})` : ''} — StreamVault`,
              description: data.synopsis || undefined,
              canonicalPath: `/home/${data.id}`,
              noIndex: false,
            });
          } else {
            setIsNotFound(true);
            closeDetail(false);
          }
        } else {
          setIsNotFound(false);
        }
      } else {
        // Direct /home route
        setIsNotFound(false);
        if (useUIStore.getState().detailMedia) {
          closeDetail(false);
        }
        updatePageSeo({
          title: 'Home — StreamVault',
          canonicalPath: '/home',
          noIndex: true,
        });

        // Tab & Genre Query Sync
        const tabParam = params.get('tab') as 'home' | 'search' | 'profile' | null;
        const genreParam = params.get('genre');
        if (tabParam && ['home', 'search', 'profile'].includes(tabParam)) {
          if (useUIStore.getState().activeTab !== tabParam) {
            useUIStore.getState().setActiveTab(tabParam, false);
          }
        }
        if (genreParam !== useUIStore.getState().selectedGenre) {
          useUIStore.getState().setSelectedGenre(genreParam || null, false);
        }
      }
      return;
    }

    // Route 4: Unknown Path (*) -> 404
    setIsNotFound(true);
  }, [user, isEmailConfirmed, isPasswordRecovery, openDetail, closeDetail]);

  // Global popstate & Hardware Back Interceptor
  useEffect(() => {
    const handlePopState = (_e: PopStateEvent) => {
      const playerState = usePlayerStore.getState();
      const uiState = useUIStore.getState();

      // Priority 1: If VideoPlayer is currently open, intercept Back button to exit VideoPlayer ONLY!
      if (playerState.isOpen) {
        // Dispatch custom event so VideoPlayer flushes watch_progress & exits fullscreen cleanly
        window.dispatchEvent(new CustomEvent('sv:hardware-back-from-player'));
        return;
      }

      // Priority 2: If DetailModal is currently open, intercept Back button to close DetailModal and stay on /home!
      if (uiState.detailMedia) {
        uiState.closeDetail();
        if (window.location.pathname !== '/home') {
          window.history.replaceState({ svModal: 'home' }, '', '/home');
        }
        return;
      }

      // Priority 3: If on Search or Profile tab, return to Home tab instead of leaving the app!
      if (uiState.activeTab !== 'home') {
        uiState.setActiveTab('home');
        window.history.replaceState({ svModal: 'home' }, '', '/home');
        return;
      }
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    syncRouterState();

    window.addEventListener('popstate', syncRouterState);
    window.addEventListener('sv:navigation', syncRouterState);

    return () => {
      window.removeEventListener('popstate', syncRouterState);
      window.removeEventListener('sv:navigation', syncRouterState);
    };
  }, [syncRouterState]);

  // Loading Splash Screen
  if (loading) {
    return (
      <div className="min-h-screen bg-black flex flex-col items-center justify-center space-y-4">
        <h1 className="text-red-600 font-black tracking-widest text-4xl sm:text-5xl font-mono animate-pulse">
          STREAMVAULT
        </h1>
        <div className="w-8 h-8 border-3 border-red-600/30 border-t-red-600 rounded-full animate-spin" />
      </div>
    );
  }

  // 1. Password Recovery Flow
  if (isPasswordRecovery) {
    return (
      <Suspense fallback={<ViewFallback />}>
        <ResetPasswordScreen />
      </Suspense>
    );
  }

  // 2. Email Confirmation Required
  if ((user && !isEmailConfirmed) || pendingVerificationEmail) {
    return (
      <Suspense fallback={<ViewFallback />}>
        <VerifyEmailScreen />
      </Suspense>
    );
  }

  // 3. Legal & Compliance Suite (/legal/:slug) - Accessible to both logged-in and public visitors
  if (currentPath.startsWith('/legal')) {
    const slug = currentPath.replace('/legal', '').replace('/', '') || 'terms';
    const validSlug = ['terms', 'privacy', 'dmca', 'cookies'].includes(slug)
      ? (slug as 'terms' | 'privacy' | 'dmca' | 'cookies')
      : 'terms';

    return (
      <Suspense fallback={<ViewFallback />}>
        <LegalPage
          currentSlug={validSlug}
          onSelectSlug={(newSlug) => {
            window.history.pushState({}, '', `/legal/${newSlug}`);
          }}
          onBack={() => {
            const target = user ? '/home' : '/';
            window.history.pushState({}, '', target);
          }}
          isAuthenticated={!!user}
        />
        <CookieConsentBanner />
      </Suspense>
    );
  }

  // 4. Dedicated 404 Screen for deleted/invalid titles or unknown paths
  if (isNotFound) {
    return (
      <Suspense fallback={<ViewFallback />}>
        <NotFoundScreen
          targetPath={user ? '/home' : '/'}
          onReset={() => {
            setIsNotFound(false);
          }}
        />
      </Suspense>
    );
  }

  // 5. Commercial Landing Page for Unauthenticated Visitors at /
  if (!user) {
    return (
      <Suspense fallback={<ViewFallback />}>
        <LandingPage
          initialAuthMode={authPromptNotice ? 'login' : null}
          initialAuthNotice={authPromptNotice}
          onNavigateLegal={(slug) => {
            window.history.pushState({}, '', `/legal/${slug}`);
          }}
        />
        <CookieConsentBanner
          onNavigateLegal={(slug) => {
            window.history.pushState({}, '', `/legal/${slug}`);
          }}
        />
      </Suspense>
    );
  }

  // 6. Authenticated Streaming Catalog Flow (/home & /home/:mediaId)
  return (
    <Suspense fallback={<ViewFallback />}>
      <div className="min-h-screen bg-black text-white relative flex flex-col">
        {/* Toast Notification */}
        {toastMessage && (
          <div className="fixed top-20 right-6 z-[80] bg-neutral-900/95 border border-neutral-700 text-white px-4 py-2.5 rounded-xl shadow-2xl text-xs font-semibold backdrop-blur-xl animate-fade-in flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                toastMessage.includes('✓')
                  ? 'bg-emerald-500 shadow-emerald-500/50 shadow-sm'
                  : 'bg-red-600'
              }`}
            />
            <span>{toastMessage}</span>
          </div>
        )}

        {/* Main Browsing Screens - Hidden when Detail View or Video Player is active */}
        <div className={detailMedia || isPlayerOpen ? 'hidden' : 'block flex-1'}>
          <Navbar />
          {activeTab === 'home' && <HomeScreen />}
          {activeTab === 'search' && <SearchScreen />}
          {activeTab === 'profile' && <ProfileScreen />}
        </div>

        {/* Dedicated Movie / Series Detail View - Hidden when Video Player is active */}
        {detailMedia && (
          <div className={isPlayerOpen ? 'hidden' : 'block'}>
            <DetailModal />
          </div>
        )}

        {/* Custom HLS Video Player - Only loaded when player is actually active */}
        {isPlayerOpen && <VideoPlayer />}

        {/* Global Cookie Consent Banner */}
        <CookieConsentBanner
          onNavigateLegal={(slug) => {
            window.history.pushState({}, '', `/legal/${slug}`);
          }}
        />
      </div>
    </Suspense>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <MainApp />
    </QueryClientProvider>
  );
}
