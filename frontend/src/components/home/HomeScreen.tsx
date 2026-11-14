import React, { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { supabase } from '../../lib/supabase';
import { useAuthStore } from '../../store/useAuthStore';
import { useUIStore } from '../../store/useUIStore';
import { HomeFeedData, MediaItem } from '../../types';
import { Billboard } from './Billboard';
import { ContinueWatchingRow } from './ContinueWatchingRow';
import { MediaRow } from './MediaRow';
import { PosterCard } from './PosterCard';
import { Footer } from '../layout/Footer';
import { prevalidateTrailer } from '../../lib/trailerValidator';

export const HomeScreen: React.FC = () => {
  const { user } = useAuthStore();
  const { selectedGenre } = useUIStore();

  // 1. Fetch Home Feed using high-performance Postgres RPC
  const { data: feedData, isLoading, refetch } = useQuery<HomeFeedData>({
    queryKey: ['home_feed', user?.id],
    queryFn: async () => {
      const { data, error } = await supabase.rpc('get_home_feed', {
        current_user_id: user?.id || null
      });
      if (error) throw error;
      return data as HomeFeedData;
    },
    staleTime: 1000 * 60 * 3 // Cache for 3 minutes
  });

  // 2. Fetch all published media to build dynamic genre rows
  const { data: allMedia } = useQuery<MediaItem[]>({
    queryKey: ['all_published_media'],
    queryFn: async () => {
      const { data, error } = await supabase
        .from('media_items')
        .select('*')
        .eq('is_published', true)
        .order('views_count', { ascending: false });
      if (error) throw error;
      return (data || []) as MediaItem[];
    },
    staleTime: 1000 * 60 * 5
  });

  // Pinned billboard priority resolution:
  // Prioritize published items with is_featured_billboard === true (ordered by billboard_priority DESC, updated_at DESC)
  const pinnedBillboards = (allMedia || [])
    .filter(m => m.is_featured_billboard === true)
    .sort((a, b) => {
      const pDiff = (b.billboard_priority ?? 0) - (a.billboard_priority ?? 0);
      if (pDiff !== 0) return pDiff;
      const bDate = new Date(b.updated_at || b.created_at).getTime();
      const aDate = new Date(a.updated_at || a.created_at).getTime();
      return bDate - aDate;
    });

  const rawFeatured = pinnedBillboards.length > 0
    ? pinnedBillboards[0]
    : feedData?.most_watched?.[0] || feedData?.recently_added?.[0] || allMedia?.[0];

  const featuredItem = rawFeatured && allMedia
    ? (allMedia.find(m => m.id === rawFeatured.id) || rawFeatured)
    : rawFeatured;

  // Pre-validate trailers in the background for billboard item and initial visible rail items
  useEffect(() => {
    if (featuredItem?.trailer_youtube_key) {
      prevalidateTrailer(featuredItem.trailer_youtube_key);
    }
    const candidates = [
      ...(feedData?.recently_added || []).slice(0, 8),
      ...(feedData?.most_watched || []).slice(0, 8),
      ...(feedData?.most_liked || []).slice(0, 8),
      ...(feedData?.critics_picks || []).slice(0, 8)
    ];
    candidates.forEach((item, idx) => {
      if (item.trailer_youtube_key) {
        setTimeout(() => {
          prevalidateTrailer(item.trailer_youtube_key);
        }, idx * 100);
      }
    });
  }, [featuredItem?.id, featuredItem?.trailer_youtube_key, feedData]);

  if (isLoading) {
    return (
      <div className="min-h-screen bg-black pt-20 px-4 sm:px-8 space-y-8 animate-pulse">
        <div className="w-full h-[60vh] bg-neutral-900 rounded-2xl" />
        <div className="h-44 bg-neutral-900 rounded-xl" />
        <div className="h-44 bg-neutral-900 rounded-xl" />
      </div>
    );
  }

  // Intelligent tokenized genre matching
  const filteredAll = selectedGenre && allMedia
    ? allMedia.filter(m => {
        if (!m.genres || m.genres.length === 0) return false;
        const tokens = selectedGenre
          .toLowerCase()
          .split(/&|\/|,|\s+and\s+/)
          .map(t => t.trim())
          .filter(Boolean);
        return m.genres.some(itemGenre => {
          const itemLower = itemGenre.toLowerCase();
          return tokens.some(t => itemLower.includes(t) || t.includes(itemLower));
        });
      })
    : allMedia || [];

  // Helper for genre filtering
  const getGenreItems = (keyword: string) => {
    return (allMedia || []).filter(m =>
      m.genres && m.genres.some(g => g.toLowerCase().includes(keyword.toLowerCase()))
    );
  };

  return (
    <div className="min-h-screen bg-black pb-28">
      {/* If a genre filter is selected, show filtered view */}
      {selectedGenre ? (
        <div className="pt-24 px-4 sm:px-8 space-y-6 max-w-7xl mx-auto">
          <div className="flex items-center justify-between pb-2 border-b border-neutral-800">
            <div>
              <h1 className="text-2xl sm:text-3xl font-black text-white">
                {selectedGenre}
              </h1>
              <p className="text-xs text-neutral-400 mt-1">
                Showing {filteredAll.length} {filteredAll.length === 1 ? 'title' : 'titles'}
              </p>
            </div>
            <button
              onClick={() => useUIStore.getState().setSelectedGenre(null)}
              className="px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 hover:text-white rounded-lg text-xs font-semibold transition cursor-pointer"
            >
              Clear Filter
            </button>
          </div>

          {filteredAll.length === 0 ? (
            <div className="py-20 text-center space-y-3">
              <p className="text-neutral-400 text-sm">No titles found for &quot;{selectedGenre}&quot; yet.</p>
              <button
                onClick={() => useUIStore.getState().setSelectedGenre(null)}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-xs font-bold transition cursor-pointer"
              >
                Browse All Titles
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-6 sm:gap-8 pt-4 pb-12">
              {filteredAll.map(m => (
                <div key={m.id} className="w-full">
                  <PosterCard media={m} isGrid />
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <>
          {/* Billboard / Hero */}
          {featuredItem && (
            <Billboard
              item={featuredItem}
              resumeProgress={feedData?.continue_watching?.find(cw => cw.media_id === featuredItem.id) || null}
            />
          )}

          {/* Rows Container */}
          <div className="space-y-6 -mt-12 sm:-mt-20 relative z-20">
            {/* Continue Watching for User */}
            {feedData?.continue_watching && feedData.continue_watching.length > 0 && (
              <ContinueWatchingRow
                items={feedData.continue_watching}
                onRefresh={refetch}
              />
            )}

            {/* Top 10 / Most Watched */}
            {feedData?.most_watched && feedData.most_watched.length > 0 && (
              <MediaRow
                title="Top 10 on StreamVault"
                items={feedData.most_watched}
                isTop10
              />
            )}

            {/* Recently Uploaded */}
            {feedData?.recently_added && feedData.recently_added.length > 0 && (
              <MediaRow
                title="Recently Added"
                items={feedData.recently_added}
              />
            )}

            {/* Most Liked */}
            {feedData?.most_liked && feedData.most_liked.length > 0 && (
              <MediaRow
                title="Popular & Trending"
                items={feedData.most_liked}
              />
            )}

            {/* Critics' Picks */}
            {feedData?.critics_picks && feedData.critics_picks.length > 0 && (
              <MediaRow
                title="Critics' Choice"
                items={feedData.critics_picks}
              />
            )}

            {/* Dynamic Genre Rows */}
            <MediaRow
              title="Action & Adrenaline"
              items={getGenreItems('Action')}
            />

            <MediaRow
              title="Sci-Fi & Futuristic"
              items={getGenreItems('Sci-Fi')}
            />

            <MediaRow
              title="Anime & Animated Marvels"
              items={getGenreItems('Animation')}
            />

            <MediaRow
              title="Crime, Gripping Thrillers & Heists"
              items={getGenreItems('Crime')}
            />

            <MediaRow
              title="Comfort Binges & Comedy"
              items={getGenreItems('Comedy')}
            />
          </div>
        </>
      )}

      {/* Commercial Compliance Footer for Logged-In Users */}
      <Footer />
    </div>
  );
};
