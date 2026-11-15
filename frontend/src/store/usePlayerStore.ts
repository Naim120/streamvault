import { create } from 'zustand';
import { Episode, MediaItem } from '../types';
import { useUIStore } from './useUIStore';

interface PlayerState {
  isOpen: boolean;
  isPlaying: boolean;
  media: MediaItem | null;
  episode: Episode | null;
  startSeconds: number;
  isTrailer: boolean;
  trailerUrl: string | null;
  savedScrollY: number;
  openedFromDetail: boolean;
  openPlayer: (media: MediaItem, episode?: Episode | null, startSeconds?: number) => void;
  openTrailer: (media: MediaItem, trailerUrl?: string | null) => void;
  closePlayer: () => void;
  playEpisode: (episode: Episode, startSeconds?: number) => void;
  setIsPlaying: (playing: boolean) => void;
}

export const usePlayerStore = create<PlayerState>((set, get) => ({
  isOpen: false,
  isPlaying: false,
  media: null,
  episode: null,
  startSeconds: 0,
  isTrailer: false,
  trailerUrl: null,
  savedScrollY: 0,
  openedFromDetail: false,

  setIsPlaying: (playing: boolean) => set({ isPlaying: playing }),

  openPlayer: (media, episode = null, startSeconds = 0) => {
    const prevOpen = get().isOpen;
    const hasDetailOpen = Boolean(useUIStore.getState().detailMedia);
    const scroll = typeof window !== 'undefined' ? (window.scrollY || window.pageYOffset || 0) : 0;

    set({
      isOpen: true,
      isPlaying: false,
      media,
      episode,
      startSeconds,
      isTrailer: false,
      trailerUrl: null,
      savedScrollY: scroll,
      openedFromDetail: hasDetailOpen
    });

    if (!prevOpen && typeof window !== 'undefined' && window.history.state?.svModal !== 'player') {
      window.history.pushState(
        { svModal: 'player', openedFromDetail: hasDetailOpen, mediaId: media.id },
        '',
        window.location.pathname
      );
    }
  },

  openTrailer: (media, trailerUrl = null) => {
    const prevOpen = get().isOpen;
    const hasDetailOpen = Boolean(useUIStore.getState().detailMedia);
    const scroll = typeof window !== 'undefined' ? (window.scrollY || window.pageYOffset || 0) : 0;

    set({
      isOpen: true,
      isPlaying: true,
      media,
      episode: null,
      startSeconds: 0,
      isTrailer: true,
      trailerUrl: trailerUrl || media.trailer_youtube_key || null,
      savedScrollY: scroll,
      openedFromDetail: hasDetailOpen
    });

    if (!prevOpen && typeof window !== 'undefined' && window.history.state?.svModal !== 'player') {
      window.history.pushState(
        { svModal: 'player', openedFromDetail: hasDetailOpen, mediaId: media.id },
        '',
        window.location.pathname
      );
    }
  },

  closePlayer: () => {
    const { openedFromDetail, media } = get();
    set({
      isOpen: false,
      isPlaying: false,
      media: null,
      episode: null,
      startSeconds: 0,
      isTrailer: false,
      trailerUrl: null,
      openedFromDetail: false
    });

    if (typeof window !== 'undefined' && window.history.state?.svModal === 'player') {
      const targetPath = openedFromDetail && media ? `/home/${media.id}` : '/home';
      window.history.replaceState(
        openedFromDetail ? { svModal: 'detail', mediaId: media?.id } : { svModal: 'home' },
        '',
        targetPath
      );
    }
  },

  playEpisode: (episode: Episode, startSeconds = 0) => {
    const { media, openedFromDetail } = get();
    if (!media) return;
    set({
      isOpen: true,
      isPlaying: false,
      episode,
      startSeconds,
      isTrailer: false,
      trailerUrl: null,
      openedFromDetail
    });
  }
}));
