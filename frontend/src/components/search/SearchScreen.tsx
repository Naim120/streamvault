import React, { useState, useEffect, useMemo } from 'react';
import { Search, Play, X, Sparkles, Filter } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { supabase } from '../../lib/supabase';
import { useUIStore } from '../../store/useUIStore';
import { usePlayerStore } from '../../store/usePlayerStore';
import { MediaItem } from '../../types';
import { PosterCard } from '../home/PosterCard';
import { MediaImage } from '../common/MediaImage';
import { sanitizeSearchInput, escapeRegex } from '../../lib/security';

const FILTER_PILLS = [
  { id: 'all', label: 'All' },
  { id: 'movies', label: 'Movies' },
  { id: 'series', label: 'Series' },
  { id: 'action', label: 'Action' },
  { id: 'sci-fi', label: 'Sci-Fi' },
  { id: 'drama', label: 'Drama' },
  { id: 'animation', label: 'Animation' },
  { id: 'crime', label: 'Crime' },
];

const SEARCH_STOPWORDS = new Set([
  'all', 'the', 'and', 'for', 'with', 'from', 'this', 'that', 'have',
  'into', 'when', 'who', 'what', 'where', 'why', 'how', 'are', 'was',
  'were', 'been', 'being', 'its', 'his', 'her', 'their', 'our', 'your'
]);

function scoreLocalItem(item: MediaItem, qNorm: string, qTokens: string[]): number {
  const title = (item.title || '').trim();
  const titleLower = title.toLowerCase();
  const genresLower = (item.genres || []).map(g => String(g).trim().toLowerCase());
  const releaseYearStr = String(item.release_year || '').trim();
  const synopsisLower = (item.synopsis || '').trim().toLowerCase();

  let score = 0;

  // 1. Exact title match (title_lower == q): +100
  if (titleLower === qNorm) {
    score += 100;
  }
  // 2. Title starts with q (title_lower.startswith(q)): +85
  else if (titleLower.startsWith(qNorm)) {
    score += 85;
  }
  // 3. Word-Prefix in Title: Every token in q_tokens matches start of a word in title_lower: +70
  else if (qTokens.length > 0 && qTokens.every(tok => new RegExp(`\\b${escapeRegex(tok)}`, 'i').test(titleLower))) {
    score += 70;
  }
  // 4. Substring in Title (only if len(q) >= 3 and q not in STOPWORDS and q in title_lower): +45
  else if (qNorm.length >= 3 && !SEARCH_STOPWORDS.has(qNorm) && titleLower.includes(qNorm)) {
    score += 45;
  }

  // Genre, Type & Release Year Matching:
  // Any genre starts with q (or matches word boundary \b) or str(release_year) == q: +35
  const genreMatched = genresLower.some(g => g.startsWith(qNorm) || new RegExp(`\\b${escapeRegex(qNorm)}`, 'i').test(g));
  const yearMatched = Boolean(releaseYearStr && releaseYearStr === qNorm);
  if (genreMatched || yearMatched) {
    score += 35;
  }

  // Synopsis Matching (Strict Whole-Word Only for Non-Stopwords >= 4 Chars):
  // Only check synopsis if len(q) >= 4 and q not in STOPWORDS, and require an exact whole-word boundary match: +15
  if (qNorm.length >= 4 && !SEARCH_STOPWORDS.has(qNorm) && new RegExp(`\\b${escapeRegex(qNorm)}\\b`, 'i').test(synopsisLower)) {
    score += 15;
  }

  // Multi-word requirement:
  // If q has multiple words, require every non-stopword token to match at least Title, Genre, or Year
  if (qTokens.length > 1 && score > 0) {
    const nonStopTokens = qTokens.filter(tok => !SEARCH_STOPWORDS.has(tok));
    const tokensToVerify = nonStopTokens.length > 0 ? nonStopTokens : qTokens;
    const allMatch = tokensToVerify.every(tok => {
      const inTitle = new RegExp(`\\b${escapeRegex(tok)}`, 'i').test(titleLower);
      const inGenre = genresLower.some(g => g.startsWith(tok) || new RegExp(`\\b${escapeRegex(tok)}`, 'i').test(g));
      const inYear = Boolean(releaseYearStr && releaseYearStr === tok);
      return inTitle || inGenre || inYear;
    });
    if (!allMatch) {
      score = 0;
    }
  }

  return score;
}

export const SearchScreen: React.FC = () => {
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [selectedFilter, setSelectedFilter] = useState('all');

  const { openDetail } = useUIStore();
  const { openPlayer } = usePlayerStore();

  // 1. Pre-warm & share published catalog cache with HomeScreen
  const { data: allMedia, isLoading: isLoadingInitialCatalog } = useQuery<MediaItem[]>({
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
    staleTime: 1000 * 60 * 5, // 5 minutes
  });

  // 2. 120ms debounce for background network validation
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(sanitizeSearchInput(query).trim());
    }, 120);
    return () => clearTimeout(timer);
  }, [query]);

  // 3. Instant 0ms local search computed via useMemo across cached catalog
  const localResults = useMemo(() => {
    const rawItems = allMedia || [];
    let filtered = rawItems;

    // Apply category / genre filter
    if (selectedFilter === 'movies') {
      filtered = filtered.filter(m => m.type === 'movie');
    } else if (selectedFilter === 'series') {
      filtered = filtered.filter(m => m.type === 'series');
    } else if (selectedFilter !== 'all') {
      const filterLow = selectedFilter.toLowerCase();
      filtered = filtered.filter(m => (m.genres || []).some(g => String(g).toLowerCase().includes(filterLow)));
    }

    const cleanQ = sanitizeSearchInput(query).toLowerCase();
    if (!cleanQ) {
      // Default recommended sorting
      return [...filtered].sort((a, b) => {
        const aHero = a.is_featured_billboard ? 1 : 0;
        const bHero = b.is_featured_billboard ? 1 : 0;
        if (aHero !== bHero) return bHero - aHero;
        return (b.views_count || 0) - (a.views_count || 0);
      }).slice(0, 24);
    }

    const qTokens = cleanQ.split(' ').filter(Boolean);
    const scored: Array<{ item: MediaItem; score: number }> = [];

    for (const item of filtered) {
      const score = scoreLocalItem(item, cleanQ, qTokens);
      if (score > 0) {
        scored.push({ item, score });
      }
    }

    scored.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      return (b.item.views_count || 0) - (a.item.views_count || 0);
    });

    return scored.map(s => s.item).slice(0, 24);
  }, [allMedia, query, selectedFilter]);

  // 4. Background query against high-throughput in-memory search endpoint
  const { data: serverResults, isFetching: isFetchingServer } = useQuery<MediaItem[]>({
    queryKey: ['catalog_search', debouncedQuery, selectedFilter],
    queryFn: async ({ signal }) => {
      let url = `/api/metadata/search-catalog?limit=24`;
      if (debouncedQuery) {
        url += `&q=${encodeURIComponent(debouncedQuery)}`;
      }
      if (selectedFilter === 'movies') url += `&type=movie`;
      else if (selectedFilter === 'series') url += `&type=series`;
      else if (selectedFilter !== 'all') url += `&genre=${encodeURIComponent(selectedFilter)}`;

      const res = await fetch(url, { signal });
      if (!res.ok) throw new Error('Search failed');
      return res.json() as Promise<MediaItem[]>;
    },
    placeholderData: (prev) => prev,
    staleTime: 1000 * 30, // 30s client cache
  });

  // Zero-flicker result resolution: use server results if synchronized, otherwise 0ms local results
  const isQuerySync = debouncedQuery === sanitizeSearchInput(query).trim();
  const displayResults = (isQuerySync && serverResults) ? serverResults : localResults;

  // Only show skeletons if no local items exist yet and initial catalog is still downloading
  const hasLocalCatalog = Boolean(allMedia && (allMedia as MediaItem[]).length > 0);
  const showInitialSkeleton = Boolean(isLoadingInitialCatalog && !hasLocalCatalog);

  return (
    <div className="min-h-screen bg-black pt-20 px-4 sm:px-8 pb-28 max-w-7xl mx-auto">
      {/* 1. Sleek Search Input Bar */}
      <div className="relative max-w-2xl mx-auto mb-4">
        <Search className="w-5 h-5 text-neutral-400 absolute left-4 top-1/2 -translate-y-1/2 pointer-events-none" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search movies, TV shows, genres, actors, release years..."
          autoFocus
          className="w-full bg-[#1c1c1c] border border-neutral-700/80 rounded-2xl py-3.5 pl-12 pr-10 text-white text-sm sm:text-base focus:outline-none focus:border-red-500 shadow-xl transition placeholder:text-neutral-500"
        />
        {query && (
          <button
            onClick={() => setQuery('')}
            aria-label="Clear search input"
            className="text-neutral-400 hover:text-white absolute right-3.5 top-1/2 -translate-y-1/2 p-1.5 rounded-full hover:bg-neutral-800 transition cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* 2. Sleek One-Click Genre & Type Filter Pills */}
      <div className="max-w-2xl mx-auto mb-8 flex items-center gap-2 overflow-x-auto pb-2 scrollbar-none">
        <div className="flex items-center gap-1.5 text-neutral-400 text-xs font-semibold mr-1 flex-shrink-0">
          <Filter className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Filter:</span>
        </div>
        {FILTER_PILLS.map((pill) => {
          const isActive = selectedFilter === pill.id;
          return (
            <button
              key={pill.id}
              onClick={() => setSelectedFilter(pill.id)}
              className={`px-3.5 py-1.5 rounded-full text-xs font-semibold whitespace-nowrap transition cursor-pointer border ${
                isActive
                  ? 'bg-red-600 border-red-500 text-white shadow-lg shadow-red-600/30'
                  : 'bg-neutral-900/90 border-neutral-800 text-neutral-300 hover:text-white hover:bg-neutral-800'
              }`}
            >
              {pill.label}
            </button>
          );
        })}
      </div>

      {/* 3. Search Results & Showcase */}
      {showInitialSkeleton ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4 animate-pulse">
          {[...Array(12)].map((_, i) => (
            <div key={i} className="aspect-[2/3] bg-neutral-900 rounded-xl" />
          ))}
        </div>
      ) : query.trim() ? (
        /* State A: Active Search Results */
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-white font-bold text-lg flex items-center gap-2">
              <span>Results for "{query.trim()}"</span>
              {isFetchingServer && (
                <span className="w-2 h-2 rounded-full bg-red-500 animate-ping" title="Syncing..." />
              )}
            </h2>
            <span className="text-neutral-500 text-xs font-mono">
              {displayResults.length} {displayResults.length === 1 ? 'title' : 'titles'} found
            </span>
          </div>

          {displayResults.length === 0 ? (
            <div className="text-center py-20 text-neutral-500 space-y-2">
              <p className="text-base font-semibold text-neutral-300">
                No titles found for "{query.trim()}"
              </p>
              <p className="text-xs">
                Try searching for another title, actor, or genre keyword, or select the "All" filter.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4 sm:gap-6">
              {displayResults.map((item) => (
                <div key={item.id} className="w-full min-w-0">
                  <PosterCard media={item} isGrid />
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        /* State B: Empty Query -> Recommended Shows & Movies Showcase */
        <div className="max-w-3xl mx-auto space-y-4">
          <div className="flex items-center justify-between text-white font-bold text-lg mb-2">
            <div className="flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-red-500" />
              <h3>Recommended Shows & Movies</h3>
            </div>
            {selectedFilter !== 'all' && (
              <span className="text-xs text-neutral-400 capitalize font-normal">
                Category: {selectedFilter}
              </span>
            )}
          </div>

          <div className="space-y-3">
            {displayResults.slice(0, 10).map((item) => (
              <div
                key={item.id}
                onClick={() => openDetail(item)}
                className="bg-[#141414] hover:bg-[#1f1f1f] border border-neutral-800/60 p-2.5 rounded-2xl flex items-center justify-between gap-4 cursor-pointer transition duration-150 group shadow-md"
              >
                {/* Left: 16:9 Backdrop Thumbnail */}
                <div className="relative w-28 sm:w-36 aspect-video rounded-xl overflow-hidden bg-neutral-900 flex-shrink-0">
                  <MediaImage
                    title={item.title}
                    posterUrl={item.poster_url}
                    backdropUrl={item.backdrop_url}
                    aspect="backdrop"
                    fallbackYear={item.release_year}
                    imgClassName="group-hover:scale-105 transition duration-300"
                  />
                </div>

                {/* Center: Title & Metadata */}
                <div className="flex-1 min-w-0">
                  <h4 className="text-white font-bold text-sm sm:text-base group-hover:text-red-500 transition line-clamp-1">
                    {item.title}
                  </h4>
                  <div className="flex items-center gap-2 text-xs text-neutral-400 mt-1">
                    <span>{item.release_year}</span>
                    <span>•</span>
                    <span className="capitalize">{item.type}</span>
                    {item.maturity_rating && (
                      <>
                        <span>•</span>
                        <span className="px-1 border border-neutral-700 rounded text-[10px]">
                          {item.maturity_rating}
                        </span>
                      </>
                    )}
                    {item.genres && item.genres.length > 0 && (
                      <>
                        <span className="hidden sm:inline">•</span>
                        <span className="hidden sm:inline text-neutral-500 truncate max-w-[150px]">
                          {item.genres.slice(0, 2).join(', ')}
                        </span>
                      </>
                    )}
                  </div>
                </div>

                {/* Right: Circular Play Button */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    openPlayer(item, null, 0);
                  }}
                  className="w-10 h-10 rounded-full border border-neutral-600 group-hover:border-red-500 group-hover:bg-red-600 text-white flex items-center justify-center transition flex-shrink-0 mr-2 cursor-pointer shadow-md"
                  title="Play"
                >
                  <Play className="w-4 h-4 fill-current ml-0.5" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
