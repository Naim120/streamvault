import { create } from 'zustand';
import { MediaItem } from '../types';
import { updatePageSeo } from '../lib/seo';

function updateBrowserUrl(params: {
  tab?: 'home' | 'search' | 'profile' | null;
  genre?: string | null;
  titleId?: string | null;
}, push = true) {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);

  // If opening detail view, push clean /home/:mediaId
  if (params.titleId) {
    const newPath = `/home/${params.titleId}`;
    if (window.location.pathname !== newPath) {
      if (push) {
        window.history.pushState({ title: params.titleId }, '', newPath);
      } else {
        window.history.replaceState({ title: params.titleId }, '', newPath);
      }
    }
    return;
  }

  // If closing detail view, restore to /home
  if (params.titleId === null) {
    url.searchParams.delete('title');
    url.searchParams.delete('media');
    const query = url.searchParams.toString();
    const newPath = '/home' + (query ? `?${query}` : '');
    if (window.location.pathname !== '/home' || window.location.search !== (query ? `?${query}` : '')) {
      if (push) {
        window.history.pushState({}, '', newPath);
      } else {
        window.history.replaceState({}, '', newPath);
      }
    }
    document.title = 'Home — StreamVault';
    return;
  }

  // Tab sync
  const basePath = window.location.pathname.startsWith('/home') ? '/home' : window.location.pathname;
  if (params.tab !== undefined) {
    if (!params.tab || params.tab === 'home') {
      url.searchParams.delete('tab');
    } else {
      url.searchParams.set('tab', params.tab);
    }
  }

  // Genre sync
  if (params.genre !== undefined) {
    if (!params.genre) {
      url.searchParams.delete('genre');
    } else {
      url.searchParams.set('genre', params.genre);
    }
  }

  const query = url.searchParams.toString();
  const newPath = (basePath || '/home') + (query ? `?${query}` : '');
  const currentPath = window.location.pathname + window.location.search;

  if (currentPath !== newPath) {
    if (push) {
      window.history.pushState(
        {
          tab: url.searchParams.get('tab') || 'home',
          genre: url.searchParams.get('genre'),
        },
        '',
        newPath
      );
    } else {
      window.history.replaceState(
        {
          tab: url.searchParams.get('tab') || 'home',
          genre: url.searchParams.get('genre'),
        },
        '',
        newPath
      );
    }
  }
}

interface UIState {
  activeTab: 'home' | 'search' | 'profile';
  selectedGenre: string | null;
  detailMedia: MediaItem | null;
  toastMessage: string | null;
  progressRefreshTrigger: number;
  triggerProgressRefresh: () => void;
  setActiveTab: (tab: 'home' | 'search' | 'profile', syncUrl?: boolean) => void;
  setSelectedGenre: (genre: string | null, syncUrl?: boolean) => void;
  openDetail: (media: MediaItem, syncUrl?: boolean) => void;
  closeDetail: (syncUrl?: boolean) => void;
  closeDetailFromHistory: () => void;
  showToast: (message: string) => void;
  clearToast: () => void;
  resetUI: () => void;
}

export const useUIStore = create<UIState>((set, get) => ({
  activeTab: 'home',
  selectedGenre: null,
  detailMedia: null,
  toastMessage: null,
  progressRefreshTrigger: 0,

  triggerProgressRefresh: () => set((state) => ({ progressRefreshTrigger: state.progressRefreshTrigger + 1 })),

  setActiveTab: (tab, syncUrl = true) => {
    set({ activeTab: tab });
    if (syncUrl) {
      updateBrowserUrl({ tab });
    }
  },

  setSelectedGenre: (genre, syncUrl = true) => {
    set({ selectedGenre: genre });
    if (syncUrl) {
      updateBrowserUrl({ genre });
    }
  },

  openDetail: (media, syncUrl = true) => {
    const prevDetail = get().detailMedia;
    set({ detailMedia: media });

    if (typeof window !== 'undefined') {
      if (!prevDetail && window.history.state?.svModal !== 'detail' && window.history.state?.svModal !== 'player') {
        window.history.pushState({ svModal: 'detail', mediaId: media.id }, '', '/home/' + media.id);
      } else if (syncUrl && (!prevDetail || prevDetail.id !== media.id)) {
        updateBrowserUrl({ titleId: media.id });
      }
    }

    updatePageSeo({
      title: `Watch ${media.title}${media.release_year ? ` (${media.release_year})` : ''} — StreamVault`,
      description: media.synopsis || undefined,
      canonicalPath: `/home/${media.id}`,
      noIndex: false,
    });
  },

  closeDetail: (syncUrl = true) => {
    set({ detailMedia: null });
    if (typeof window !== 'undefined' && window.location.pathname.startsWith('/home/')) {
      window.history.replaceState({ svModal: 'home' }, '', '/home');
    } else if (syncUrl) {
      updateBrowserUrl({ titleId: null });
    }
    updatePageSeo({
      title: 'Home — StreamVault',
      canonicalPath: '/home',
      noIndex: true,
    });
  },

  closeDetailFromHistory: () => {
    set({ detailMedia: null });
    updatePageSeo({
      title: 'Home — StreamVault',
      canonicalPath: '/home',
      noIndex: true,
    });
  },

  showToast: (message) => {
    set({ toastMessage: message });
    setTimeout(() => {
      set({ toastMessage: null });
    }, 3000);
  },

  clearToast: () => set({ toastMessage: null }),
  resetUI: () => set({
    activeTab: 'home',
    selectedGenre: null,
    detailMedia: null,
    toastMessage: null,
    progressRefreshTrigger: 0
  })
}));

