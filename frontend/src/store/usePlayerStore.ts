import { create } from 'zustand';
import { Episode, MediaItem } from '../types';

interface PlayerState {
  isOpen: boolean;
  isPlaying: boolean;
  media: MediaItem | null;
  episode: Episode | null;
  startSeconds: number;
  isTrailer: boolean;
  trailerUrl: string | null;
  savedScrollY: number;
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

  setIsPlaying: (playing: boolean) => set({ isPlaying: playing }),

  openPlayer: (media, episode = null, startSeconds = 0) => {
    const scroll = typeof window !== 'undefined' ? (window.scrollY || window.pageYOffset || 0) : 0;
    set({
      isOpen: true,
      isPlaying: false,
      media,
      episode,
      startSeconds,
      isTrailer: false,
      trailerUrl: null,
      savedScrollY: scroll
    });
  },

  openTrailer: (media, trailerUrl = null) => {
    const scroll = typeof window !== 'undefined' ? (window.scrollY || window.pageYOffset || 0) : 0;
    set({
      isOpen: true,
      isPlaying: true,
      media,
      episode: null,
      startSeconds: 0,
      isTrailer: true,
      trailerUrl: trailerUrl || media.trailer_youtube_key || null,
      savedScrollY: scroll
    });
  },

  closePlayer: () => {
    set({
      isOpen: false,
      isPlaying: false,
      media: null,
      episode: null,
      startSeconds: 0,
      isTrailer: false,
      trailerUrl: null
    });
  },

  playEpisode: (episode: Episode, startSeconds = 0) => {
    const { media } = get();
    if (!media) return;
    set({
      isOpen: true,
      isPlaying: false,
      episode,
      startSeconds,
      isTrailer: false,
      trailerUrl: null
    });
  }
}));
