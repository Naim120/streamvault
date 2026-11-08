import React, { useState, useEffect } from 'react';
import { Edit2, LogOut, Check, Trash2, Clock, ThumbsUp, Bookmark, Play, X, ChevronLeft, ChevronRight } from 'lucide-react';
import { useAuthStore } from '../../store/useAuthStore';
import { useUIStore } from '../../store/useUIStore';
import { usePlayerStore } from '../../store/usePlayerStore';
import { supabase } from '../../lib/supabase';
import { PRESET_AVATARS } from '../../lib/avatars';
import { CustomList, Episode, MediaItem, WatchProgressItem } from '../../types';
import { PosterCard } from '../home/PosterCard';
import { MediaImage } from '../common/MediaImage';

interface WatchHistoryRow {
  id: string;
  user_id: string;
  media_id: string;
  episode_id: string | null;
  season_number: number | null;
  episode_number: number | null;
  position_seconds: number;
  duration_seconds: number;
  watched_at: string;
  media?: MediaItem;
  episode?: Episode;
}

function formatDateGroup(dateString: string): string {
  const d = new Date(dateString);
  const now = new Date();

  const monthNames = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'
  ];

  const isToday =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const isYesterday =
    d.getFullYear() === yesterday.getFullYear() &&
    d.getMonth() === yesterday.getMonth() &&
    d.getDate() === yesterday.getDate();

  const dateFormatted = `${monthNames[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;

  if (isToday) {
    return `Today — ${dateFormatted}`;
  }
  if (isYesterday) {
    return `Yesterday — ${dateFormatted}`;
  }
  return dateFormatted;
}

function formatTime(dateString: string): string {
  const d = new Date(dateString);
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });
}

function formatSubtitle(item: WatchHistoryRow): string {
  const pos = item.position_seconds || 0;
  const dur = item.duration_seconds || item.episode?.duration_seconds || (item.media?.runtime_minutes ? item.media.runtime_minutes * 60 : 0);
  const timeStr = formatTime(item.watched_at);
  const isCompleted = dur > 0 && pos >= dur * 0.95;

  if (isCompleted) {
    return `Completed • ${timeStr}`;
  }
  const posMin = Math.round(pos / 60);
  const durMin = Math.round(dur / 60);

  if (durMin > 0 && posMin > 0) {
    return `${posMin}m of ${durMin}m watched • ${timeStr}`;
  }
  if (posMin > 0) {
    return `${posMin}m watched • ${timeStr}`;
  }
  return `Watched at ${timeStr}`;
}

function getPrimaryTitle(item: WatchHistoryRow): string {
  const mediaTitle = item.media?.title || 'Video Title';
  const isSeries = item.media?.type === 'series' || !!item.episode_id;

  if (isSeries) {
    const sNum = item.season_number || item.episode?.season_number || 1;
    const eNum = item.episode_number || item.episode?.episode_number || 1;
    const epTitle = item.episode?.title ? ` "${item.episode.title}"` : '';
    return `${mediaTitle} — S${sNum}:E${eNum}${epTitle}`;
  }

  return mediaTitle;
}

export const ProfileScreen: React.FC = () => {
  const { user, profile, signOut, updateAvatar } = useAuthStore();
  const { showToast } = useUIStore();
  const { openPlayer } = usePlayerStore();

  const [showAvatarModal, setShowAvatarModal] = useState(false);
  const [continueWatching, setContinueWatching] = useState<WatchProgressItem[]>([]);
  const [likedMedia, setLikedMedia] = useState<MediaItem[]>([]);
  const [customLists, setCustomLists] = useState<CustomList[]>([]);
  const [selectedListId, setSelectedListId] = useState<string | null>(null);
  const [listMedia, setListMedia] = useState<MediaItem[]>([]);
  const [watchHistory, setWatchHistory] = useState<WatchHistoryRow[]>([]);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const ITEMS_PER_PAGE = 10;

  useEffect(() => {
    if (!user) return;

    const loadProfileData = async () => {
      // 1. Continue Watching
      const { data: prog } = await supabase
        .from('watch_progress')
        .select('*, media:media_items(*)')
        .eq('user_id', user.id)
        .eq('is_completed', false)
        .order('updated_at', { ascending: false });
      if (prog) setContinueWatching(prog.map(p => ({ ...p, title: p.media?.title || 'Title', poster_url: p.media?.poster_url })));

      // 2. Liked Titles
      const { data: likes } = await supabase
        .from('user_likes')
        .select('media:media_items(*)')
        .eq('user_id', user.id)
        .order('created_at', { ascending: false });
      if (likes) setLikedMedia(likes.map(l => l.media as unknown as MediaItem).filter(Boolean));

      // 3. Custom Lists
      const { data: lists } = await supabase
        .from('custom_lists')
        .select('*')
        .eq('user_id', user.id);
      if (lists && lists.length > 0) {
        setCustomLists(lists as CustomList[]);
        setSelectedListId(lists[0].id);
      }

      // 4. Watch History with media and episode details
      const { data: hist } = await supabase
        .from('watch_history')
        .select(`
          id,
          user_id,
          media_id,
          episode_id,
          season_number,
          episode_number,
          position_seconds,
          duration_seconds,
          watched_at,
          media:media_items(id, title, type, poster_url, backdrop_url, release_year, runtime_minutes),
          episode:episodes(id, season_number, episode_number, title, still_url, duration_seconds)
        `)
        .eq('user_id', user.id)
        .order('watched_at', { ascending: false });
      if (hist) setWatchHistory(hist as unknown as WatchHistoryRow[]);
    };

    loadProfileData();
  }, [user]);

  // Load items for active selected custom list
  useEffect(() => {
    if (!selectedListId) return;
    const fetchListItems = async () => {
      const { data } = await supabase
        .from('custom_list_items')
        .select('media:media_items(*)')
        .eq('list_id', selectedListId);
      if (data) setListMedia(data.map(d => d.media as unknown as MediaItem).filter(Boolean));
    };
    fetchListItems();
  }, [selectedListId]);

  const handleAvatarSelect = async (url: string) => {
    await updateAvatar(url);
    setShowAvatarModal(false);
    showToast('Profile avatar updated');
  };

  const handleClearHistory = async () => {
    if (!user) return;
    await supabase.from('watch_history').delete().eq('user_id', user.id);
    setWatchHistory([]);
    setCurrentPage(1);
    showToast('Watch history cleared');
  };

  const handleDeleteHistoryItem = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!user) return;
    const { error } = await supabase.from('watch_history').delete().eq('id', id).eq('user_id', user.id);
    if (!error) {
      setWatchHistory((prev) => prev.filter((item) => item.id !== id));
      showToast('Removed item from watch history');
    }
  };

  const handlePlayHistoryItem = (item: WatchHistoryRow) => {
    if (!item.media) return;
    const isCompleted = item.duration_seconds > 0 && item.position_seconds >= item.duration_seconds * 0.95;
    const startSec = isCompleted ? 0 : (item.position_seconds > 5 ? item.position_seconds : 0);

    if (item.episode) {
      openPlayer(item.media, item.episode, startSec);
    } else if (item.episode_id) {
      const ep: Episode = {
        id: item.episode_id,
        media_id: item.media_id,
        season_id: '',
        season_number: item.season_number || 1,
        episode_number: item.episode_number || 1,
        title: `Episode ${item.episode_number || 1}`,
        synopsis: '',
        duration_seconds: item.duration_seconds || 2700,
        still_url: null,
        is_published: true
      };
      openPlayer(item.media, ep, startSec);
    } else {
      openPlayer(item.media, null, startSec);
    }
  };

  return (
    <div className="min-h-screen bg-black pt-20 px-4 sm:px-8 pb-32 max-w-7xl mx-auto space-y-10">
      {/* Profile Header */}
      <div className="bg-[#141414] border border-neutral-800 rounded-3xl p-6 sm:p-8 flex flex-col sm:flex-row items-center gap-6 shadow-2xl relative">
        {/* Avatar with Edit Badge */}
        <div className="relative group cursor-pointer" onClick={() => setShowAvatarModal(true)}>
          <img
            src={profile?.avatar_url || 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150'}
            alt="User Avatar"
            className="w-24 h-24 sm:w-28 sm:h-28 rounded-2xl object-cover border-2 border-neutral-700 shadow-xl group-hover:opacity-80 transition"
          />
          <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition bg-black/40 rounded-2xl">
            <Edit2 className="w-6 h-6 text-white drop-shadow" />
          </div>
        </div>

        {/* User Info */}
        <div className="flex-1 text-center sm:text-left space-y-1">
          <div className="flex items-center justify-center sm:justify-start gap-2">
            <h1 className="text-2xl sm:text-3xl font-black text-white">
              {profile?.display_name || user?.email?.split('@')[0]}
            </h1>
            <span className="px-2 py-0.5 bg-red-600/20 text-red-500 border border-red-500/30 rounded text-[10px] font-bold uppercase tracking-wider">
              Member
            </span>
          </div>
          <p className="text-neutral-400 text-xs sm:text-sm">{user?.email}</p>
          <p className="text-neutral-500 text-[11px] pt-1">
            Registered: {user?.created_at ? new Date(user.created_at).toLocaleDateString() : 'Active'}
          </p>
        </div>

        {/* Sign Out Button */}
        <button
          onClick={signOut}
          className="px-5 py-2.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 font-semibold rounded-xl text-xs transition flex items-center gap-2"
        >
          <LogOut className="w-4 h-4" />
          Sign Out
        </button>
      </div>

      {/* 12 Built-In Preset Avatars Modal */}
      {showAvatarModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4">
          <div className="bg-[#181818] border border-neutral-800 rounded-3xl max-w-xl w-full p-6 sm:p-8 shadow-2xl">
            <h3 className="text-xl font-bold text-white mb-2">Choose Profile Avatar</h3>
            <p className="text-neutral-400 text-xs mb-6">Select from 12 high-contrast avatars</p>
            <div className="grid grid-cols-3 sm:grid-cols-4 gap-4 max-h-96 overflow-y-auto pr-1 mb-6">
              {PRESET_AVATARS.map((av) => (
                <button
                  key={av.id}
                  onClick={() => handleAvatarSelect(av.url)}
                  className={`group relative rounded-xl overflow-hidden border-2 transition ${
                    profile?.avatar_url === av.url ? 'border-red-600 ring-2 ring-red-600/50' : 'border-neutral-800 hover:border-neutral-600'
                  }`}
                >
                  <img src={av.url} alt={av.name} className="w-full aspect-square object-cover" />
                  <div className="absolute inset-x-0 bottom-0 bg-black/80 py-1 text-center text-[10px] text-white font-medium truncate px-1">
                    {av.name}
                  </div>
                </button>
              ))}
            </div>
            <div className="flex justify-end">
              <button
                onClick={() => setShowAvatarModal(false)}
                className="px-5 py-2 bg-neutral-800 hover:bg-neutral-700 text-white rounded-xl text-xs font-semibold"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Saved Custom Lists */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bookmark className="w-5 h-5 text-red-500" />
            <h2 className="text-xl font-bold text-white">My Saved Lists</h2>
          </div>
        </div>

        {/* List Tabs */}
        {customLists.length > 0 && (
          <div className="flex gap-2 overflow-x-auto no-scrollbar pb-2">
            {customLists.map((cl) => (
              <button
                key={cl.id}
                onClick={() => setSelectedListId(cl.id)}
                className={`px-4 py-2 rounded-xl text-xs font-bold transition whitespace-nowrap ${
                  selectedListId === cl.id ? 'bg-red-600 text-white' : 'bg-[#181818] text-neutral-400 hover:text-white'
                }`}
              >
                {cl.name}
              </button>
            ))}
          </div>
        )}

        {listMedia.length === 0 ? (
          <p className="text-neutral-500 text-xs py-4">No titles added to this list yet.</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4 sm:gap-6">
            {listMedia.map((m) => (
              <div key={m.id} className="w-full min-w-0">
                <PosterCard media={m} isGrid />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Liked Movies & Series */}
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <ThumbsUp className="w-5 h-5 text-red-500" />
          <h2 className="text-xl font-bold text-white">Liked Titles</h2>
        </div>
        {likedMedia.length === 0 ? (
          <p className="text-neutral-500 text-xs py-2">You haven't liked any titles yet.</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4 sm:gap-6">
            {likedMedia.map((m) => (
              <div key={m.id} className="w-full min-w-0">
                <PosterCard media={m} isGrid />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Watch History Log */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Clock className="w-5 h-5 text-red-500" />
            <h2 className="text-xl font-bold text-white">Watch History</h2>
            {watchHistory.length > 0 && (
              <span className="text-xs bg-neutral-800 text-neutral-400 font-mono px-2 py-0.5 rounded-full">
                {watchHistory.length}
              </span>
            )}
          </div>
          {watchHistory.length > 0 && (
            <button
              onClick={handleClearHistory}
              className="text-xs text-neutral-500 hover:text-red-500 transition flex items-center gap-1 cursor-pointer"
            >
              <Trash2 className="w-3.5 h-3.5" /> Clear All History
            </button>
          )}
        </div>

        {watchHistory.length === 0 ? (
          <p className="text-neutral-500 text-xs py-4 bg-[#141414] border border-neutral-800/80 rounded-2xl text-center">
            No watch history recorded yet.
          </p>
        ) : (
          <div className="bg-[#141414] border border-neutral-800 rounded-2xl overflow-hidden shadow-xl">
            {(() => {
              const totalPages = Math.max(1, Math.ceil(watchHistory.length / ITEMS_PER_PAGE));
              const paginatedHistory = watchHistory.slice((currentPage - 1) * ITEMS_PER_PAGE, currentPage * ITEMS_PER_PAGE);

              const dateGroups: { dateHeader: string; items: WatchHistoryRow[] }[] = [];
              paginatedHistory.forEach((item) => {
                const header = formatDateGroup(item.watched_at);
                const existing = dateGroups.find((g) => g.dateHeader === header);
                if (existing) {
                  existing.items.push(item);
                } else {
                  dateGroups.push({ dateHeader: header, items: [item] });
                }
              });

              return (
                <>
                  <div className="divide-y divide-neutral-800/80">
                    {dateGroups.map((group) => (
                      <div key={group.dateHeader}>
                        {/* Clean sticky date section header */}
                        <div className="px-4 py-2 bg-neutral-900/95 text-neutral-300 font-bold text-xs uppercase tracking-wider sticky top-0 z-10 backdrop-blur border-b border-neutral-800">
                          {group.dateHeader}
                        </div>

                        <div className="divide-y divide-neutral-800/60">
                          {group.items.map((h) => {
                            const isCompleted = h.duration_seconds > 0 && h.position_seconds >= h.duration_seconds * 0.95;
                            const primaryTitle = getPrimaryTitle(h);
                            const subtitle = formatSubtitle(h);

                            return (
                              <div
                                key={h.id}
                                className="p-3.5 sm:p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 hover:bg-[#1a1a1a] transition group"
                              >
                                {/* Thumbnail & Info */}
                                <div className="flex items-center gap-3.5 flex-1 min-w-0">
                                  {/* 16:9 Thumbnail with bottom progress bar */}
                                  <div className="relative w-28 sm:w-36 aspect-video bg-neutral-900 rounded-xl overflow-hidden flex-shrink-0 border border-neutral-800">
                                    <img
                                      src={
                                        h.episode?.still_url ||
                                        h.media?.backdrop_url ||
                                        h.media?.poster_url ||
                                        'https://images.unsplash.com/photo-1574375927938-d5a98e8ffe85?w=500'
                                      }
                                      alt={primaryTitle}
                                      className="w-full h-full object-cover group-hover:scale-105 transition duration-300"
                                    />
                                    {/* Red progress bar along bottom edge */}
                                    {h.duration_seconds > 0 && h.position_seconds > 0 && (
                                      <div className="absolute bottom-0 left-0 right-0 h-1 bg-black/60 z-10">
                                        <div
                                          className="bg-red-600 h-full"
                                          style={{
                                            width: `${Math.min(100, Math.max(0, (h.position_seconds / h.duration_seconds) * 100))}%`
                                          }}
                                        />
                                      </div>
                                    )}
                                  </div>

                                  {/* Primary Title + Progress Subtitle */}
                                  <div className="min-w-0 flex-1 space-y-1">
                                    <h4 className="text-white font-bold text-xs sm:text-sm group-hover:text-red-500 transition truncate">
                                      {primaryTitle}
                                    </h4>
                                    <p className="text-neutral-400 text-xs">
                                      {subtitle}
                                    </p>
                                  </div>
                                </div>

                                {/* Quick Action Buttons */}
                                <div className="flex items-center gap-2 self-end sm:self-center">
                                  <button
                                    onClick={() => handlePlayHistoryItem(h)}
                                    className="px-3 py-1.5 bg-white hover:bg-neutral-200 text-black font-bold rounded-lg text-xs transition flex items-center gap-1.5 cursor-pointer shadow"
                                    title="Play item"
                                  >
                                    <Play className="w-3.5 h-3.5 fill-current" />
                                    <span>
                                      {isCompleted
                                        ? 'Play Again'
                                        : h.position_seconds > 5
                                        ? 'Resume'
                                        : 'Play'}
                                    </span>
                                  </button>
                                  <button
                                    onClick={(e) => handleDeleteHistoryItem(h.id, e)}
                                    className="p-1.5 text-neutral-400 hover:text-white hover:bg-neutral-800 rounded-lg transition cursor-pointer"
                                    title="Remove from history"
                                    aria-label="Remove item"
                                  >
                                    <X className="w-4 h-4" />
                                  </button>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Pagination Controls */}
                  {totalPages > 1 && (
                    <div className="flex items-center justify-between px-4 py-3 bg-[#181818] border-t border-neutral-800 text-xs text-neutral-400">
                      <button
                        onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                        disabled={currentPage === 1}
                        className="px-3 py-1.5 rounded-lg bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold transition flex items-center gap-1 cursor-pointer"
                      >
                        <ChevronLeft className="w-3.5 h-3.5" /> Previous
                      </button>
                      <span className="font-mono text-neutral-300">
                        Page {currentPage} of {totalPages}
                      </span>
                      <button
                        onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                        disabled={currentPage === totalPages}
                        className="px-3 py-1.5 rounded-lg bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold transition flex items-center gap-1 cursor-pointer"
                      >
                        Next <ChevronRight className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </>
              );
            })()}
          </div>
        )}
      </div>
    </div>
  );
};
