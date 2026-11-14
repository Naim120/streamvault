import React, { useState, useEffect } from 'react';
import { Play, Info, MoreVertical, Trash2, Eye } from 'lucide-react';
import { WatchProgressItem, MediaItem, Episode } from '../../types';
import { usePlayerStore } from '../../store/usePlayerStore';
import { useUIStore } from '../../store/useUIStore';
import { useAuthStore } from '../../store/useAuthStore';
import { supabase } from '../../lib/supabase';
import { MediaImage } from '../common/MediaImage';
import { prewarmStream } from '../../lib/streamPrewarm';

interface ContinueWatchingRowProps {
  items: WatchProgressItem[];
  onRefresh: () => void;
}

export const ContinueWatchingRow: React.FC<ContinueWatchingRowProps> = ({ items, onRefresh }) => {
  const { openPlayer } = usePlayerStore();
  const { openDetail, showToast } = useUIStore();
  const { user, profile } = useAuthStore();
  const [activeMenuId, setActiveMenuId] = useState<string | null>(null);

  useEffect(() => {
    if (!items || items.length === 0) return;
    // Prewarm top 3 items in continue watching row
    items.slice(0, 3).forEach((item) => {
      prewarmStream(item.episode_id || item.media_id, user?.id);
    });
  }, [items, user?.id]);

  if (!items || items.length === 0) return null;

  const handleRemove = async (item: WatchProgressItem) => {
    if (!user) return;
    await supabase.from('watch_progress').delete().eq('user_id', user.id).eq('media_id', item.media_id);
    showToast('Removed from Continue Watching');
    onRefresh();
  };

  const handlePlay = (item: WatchProgressItem) => {
    // Construct lightweight MediaItem
    const m: MediaItem = {
      id: item.media_id,
      title: item.title,
      type: item.type,
      poster_url: item.poster_url,
      backdrop_url: item.backdrop_url,
      tmdb_id: 0,
      imdb_id: null,
      slug: '',
      synopsis: null,
      release_year: 2026,
      maturity_rating: 'U/A 16+',
      runtime_minutes: Math.round(item.duration_seconds / 60),
      trailer_youtube_key: null,
      genres: [],
      cast_members: [],
      creators_directors: [],
      critic_rating: 8.0,
      views_count: 0,
      likes_count: 0,
      badge_type: 'none',
      is_published: true,
      created_at: item.updated_at
    };

    let ep: Episode | null = null;
    if (item.type === 'series' && item.episode_id) {
      ep = {
        id: item.episode_id,
        media_id: item.media_id,
        season_id: '',
        season_number: item.season_number || 1,
        episode_number: item.episode_number || 1,
        title: item.episode_title || 'Episode',
        synopsis: item.episode_synopsis || null,
        duration_seconds: item.episode_duration_seconds || item.duration_seconds || 0,
        still_url: item.episode_still_url || item.backdrop_url || item.poster_url,
        is_published: true
      };
    }

    openPlayer(m, ep, item.position_seconds);
  };

  const handleOpenInfo = async (item: WatchProgressItem) => {
    const { data } = await supabase.from('media_items').select('*').eq('id', item.media_id).single();
    if (data) openDetail(data as MediaItem);
  };

  const userName = profile?.display_name || user?.email?.split('@')[0] || 'You';

  return (
    <div className="py-4 space-y-3">
      <h2 className="text-white font-bold text-lg md:text-xl tracking-tight px-4 sm:px-8">
        Continue Watching for {userName}
      </h2>

      <div className="flex gap-4 overflow-x-auto no-scrollbar px-4 sm:px-8 py-2">
        {items.map((item) => (
          <div
            key={item.media_id}
            tabIndex={0}
            role="button"
            data-tv-card="true"
            aria-label={`Resume watching ${item.title}`}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handlePlay(item);
              }
            }}
            onMouseEnter={() => prewarmStream(item.episode_id || item.media_id, user?.id)}
            className="relative flex-none w-44 sm:w-52 aspect-[2/3] rounded-xl overflow-hidden bg-[#181818] netflix-card-hover group border border-neutral-800/60 cursor-pointer"
          >
            {/* Poster Thumbnail */}
            <MediaImage
              title={item.title}
              posterUrl={item.poster_url}
              backdropUrl={item.backdrop_url}
              aspect="poster"
            />

            {/* Centered Play Button Overlay */}
            <div 
              onClick={() => handlePlay(item)}
              className="absolute inset-0 bg-black/40 flex items-center justify-center cursor-pointer transition group-hover:bg-black/25"
            >
              <div className="w-12 h-12 rounded-full bg-white/20 backdrop-blur-md flex items-center justify-center text-white border border-white/30 group-hover:scale-110 transition shadow-lg">
                <Play className="w-5 h-5 fill-current ml-0.5" />
              </div>
            </div>

            {/* Bottom Details Strip & Red Progress Bar */}
            <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black via-black/90 to-transparent p-3 pt-6 z-10">
              <h4 className="text-white font-bold text-xs line-clamp-1 mb-0.5">
                {item.title}
              </h4>
              {item.type === 'series' && item.season_number && (
                <p className="text-[10px] text-neutral-400 line-clamp-1 mb-1.5">
                  S{item.season_number}:E{item.episode_number} {item.episode_title}
                </p>
              )}

              {/* Red Progress Bar */}
              <div className="w-full bg-neutral-700/80 h-1.5 rounded-full overflow-hidden mb-2">
                <div
                  className="bg-red-600 h-full rounded-full"
                  style={{ width: `${Math.min(100, Math.max(5, item.progress_pct))}%` }}
                />
              </div>

              {/* Action Buttons: Info & 3-dot menu */}
              <div className="flex items-center justify-between text-neutral-400">
                <button
                  onClick={() => handleOpenInfo(item)}
                  className="hover:text-white transition p-1"
                  title="Details"
                >
                  <Info className="w-4 h-4" />
                </button>

                <div className="relative">
                  <button
                    onClick={() => setActiveMenuId(activeMenuId === item.media_id ? null : item.media_id)}
                    className="hover:text-white transition p-1"
                  >
                    <MoreVertical className="w-4 h-4" />
                  </button>

                  {/* 3-Dot Quick Menu */}
                  {activeMenuId === item.media_id && (
                    <div className="absolute right-0 bottom-8 w-44 bg-neutral-900 border border-neutral-700 rounded-xl p-1.5 shadow-2xl z-30 backdrop-blur-xl">
                      <button
                        onClick={() => {
                          handleOpenInfo(item);
                          setActiveMenuId(null);
                        }}
                        className="w-full text-left px-2.5 py-1.5 rounded-lg text-xs text-neutral-300 hover:bg-neutral-800 transition flex items-center gap-2"
                      >
                        <Eye className="w-3.5 h-3.5" />
                        <span>View Details</span>
                      </button>
                      <button
                        onClick={() => {
                          handleRemove(item);
                          setActiveMenuId(null);
                        }}
                        className="w-full text-left px-2.5 py-1.5 rounded-lg text-xs text-red-400 hover:bg-red-600/15 transition flex items-center gap-2"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        <span>Remove from row</span>
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
