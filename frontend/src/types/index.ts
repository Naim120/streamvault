export type MediaType = 'movie' | 'series';
export type BadgeType = 'recently_added' | 'new_season' | 'none';
export type ProcessingStatus = 'queued' | 'processing' | 'ready' | 'failed';

export interface Profile {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string;
  created_at: string;
  last_login_at: string | null;
  last_ip: string | null;
  last_country: string | null;
}

export interface CastMember {
  name: string;
  character?: string;
}

export interface CreatorDirector {
  name: string;
  role: string;
}

export interface MediaItem {
  id: string;
  tmdb_id: number;
  imdb_id: string | null;
  type: MediaType;
  title: string;
  slug: string;
  synopsis: string | null;
  release_year: number | null;
  maturity_rating: string;
  runtime_minutes: number | null;
  poster_url: string | null;
  backdrop_url: string | null;
  trailer_youtube_key: string | null;
  genres: string[];
  audio_languages?: string[];
  cast_members: CastMember[];
  creators_directors: CreatorDirector[];
  critic_rating: number;
  views_count: number;
  likes_count: number;
  badge_type: BadgeType;
  badge?: string | null;
  badge_expires_at?: string | null;
  is_published: boolean;
  is_featured_billboard?: boolean;
  billboard_priority?: number;
  updated_at?: string;
  created_at: string;
}

export interface Season {
  id: string;
  media_id: string;
  season_number: number;
  title: string;
  episode_count: number;
}

export interface Episode {
  id: string;
  media_id: string;
  season_id: string;
  season_number: number;
  episode_number: number;
  title: string;
  synopsis: string | null;
  duration_seconds: number;
  still_url: string | null;
  is_published: boolean;
}

export interface AudioTrack {
  index: number;
  stream_idx?: number;
  lang: string;
  label: string;
  codec: string;
  channels?: number;
  is_default?: boolean;
}

export interface SubtitleTrack {
  index: number;
  stream_idx?: number;
  lang: string;
  label: string;
  vtt_path: string;
  codec?: string;
}

export interface MediaStream {
  id: string;
  media_id?: string | null;
  episode_id?: string | null;
  master_playlist_path: string;
  is_transcoded: boolean;
  available_qualities: string[];
  audio_tracks: AudioTrack[];
  subtitle_tracks: SubtitleTrack[];
  file_size_bytes: number;
  processing_status: ProcessingStatus;
}

export interface SavedTrackPreferences {
  audio_index?: number | null;
  audio_lang?: string | null;
  audio_label?: string | null;
  subtitle_index?: number | null;
  subtitle_lang?: string | null;
  subtitle_label?: string | null;
}

export interface WatchProgressItem {
  progress_id?: string;
  media_id: string;
  episode_id?: string | null;
  is_completed?: boolean;
  title: string;
  type: MediaType;
  poster_url: string | null;
  backdrop_url: string | null;
  season_number: number | null;
  episode_number: number | null;
  episode_title: string | null;
  episode_synopsis?: string | null;
  episode_still_url?: string | null;
  episode_duration_seconds?: number | null;
  position_seconds: number;
  duration_seconds: number;
  progress_pct: number;
  updated_at: string;
  saved_preferences?: SavedTrackPreferences | null;
  preferred_audio_index?: number | null;
  preferred_audio_lang?: string | null;
  preferred_audio_label?: string | null;
  preferred_subtitle_index?: number | null;
  preferred_subtitle_lang?: string | null;
  preferred_subtitle_label?: string | null;
}

export interface HomeFeedData {
  continue_watching: WatchProgressItem[];
  recently_added: MediaItem[];
  most_watched: MediaItem[];
  most_liked: MediaItem[];
  critics_picks: MediaItem[];
}

export interface CustomList {
  id: string;
  user_id: string;
  name: string;
  created_at: string;
}

export interface CustomListItem {
  list_id: string;
  media_id: string;
  added_at: string;
  media?: MediaItem;
}

export interface SystemCapabilities {
  cpu_cores: number;
  cpu_percent: number;
  total_ram_gb: number;
  available_ram_gb: number;
  ram_percent: number;
  total_disk_gb: number;
  free_disk_gb: number;
  disk_percent: number;
  has_nvenc: boolean;
  has_vaapi: boolean;
  has_qsv: boolean;
  preferred_encoder: string;
  is_low_spec: boolean;
  warning_message: string | null;
}

export interface SystemSettings {
  id: number;
  transcoding_enabled: boolean;
  transcode_mode?: string;
  max_transcode_threads: number;
  hw_accel_type: string;
}
