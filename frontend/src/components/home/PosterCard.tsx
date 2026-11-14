import React from 'react';
import { Play, Plus, Info, Star } from 'lucide-react';
import { MediaItem } from '../../types';
import { useUIStore } from '../../store/useUIStore';
import { usePlayerStore } from '../../store/usePlayerStore';
import { MediaImage } from '../common/MediaImage';
import { prevalidateTrailer } from '../../lib/trailerValidator';

export interface PosterCardProps {
  media: MediaItem;
  rank?: number; // 1 to 10 for Top 10 rows
  isGrid?: boolean;
  className?: string;
}

export const PosterCard: React.FC<PosterCardProps> = ({
  media,
  rank,
  isGrid = true,
  className
}) => {
  const { openDetail } = useUIStore();
  const { openPlayer } = usePlayerStore();

  const getActiveBadge = (item: MediaItem): string | null => {
    if (item.badge_expires_at) {
      const expiresAt = new Date(item.badge_expires_at).getTime();
      if (!isNaN(expiresAt) && expiresAt <= Date.now()) {
        return null;
      }
    }
    if (item.badge && item.badge.trim() !== '' && item.badge.toLowerCase() !== 'none') {
      return item.badge;
    }
    if (item.badge_type === 'recently_added') return 'Recently added';
    if (item.badge_type === 'new_season') return 'New Season';
    return null;
  };

  const badgeText = getActiveBadge(media);

  return (
    <div
      tabIndex={0}
      role="button"
      data-tv-card="true"
      aria-label={`View details for ${media.title}`}
      onClick={() => openDetail(media)}
      onMouseEnter={() => {
        if (media.trailer_youtube_key) {
          prevalidateTrailer(media.trailer_youtube_key);
        }
      }}
      onFocus={() => {
        if (media.trailer_youtube_key) {
          prevalidateTrailer(media.trailer_youtube_key);
        }
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openDetail(media);
        }
      }}
      className={`relative rounded-xl overflow-hidden cursor-pointer group/poster netflix-card-hover bg-[#181818] shadow-md hover:shadow-2xl transition duration-300 min-w-0 ${
        isGrid ? 'w-full aspect-[2/3]' : 'flex-none w-36 sm:w-44 md:w-52 aspect-[2/3]'
      } ${className || ''}`}
    >
      {/* Rank Number for Top 10 (Large red/white corner badge) */}
      {rank && (
        <div className="absolute top-2 left-2 z-20 bg-red-600 text-white font-black text-xs px-2 py-0.5 rounded shadow-lg">
          TOP {rank}
        </div>
      )}

      {/* Centralized Resilient Media Artwork */}
      <MediaImage
        title={media.title}
        posterUrl={media.poster_url}
        backdropUrl={media.backdrop_url}
        aspect="poster"
        fallbackYear={media.release_year}
        className="rounded-xl"
      />

      {/* Promotional Badge (Bottom-centered Red Badge) */}
      {badgeText && (
        <div className="absolute bottom-2 left-1/2 -translate-x-1/2 z-10 bg-red-600 text-white text-[10px] font-extrabold uppercase px-2 py-0.5 rounded-sm shadow-md whitespace-nowrap">
          {badgeText}
        </div>
      )}

      {/* Hover Card Overlay (Netflix Style) */}
      <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/30 to-transparent opacity-0 group-hover/poster:opacity-100 transition-opacity duration-200 flex flex-col justify-end p-3 z-20">
        <h4 className="text-white font-bold text-xs line-clamp-1 mb-1">
          {media.title}
        </h4>

        <div className="flex items-center gap-2 text-[10px] text-neutral-300 mb-2">
          <span>{media.release_year}</span>
          <span className="px-1 border border-neutral-600 rounded text-[9px]">
            {media.maturity_rating || 'U/A 16+'}
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            onClick={(e) => {
              e.stopPropagation();
              openPlayer(media, null, 0);
            }}
            className="p-2 bg-white hover:bg-neutral-200 text-black rounded-full transition"
            title="Play"
          >
            <Play className="w-3.5 h-3.5 fill-current ml-0.5" />
          </button>

          <button
            onClick={(e) => {
              e.stopPropagation();
              openDetail(media);
            }}
            className="p-2 bg-neutral-800/80 hover:bg-neutral-700 text-white rounded-full transition ml-auto"
            title="More Info"
          >
            <Info className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
};
