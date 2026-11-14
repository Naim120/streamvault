-- ==============================================================================
-- STREAMVAULT MASTER DATABASE SETUP & REPLICATION SCRIPT
-- ==============================================================================
-- Production-ready, idempotent schema for fresh server deployments.
-- Includes all extensions, enums, tables, columns, indexes, RLS policies,
-- security-definer RPC functions, auth triggers, and default settings.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. EXTENSIONS
-- ------------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "unaccent";

-- ------------------------------------------------------------------------------
-- 2. ENUMS
-- ------------------------------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE media_type_enum AS ENUM ('movie', 'series');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
  CREATE TYPE badge_type_enum AS ENUM ('recently_added', 'new_season', 'none');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
  CREATE TYPE processing_status_enum AS ENUM ('queued', 'processing', 'ready', 'failed');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

-- ------------------------------------------------------------------------------
-- 3. TABLES DEFINITIONS
-- ------------------------------------------------------------------------------

-- Profiles Table (Synced with auth.users)
CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  display_name TEXT,
  avatar_url TEXT DEFAULT 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150',
  created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
  first_joined_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
  last_login_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  last_logout_at TIMESTAMPTZ,
  is_signed_out BOOLEAN DEFAULT FALSE,
  total_site_seconds BIGINT DEFAULT 0,
  total_watch_seconds BIGINT DEFAULT 0,
  total_logins INTEGER DEFAULT 0,
  last_ip TEXT,
  last_ipv4 TEXT,
  last_ipv6 TEXT,
  last_country TEXT,
  last_city TEXT,
  last_device TEXT,
  last_browser TEXT,
  current_activity TEXT DEFAULT 'Offline',
  phone_number VARCHAR UNIQUE,
  phone_verified BOOLEAN DEFAULT FALSE,
  tier VARCHAR DEFAULT 'free',
  storage_used_bytes BIGINT DEFAULT 0,
  storage_limit_bytes BIGINT DEFAULT 5368709120,
  bandwidth_throttle_kbps INTEGER DEFAULT 1024,
  stripe_customer_id VARCHAR,
  stripe_subscription_id VARCHAR,
  auth_provider VARCHAR DEFAULT 'password',
  password_hash VARCHAR
);

-- User Login Logs (Dual-Stack IP & Session Duration Analytics)
CREATE TABLE IF NOT EXISTS public.user_login_logs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
  ip_address TEXT,
  ipv4_address TEXT,
  ipv6_address TEXT,
  country TEXT,
  city TEXT,
  user_agent TEXT,
  device_type TEXT,
  browser TEXT,
  os_name TEXT,
  session_site_seconds BIGINT DEFAULT 0,
  session_watch_seconds BIGINT DEFAULT 0,
  last_heartbeat_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()),
  logged_out_at TIMESTAMPTZ,
  logged_in_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Email Verification Rate Limiting & Cooldown Table
CREATE TABLE IF NOT EXISTS public.auth_email_rate_limits (
  email TEXT PRIMARY KEY,
  last_sent_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  send_count_window INTEGER NOT NULL DEFAULT 1,
  window_started_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  last_ip TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- System Settings
CREATE TABLE IF NOT EXISTS public.system_settings (
  id INTEGER PRIMARY KEY DEFAULT 1,
  transcoding_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  max_transcode_threads INTEGER NOT NULL DEFAULT 4,
  hw_accel_type TEXT DEFAULT 'auto',
  cpu_thread_limit TEXT DEFAULT 'auto',
  allocated_cpu_cores INTEGER DEFAULT 2,
  allocated_ram_mb INTEGER DEFAULT 2048,
  gpu_enabled BOOLEAN DEFAULT FALSE,
  active_server_url TEXT DEFAULT NULL,
  active_tunnel_url TEXT DEFAULT NULL,
  cloudflare_pages_url TEXT DEFAULT NULL,
  server_heartbeat_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

ALTER TABLE public.system_settings
  ADD COLUMN IF NOT EXISTS active_server_url TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS active_tunnel_url TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS cloudflare_pages_url TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS server_heartbeat_at TIMESTAMPTZ DEFAULT NOW();

-- Media Items (Movies and TV Series)
CREATE TABLE IF NOT EXISTS public.media_items (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tmdb_id INTEGER,
  imdb_id TEXT,
  type media_type_enum NOT NULL,
  title TEXT NOT NULL,
  slug TEXT NOT NULL,
  synopsis TEXT,
  release_year INTEGER,
  maturity_rating TEXT DEFAULT 'U/A 16+',
  runtime_minutes INTEGER,
  poster_url TEXT,
  backdrop_url TEXT,
  trailer_youtube_key TEXT,
  genres TEXT[] DEFAULT '{}'::text[],
  audio_languages TEXT[] DEFAULT ARRAY['English'::text],
  cast_members JSONB DEFAULT '[]'::jsonb,
  creators_directors JSONB DEFAULT '[]'::jsonb,
  critic_rating NUMERIC DEFAULT 0.0,
  views_count BIGINT NOT NULL DEFAULT 0,
  likes_count BIGINT NOT NULL DEFAULT 0,
  badge_type badge_type_enum NOT NULL DEFAULT 'recently_added',
  badge TEXT,
  badge_expires_at TIMESTAMPTZ,
  is_featured_billboard BOOLEAN DEFAULT FALSE,
  billboard_priority INTEGER DEFAULT 0,
  is_published BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- Seasons Table (For Series)
CREATE TABLE IF NOT EXISTS public.seasons (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  media_id UUID NOT NULL REFERENCES public.media_items(id) ON DELETE CASCADE,
  season_number INTEGER NOT NULL,
  title TEXT NOT NULL,
  episode_count INTEGER DEFAULT 0,
  CONSTRAINT uq_season_media UNIQUE (media_id, season_number)
);

-- Episodes Table (For Series)
CREATE TABLE IF NOT EXISTS public.episodes (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  media_id UUID NOT NULL REFERENCES public.media_items(id) ON DELETE CASCADE,
  season_id UUID NOT NULL REFERENCES public.seasons(id) ON DELETE CASCADE,
  season_number INTEGER NOT NULL,
  episode_number INTEGER NOT NULL,
  title TEXT NOT NULL,
  synopsis TEXT,
  duration_seconds INTEGER DEFAULT 0,
  still_url TEXT,
  is_published BOOLEAN NOT NULL DEFAULT FALSE,
  CONSTRAINT uq_episode_season UNIQUE (season_id, episode_number)
);

-- Media Streams (HLS Playlists, Audio Tracks, Subtitle Tracks)
CREATE TABLE IF NOT EXISTS public.media_streams (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  media_id UUID REFERENCES public.media_items(id) ON DELETE CASCADE,
  episode_id UUID REFERENCES public.episodes(id) ON DELETE CASCADE,
  master_playlist_path TEXT NOT NULL,
  is_transcoded BOOLEAN NOT NULL DEFAULT FALSE,
  available_qualities JSONB NOT NULL DEFAULT '["Original"]'::jsonb,
  audio_tracks JSONB NOT NULL DEFAULT '[]'::jsonb,
  subtitle_tracks JSONB NOT NULL DEFAULT '[]'::jsonb,
  file_size_bytes BIGINT DEFAULT 0,
  processing_status processing_status_enum NOT NULL DEFAULT 'queued',
  created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- Watch Progress (Single active progress row per Movie / Series Episode)
CREATE TABLE IF NOT EXISTS public.watch_progress (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  media_id UUID NOT NULL REFERENCES public.media_items(id) ON DELETE CASCADE,
  episode_id UUID REFERENCES public.episodes(id) ON DELETE CASCADE,
  season_number INTEGER,
  episode_number INTEGER,
  position_seconds DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  is_completed BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  preferred_audio_index INTEGER DEFAULT NULL,
  preferred_audio_lang TEXT DEFAULT NULL,
  preferred_audio_label TEXT DEFAULT NULL,
  preferred_subtitle_index INTEGER DEFAULT NULL,
  preferred_subtitle_lang TEXT DEFAULT NULL,
  preferred_subtitle_label TEXT DEFAULT NULL,
  CONSTRAINT uq_watch_progress_user_episode UNIQUE (user_id, episode_id)
);

-- Watch History (Historical playback records)
CREATE TABLE IF NOT EXISTS public.watch_history (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  media_id UUID NOT NULL REFERENCES public.media_items(id) ON DELETE CASCADE,
  episode_id UUID REFERENCES public.episodes(id) ON DELETE CASCADE,
  season_number INTEGER,
  episode_number INTEGER,
  position_seconds DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0.0,
  watched_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- User Likes
CREATE TABLE IF NOT EXISTS public.user_likes (
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  media_id UUID NOT NULL REFERENCES public.media_items(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  PRIMARY KEY (user_id, media_id)
);

-- Custom Lists ("My List")
CREATE TABLE IF NOT EXISTS public.custom_lists (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- Custom List Items
CREATE TABLE IF NOT EXISTS public.custom_list_items (
  list_id UUID NOT NULL REFERENCES public.custom_lists(id) ON DELETE CASCADE,
  media_id UUID NOT NULL REFERENCES public.media_items(id) ON DELETE CASCADE,
  added_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
  PRIMARY KEY (list_id, media_id)
);

-- ------------------------------------------------------------------------------
-- 4. PERFORMANCE & DEDUPLICATION INDEXES
-- ------------------------------------------------------------------------------

-- Media Items Indexes
CREATE INDEX IF NOT EXISTS idx_media_items_published ON public.media_items (is_published);
CREATE INDEX IF NOT EXISTS idx_media_items_type ON public.media_items (type) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_media_items_created_at ON public.media_items (created_at DESC) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_media_items_views_count ON public.media_items (views_count DESC) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_media_items_likes_count ON public.media_items (likes_count DESC) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_media_items_critic_rating ON public.media_items (critic_rating DESC) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_media_items_billboard ON public.media_items (is_featured_billboard, billboard_priority DESC) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_media_items_badge_expires ON public.media_items (badge_expires_at) WHERE badge_expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_media_items_genres_gin ON public.media_items USING GIN (genres) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_media_items_title_trgm ON public.media_items USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_media_items_synopsis_trgm ON public.media_items USING GIN (synopsis gin_trgm_ops) WHERE is_published = TRUE;

-- Seasons & Episodes Indexes
CREATE INDEX IF NOT EXISTS idx_seasons_media_id ON public.seasons (media_id, season_number);
CREATE INDEX IF NOT EXISTS idx_episodes_media_id ON public.episodes (media_id);
CREATE INDEX IF NOT EXISTS idx_episodes_season_id ON public.episodes (season_id, episode_number);

-- Media Streams Indexes
CREATE INDEX IF NOT EXISTS idx_media_streams_media ON public.media_streams (media_id);
CREATE INDEX IF NOT EXISTS idx_media_streams_episode ON public.media_streams (episode_id);

-- Profiles & Telemetry Indexes
CREATE INDEX IF NOT EXISTS idx_profiles_last_seen_at ON public.profiles (last_seen_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_profiles_total_watch ON public.profiles (total_watch_seconds DESC);
CREATE INDEX IF NOT EXISTS idx_user_login_logs_user_time ON public.user_login_logs (user_id, logged_in_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_login_logs_logged_in_at ON public.user_login_logs (logged_in_at DESC);

-- Watch Progress Deduplication & Lookup Indexes
CREATE INDEX IF NOT EXISTS idx_watch_progress_user ON public.watch_progress (user_id, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_progress_user_movie ON public.watch_progress (user_id, media_id) WHERE episode_id IS NULL;

-- Watch History Deduplication & Lookup Indexes
CREATE INDEX IF NOT EXISTS idx_watch_history_user ON public.watch_history (user_id, watched_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_history_user_episode ON public.watch_history (user_id, episode_id) WHERE episode_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_history_user_movie ON public.watch_history (user_id, media_id) WHERE episode_id IS NULL;

-- Custom Lists Indexes
CREATE INDEX IF NOT EXISTS idx_custom_lists_user ON public.custom_lists (user_id);
CREATE INDEX IF NOT EXISTS idx_custom_list_items_list ON public.custom_list_items (list_id);

-- ------------------------------------------------------------------------------
-- 5. ROW LEVEL SECURITY (RLS) POLICIES & PERMISSIONS
-- ------------------------------------------------------------------------------

-- Enable RLS on all tables
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_login_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_email_rate_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_email_rate_limits FORCE ROW LEVEL SECURITY;
ALTER TABLE public.system_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.system_settings FORCE ROW LEVEL SECURITY;
ALTER TABLE public.media_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.seasons ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.episodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.media_streams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.watch_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.watch_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_likes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.custom_lists ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.custom_list_items ENABLE ROW LEVEL SECURITY;

-- Grants to service_role (Full administrative backend access)
GRANT ALL ON TABLE public.profiles TO service_role;
GRANT ALL ON TABLE public.user_login_logs TO service_role;
GRANT ALL ON TABLE public.auth_email_rate_limits TO service_role;
GRANT ALL ON TABLE public.system_settings TO service_role;
GRANT ALL ON TABLE public.media_items TO service_role;
GRANT ALL ON TABLE public.seasons TO service_role;
GRANT ALL ON TABLE public.episodes TO service_role;
GRANT ALL ON TABLE public.media_streams TO service_role;
GRANT ALL ON TABLE public.watch_progress TO service_role;
GRANT ALL ON TABLE public.watch_history TO service_role;
GRANT ALL ON TABLE public.user_likes TO service_role;
GRANT ALL ON TABLE public.custom_lists TO service_role;
GRANT ALL ON TABLE public.custom_list_items TO service_role;

-- Grants to authenticated users (Restricted column update on profiles, no direct insert on login audit logs)
GRANT SELECT ON TABLE public.profiles TO authenticated;
GRANT UPDATE (display_name, avatar_url) ON TABLE public.profiles TO authenticated;
GRANT SELECT ON TABLE public.user_login_logs TO authenticated;
GRANT SELECT ON TABLE public.media_items TO authenticated;
GRANT SELECT ON TABLE public.seasons TO authenticated;
GRANT SELECT ON TABLE public.episodes TO authenticated;
GRANT SELECT ON TABLE public.media_streams TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.watch_progress TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.watch_history TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.user_likes TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.custom_lists TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.custom_list_items TO authenticated;

-- Grants to anon users (Metadata discovery)
GRANT SELECT ON TABLE public.media_items TO anon;

-- Profiles Policies
DROP POLICY IF EXISTS "Users can read own profile" ON public.profiles;
CREATE POLICY "Users can read own profile"
  ON public.profiles FOR SELECT
  TO authenticated
  USING (auth.uid() = id);

DROP POLICY IF EXISTS "profiles_update_own" ON public.profiles;
CREATE POLICY "profiles_update_own"
  ON public.profiles FOR UPDATE
  TO authenticated
  USING (auth.uid() = id)
  WITH CHECK (auth.uid() = id);

-- Ensure authenticated users cannot tamper with privileged profile columns
CREATE OR REPLACE FUNCTION public.protect_profile_system_columns()
RETURNS TRIGGER AS $$
BEGIN
  IF auth.role() = 'authenticated' THEN
    NEW.id := OLD.id;
    NEW.email := OLD.email;
    NEW.tier := OLD.tier;
    NEW.phone_verified := OLD.phone_verified;
    NEW.storage_limit_bytes := OLD.storage_limit_bytes;
    NEW.bandwidth_throttle_kbps := OLD.bandwidth_throttle_kbps;
    NEW.stripe_customer_id := OLD.stripe_customer_id;
    NEW.stripe_subscription_id := OLD.stripe_subscription_id;
    NEW.total_watch_seconds := OLD.total_watch_seconds;
    NEW.total_site_seconds := OLD.total_site_seconds;
    NEW.total_logins := OLD.total_logins;
    NEW.created_at := OLD.created_at;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS trg_protect_profile_system_columns ON public.profiles;
CREATE TRIGGER trg_protect_profile_system_columns
  BEFORE UPDATE ON public.profiles
  FOR EACH ROW
  EXECUTE FUNCTION public.protect_profile_system_columns();

-- User Login Logs Policies (Audit log: read-only for authenticated users, insert reserved for server-side service_role)
DROP POLICY IF EXISTS "user_login_logs_select_own" ON public.user_login_logs;
CREATE POLICY "user_login_logs_select_own"
  ON public.user_login_logs FOR SELECT
  TO authenticated
  USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_login_logs_insert_own" ON public.user_login_logs;
REVOKE INSERT ON TABLE public.user_login_logs FROM authenticated, anon;

-- Catalog Policies (Public read for published titles)
DROP POLICY IF EXISTS "media_items_select_published" ON public.media_items;
CREATE POLICY "media_items_select_published"
  ON public.media_items FOR SELECT
  TO authenticated, anon
  USING (is_published = TRUE);

DROP POLICY IF EXISTS "seasons_select_published" ON public.seasons;
CREATE POLICY "seasons_select_published"
  ON public.seasons FOR SELECT
  TO authenticated
  USING (EXISTS (
    SELECT 1 FROM public.media_items m
    WHERE m.id = seasons.media_id AND m.is_published = TRUE
  ));

DROP POLICY IF EXISTS "episodes_select_published" ON public.episodes;
CREATE POLICY "episodes_select_published"
  ON public.episodes FOR SELECT
  TO authenticated
  USING (
    is_published = TRUE AND EXISTS (
      SELECT 1 FROM public.media_items m
      WHERE m.id = episodes.media_id AND m.is_published = TRUE
    )
  );

DROP POLICY IF EXISTS "media_streams_select_ready" ON public.media_streams;
CREATE POLICY "media_streams_select_ready"
  ON public.media_streams FOR SELECT
  TO authenticated
  USING (
    processing_status = 'ready'::processing_status_enum
    AND (
      (media_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.media_items m
        WHERE m.id = media_streams.media_id AND m.is_published = TRUE
      ))
      OR
      (episode_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.episodes ep
        JOIN public.media_items m ON m.id = ep.media_id
        WHERE ep.id = media_streams.episode_id AND ep.is_published = TRUE AND m.is_published = TRUE
      ))
    )
  );

-- User Isolation Policies
DROP POLICY IF EXISTS "watch_progress_user_isolation" ON public.watch_progress;
CREATE POLICY "watch_progress_user_isolation"
  ON public.watch_progress FOR ALL
  TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "watch_history_user_isolation" ON public.watch_history;
CREATE POLICY "watch_history_user_isolation"
  ON public.watch_history FOR ALL
  TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_likes_user_isolation" ON public.user_likes;
CREATE POLICY "user_likes_user_isolation"
  ON public.user_likes FOR ALL
  TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "custom_lists_user_isolation" ON public.custom_lists;
CREATE POLICY "custom_lists_user_isolation"
  ON public.custom_lists FOR ALL
  TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "custom_list_items_user_isolation" ON public.custom_list_items;
CREATE POLICY "custom_list_items_user_isolation"
  ON public.custom_list_items FOR ALL
  TO authenticated
  USING (EXISTS (
    SELECT 1 FROM public.custom_lists cl
    WHERE cl.id = custom_list_items.list_id AND cl.user_id = auth.uid()
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.custom_lists cl
    WHERE cl.id = custom_list_items.list_id AND cl.user_id = auth.uid()
  ));

-- ------------------------------------------------------------------------------
-- 6. STORED PROCEDURES & RPC FUNCTIONS
-- ------------------------------------------------------------------------------

-- 6.1 Handle New Auth User Trigger Function
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
  INSERT INTO public.profiles (id, email, display_name, avatar_url, first_joined_at, created_at)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(NEW.raw_user_meta_data->>'full_name', split_part(NEW.email, '@', 1)),
    COALESCE(NEW.raw_user_meta_data->>'avatar_url', 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150'),
    COALESCE(NEW.created_at, NOW()),
    COALESCE(NEW.created_at, NOW())
  )
  ON CONFLICT (id) DO UPDATE
  SET email = EXCLUDED.email;

  INSERT INTO public.custom_lists (user_id, name)
  VALUES (NEW.id, 'My List')
  ON CONFLICT DO NOTHING;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- 6.2 Handle User Like Change Trigger Function
CREATE OR REPLACE FUNCTION public.handle_user_like_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    UPDATE public.media_items
    SET likes_count = likes_count + 1
    WHERE id = NEW.media_id;
    RETURN NEW;
  ELSIF TG_OP = 'DELETE' THEN
    UPDATE public.media_items
    SET likes_count = GREATEST(0, likes_count - 1)
    WHERE id = OLD.media_id;
    RETURN OLD;
  END IF;
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS on_user_like_toggle ON public.user_likes;
CREATE TRIGGER on_user_like_toggle
  AFTER INSERT OR DELETE ON public.user_likes
  FOR EACH ROW EXECUTE FUNCTION public.handle_user_like_change();

-- 6.3 Increment Views Count
CREATE OR REPLACE FUNCTION public.increment_views_count(target_media_id UUID)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
BEGIN
  IF auth.uid() IS NULL THEN
    RAISE EXCEPTION 'Not authenticated';
  END IF;

  UPDATE public.media_items
  SET views_count = views_count + 1
  WHERE id = target_media_id;
END;
$$;
GRANT EXECUTE ON FUNCTION public.increment_views_count(UUID) TO authenticated, service_role;

-- 6.4 Optimized Search RPC with Word-Boundary Trigram Matching
CREATE OR REPLACE FUNCTION public.search_media(search_query TEXT, max_results INT DEFAULT 20)
RETURNS TABLE (
  id UUID,
  title TEXT,
  slug TEXT,
  type media_type_enum,
  synopsis TEXT,
  release_year INT,
  maturity_rating TEXT,
  poster_url TEXT,
  backdrop_url TEXT,
  genres TEXT[],
  critic_rating NUMERIC,
  similarity_score REAL
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
  clean_query TEXT := trim(coalesce(search_query, ''));
  escaped_query TEXT;
BEGIN
  IF clean_query = '' THEN
    RETURN QUERY
    SELECT
      m.id,
      m.title,
      m.slug,
      m.type,
      m.synopsis,
      m.release_year,
      m.maturity_rating,
      m.poster_url,
      m.backdrop_url,
      m.genres,
      m.critic_rating,
      1.0::REAL AS similarity_score
    FROM public.media_items m
    WHERE m.is_published = true
    ORDER BY m.is_featured_billboard DESC, m.views_count DESC, m.created_at DESC
    LIMIT max_results;
    RETURN;
  END IF;

  escaped_query := regexp_replace(clean_query, '([!$()*+.:<=>?[\\\]^{|}-])', '\\\1', 'g');

  RETURN QUERY
  SELECT
    m.id,
    m.title,
    m.slug,
    m.type,
    m.synopsis,
    m.release_year,
    m.maturity_rating,
    m.poster_url,
    m.backdrop_url,
    m.genres,
    m.critic_rating,
    (
      CASE
        WHEN lower(m.title) = lower(clean_query) THEN 100.0
        WHEN lower(m.title) LIKE lower(clean_query) || '%' THEN 85.0
        WHEN m.title ~* ('\m' || escaped_query) THEN 70.0
        WHEN EXISTS (
          SELECT 1 FROM unnest(m.genres) g WHERE g ILIKE clean_query || '%'
        ) OR CAST(m.release_year AS text) = clean_query THEN 35.0
        WHEN char_length(clean_query) >= 4 AND m.synopsis ~* ('\m' || escaped_query || '\M') THEN 15.0
        ELSE 5.0
      END + (similarity(m.title, clean_query) * 10.0)
    )::REAL AS similarity_score
  FROM public.media_items m
  WHERE m.is_published = true
    AND (
      m.title ILIKE clean_query || '%'
      OR m.title ~* ('\m' || escaped_query)
      OR EXISTS (
        SELECT 1 FROM unnest(m.genres) g WHERE g ILIKE clean_query || '%'
      )
      OR CAST(m.release_year AS text) = clean_query
      OR (char_length(clean_query) >= 4 AND m.synopsis ~* ('\m' || escaped_query || '\M'))
    )
  ORDER BY
    (CASE WHEN m.title ILIKE clean_query || '%' OR m.title ~* ('\m' || escaped_query) THEN 1 ELSE 0 END) DESC,
    similarity_score DESC,
    m.views_count DESC
  LIMIT max_results;
END;
$$;
GRANT EXECUTE ON FUNCTION public.search_media(TEXT, INT) TO anon, authenticated, service_role;

-- 6.5 Admin Inspect Email Auth State (Restricted to service_role)
CREATE OR REPLACE FUNCTION public.admin_inspect_email_auth_state(p_email TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth
AS $$
DECLARE
  v_clean_email TEXT := lower(trim(COALESCE(p_email, '')));
  v_user_id UUID;
  v_confirmed_at TIMESTAMPTZ;
  v_confirmation_sent_at TIMESTAMPTZ;
  v_created_at TIMESTAMPTZ;
  v_rl_last_sent TIMESTAMPTZ;
  v_rl_count INTEGER;
  v_rl_window TIMESTAMPTZ;
  v_effective_last_sent TIMESTAMPTZ;
BEGIN
  IF v_clean_email = '' THEN
    RETURN jsonb_build_object('exists', false);
  END IF;

  SELECT u.id, u.email_confirmed_at, u.confirmation_sent_at, u.created_at
  INTO v_user_id, v_confirmed_at, v_confirmation_sent_at, v_created_at
  FROM auth.users u
  WHERE lower(u.email) = v_clean_email
  LIMIT 1;

  SELECT rl.last_sent_at, rl.send_count_window, rl.window_started_at
  INTO v_rl_last_sent, v_rl_count, v_rl_window
  FROM public.auth_email_rate_limits rl
  WHERE rl.email = v_clean_email;

  v_effective_last_sent := GREATEST(COALESCE(v_confirmation_sent_at, '-infinity'::timestamptz), COALESCE(v_rl_last_sent, '-infinity'::timestamptz));
  IF v_effective_last_sent = '-infinity'::timestamptz THEN
    v_effective_last_sent := NULL;
  END IF;

  IF v_user_id IS NULL THEN
    RETURN jsonb_build_object(
      'exists', false,
      'is_verified', false,
      'last_sent_at', v_rl_last_sent,
      'send_count_window', COALESCE(v_rl_count, 0),
      'window_started_at', v_rl_window
    );
  END IF;

  RETURN jsonb_build_object(
    'exists', true,
    'user_id', v_user_id,
    'is_verified', (v_confirmed_at IS NOT NULL),
    'email_confirmed_at', v_confirmed_at,
    'confirmation_sent_at', v_effective_last_sent,
    'created_at', v_created_at,
    'send_count_window', COALESCE(v_rl_count, 0),
    'window_started_at', v_rl_window
  );
END;
$$;
REVOKE ALL ON FUNCTION public.admin_inspect_email_auth_state(TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_inspect_email_auth_state(TEXT) TO service_role;

-- 6.6 User Enumeration Protection RPCs (Restricted to service_role)
CREATE OR REPLACE FUNCTION public.check_user_email_exists(user_email TEXT)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
BEGIN
  RETURN EXISTS (
    SELECT 1 FROM auth.users
    WHERE lower(email) = lower(trim(user_email))
  );
END;
$$;
REVOKE ALL ON FUNCTION public.check_user_email_exists(TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.check_user_email_exists(TEXT) TO service_role;

CREATE OR REPLACE FUNCTION public.get_user_auth_status(user_email TEXT)
RETURNS TABLE(user_exists boolean, email_confirmed boolean, user_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    true AS user_exists,
    (u.email_confirmed_at IS NOT NULL) AS email_confirmed,
    u.id AS user_id
  FROM auth.users u
  WHERE lower(u.email) = lower(trim(user_email))
  LIMIT 1;

  IF NOT FOUND THEN
    RETURN QUERY SELECT false AS user_exists, false AS email_confirmed, NULL::uuid AS user_id;
  END IF;
END;
$$;
REVOKE ALL ON FUNCTION public.get_user_auth_status(TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_user_auth_status(TEXT) TO service_role;

-- 6.7 Platform Telemetry KPIs RPC (Restricted to service_role)
CREATE OR REPLACE FUNCTION public.get_platform_telemetry_kpis()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
  v_total_users BIGINT;
  v_online_now BIGINT;
  v_watching_now BIGINT;
  v_total_watch BIGINT;
  v_total_site BIGINT;
BEGIN
  SELECT COUNT(*),
         COUNT(*) FILTER (WHERE is_signed_out IS NOT TRUE AND last_seen_at >= NOW() - INTERVAL '90 seconds'),
         COUNT(*) FILTER (WHERE is_signed_out IS NOT TRUE AND last_seen_at >= NOW() - INTERVAL '90 seconds' AND current_activity LIKE 'Watching:%'),
         COALESCE(SUM(total_watch_seconds), 0),
         COALESCE(SUM(total_site_seconds), 0)
  INTO v_total_users, v_online_now, v_watching_now, v_total_watch, v_total_site
  FROM public.profiles;

  IF v_total_site < v_total_watch THEN
    v_total_site := v_total_watch;
  END IF;

  RETURN jsonb_build_object(
    'total_registered_users', v_total_users,
    'online_now_count', v_online_now,
    'watching_now_count', v_watching_now,
    'total_platform_watch_seconds', v_total_watch,
    'total_platform_site_seconds', v_total_site
  );
END;
$$;
REVOKE ALL ON FUNCTION public.get_platform_telemetry_kpis() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_platform_telemetry_kpis() TO service_role;

-- 6.8 User Telemetry Heartbeat (Hardened against spoofing)
CREATE OR REPLACE FUNCTION public.record_user_telemetry_heartbeat(
  p_user_id uuid,
  p_session_log_id uuid DEFAULT NULL::uuid,
  p_delta_site_seconds integer DEFAULT 0,
  p_delta_watch_seconds integer DEFAULT 0,
  p_ipv4 text DEFAULT NULL::text,
  p_ipv6 text DEFAULT NULL::text,
  p_current_activity text DEFAULT 'Online — Browsing'::text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'auth'
AS $$
DECLARE
  v_safe_site INTEGER := GREATEST(0, LEAST(COALESCE(p_delta_site_seconds, 0), 65));
  v_safe_watch INTEGER := GREATEST(0, LEAST(COALESCE(p_delta_watch_seconds, 0), 65));
  v_target_session_id UUID := p_session_log_id;
  v_caller_role TEXT := COALESCE(auth.role(), (current_setting('request.jwt.claims', true)::jsonb ->> 'role'), '');
BEGIN
  -- Enforce caller authorization: only service_role (backend) or the authenticated user themselves
  IF v_caller_role <> 'service_role' AND (auth.uid() IS NULL OR auth.uid() <> p_user_id) THEN
    RAISE EXCEPTION 'Unauthorized telemetry heartbeat update';
  END IF;

  -- Self-healing: Guarantee profile row exists in public.profiles
  IF NOT EXISTS (SELECT 1 FROM public.profiles WHERE id = p_user_id) THEN
    INSERT INTO public.profiles (id, email, display_name, avatar_url, first_joined_at, created_at)
    SELECT
      u.id,
      u.email,
      COALESCE(u.raw_user_meta_data->>'full_name', split_part(u.email, '@', 1)),
      COALESCE(u.raw_user_meta_data->>'avatar_url', 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150'),
      COALESCE(u.created_at, NOW()),
      COALESCE(u.created_at, NOW())
    FROM auth.users u
    WHERE u.id = p_user_id
    ON CONFLICT (id) DO UPDATE
    SET email = EXCLUDED.email;
  END IF;

  UPDATE public.profiles
  SET
    last_seen_at = NOW(),
    is_signed_out = FALSE,
    total_site_seconds = COALESCE(total_site_seconds, 0) + v_safe_site,
    total_watch_seconds = COALESCE(total_watch_seconds, 0) + v_safe_watch,
    last_ipv4 = COALESCE(NULLIF(p_ipv4, ''), last_ipv4),
    last_ipv6 = COALESCE(NULLIF(p_ipv6, ''), last_ipv6),
    current_activity = COALESCE(p_current_activity, 'Online — Browsing')
  WHERE id = p_user_id;

  IF v_target_session_id IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.user_login_logs WHERE id = v_target_session_id AND user_id = p_user_id) THEN
      v_target_session_id := NULL;
    END IF;
  END IF;

  IF v_target_session_id IS NULL THEN
    SELECT id INTO v_target_session_id
    FROM public.user_login_logs
    WHERE user_id = p_user_id
    ORDER BY logged_in_at DESC
    LIMIT 1;
  END IF;

  IF v_target_session_id IS NOT NULL THEN
    UPDATE public.user_login_logs
    SET
      last_heartbeat_at = NOW(),
      logged_out_at = NULL,
      session_site_seconds = COALESCE(session_site_seconds, 0) + v_safe_site,
      session_watch_seconds = COALESCE(session_watch_seconds, 0) + v_safe_watch,
      ipv4_address = COALESCE(NULLIF(p_ipv4, ''), ipv4_address),
      ipv6_address = COALESCE(NULLIF(p_ipv6, ''), ipv6_address)
    WHERE id = v_target_session_id;
  ELSE
    INSERT INTO public.user_login_logs (
      user_id, ip_address, ipv4_address, ipv6_address, country, city,
      user_agent, device_type, browser, os_name,
      session_site_seconds, session_watch_seconds, last_heartbeat_at, logged_in_at
    ) VALUES (
      p_user_id,
      COALESCE(NULLIF(p_ipv4, ''), '127.0.0.1'),
      NULLIF(p_ipv4, ''),
      NULLIF(p_ipv6, ''),
      'Local Network',
      'Localhost',
      'Browser Web Client',
      'Desktop',
      'Chrome',
      'Linux',
      v_safe_site,
      v_safe_watch,
      NOW(),
      NOW()
    );
  END IF;
END;
$$;
REVOKE ALL ON FUNCTION public.record_user_telemetry_heartbeat(uuid, uuid, integer, integer, text, text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.record_user_telemetry_heartbeat(uuid, uuid, integer, integer, text, text, text) TO authenticated, service_role;

-- 6.9 Sync Watch Progress RPC (With Self-Healing Profiles & Telemetry Integration)
DROP FUNCTION IF EXISTS public.sync_watch_progress(uuid, uuid, integer, integer, double precision, double precision);

CREATE OR REPLACE FUNCTION public.sync_watch_progress(
  p_media_id uuid,
  p_episode_id uuid,
  p_season_number integer,
  p_episode_number integer,
  p_position_seconds double precision,
  p_duration_seconds double precision,
  p_audio_index integer DEFAULT NULL,
  p_audio_lang text DEFAULT NULL,
  p_audio_label text DEFAULT NULL,
  p_subtitle_index integer DEFAULT NULL,
  p_subtitle_lang text DEFAULT NULL,
  p_subtitle_label text DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'auth'
AS $$
DECLARE
  v_user_id UUID := auth.uid();
  v_is_completed BOOLEAN;
  v_season_num INT := p_season_number;
  v_ep_num INT := p_episode_number;
  v_title TEXT := '';
  v_media_title TEXT := '';
  v_ep_title TEXT := '';
  v_last_seen TIMESTAMPTZ;
  v_latest_session_id UUID;
  v_should_increment BOOLEAN := FALSE;
  v_audio_idx INT;
  v_audio_lang TEXT;
  v_audio_label TEXT;
  v_sub_idx INT;
  v_sub_lang TEXT;
  v_sub_label TEXT;
BEGIN
  IF v_user_id IS NULL THEN
    RAISE EXCEPTION 'Not authenticated';
  END IF;

  -- Security Check: Verify media item is published and exists
  IF NOT EXISTS (
    SELECT 1 FROM public.media_items
    WHERE id = p_media_id AND is_published = TRUE
  ) THEN
    RAISE EXCEPTION 'Media item not published or does not exist';
  END IF;

  -- Security Check: If episode_id is provided, verify it is published and belongs to p_media_id
  IF p_episode_id IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.episodes
      WHERE id = p_episode_id
        AND media_id = p_media_id
        AND is_published = TRUE
    ) THEN
      RAISE EXCEPTION 'Episode not published or media mismatch';
    END IF;
  END IF;

  -- Self-Healing: Guarantee profile row exists in public.profiles
  IF NOT EXISTS (SELECT 1 FROM public.profiles WHERE id = v_user_id) THEN
    INSERT INTO public.profiles (id, email, display_name, avatar_url, first_joined_at, created_at)
    SELECT
      u.id,
      u.email,
      COALESCE(u.raw_user_meta_data->>'full_name', split_part(u.email, '@', 1)),
      COALESCE(u.raw_user_meta_data->>'avatar_url', 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150'),
      COALESCE(u.created_at, NOW()),
      COALESCE(u.created_at, NOW())
    FROM auth.users u
    WHERE u.id = v_user_id
    ON CONFLICT (id) DO UPDATE
    SET email = EXCLUDED.email;
  END IF;

  IF p_episode_id IS NOT NULL AND (v_season_num IS NULL OR v_ep_num IS NULL) THEN
    SELECT season_number, episode_number INTO v_season_num, v_ep_num
    FROM public.episodes
    WHERE id = p_episode_id;
  END IF;

  v_is_completed := (p_duration_seconds > 0) AND (p_position_seconds >= (p_duration_seconds - 30.0) OR (p_position_seconds / p_duration_seconds) >= 0.95);

  -- Determine track preferences based on completion status
  IF v_is_completed THEN
    v_audio_idx := NULL;
    v_audio_lang := NULL;
    v_audio_label := NULL;
    v_sub_idx := NULL;
    v_sub_lang := NULL;
    v_sub_label := NULL;
  ELSE
    v_audio_idx := p_audio_index;
    v_audio_lang := p_audio_lang;
    v_audio_label := p_audio_label;
    v_sub_idx := p_subtitle_index;
    v_sub_lang := p_subtitle_lang;
    v_sub_label := p_subtitle_label;
  END IF;

  -- 1. Upsert Watch Progress
  IF p_episode_id IS NOT NULL THEN
    INSERT INTO public.watch_progress (
      user_id, media_id, episode_id, season_number, episode_number,
      position_seconds, duration_seconds, is_completed, updated_at,
      preferred_audio_index, preferred_audio_lang, preferred_audio_label,
      preferred_subtitle_index, preferred_subtitle_lang, preferred_subtitle_label
    )
    VALUES (
      v_user_id, p_media_id, p_episode_id, v_season_num, v_ep_num,
      p_position_seconds, p_duration_seconds, v_is_completed, timezone('utc'::text, now()),
      v_audio_idx, v_audio_lang, v_audio_label,
      v_sub_idx, v_sub_lang, v_sub_label
    )
    ON CONFLICT (user_id, episode_id) DO UPDATE
    SET
      media_id = EXCLUDED.media_id,
      season_number = EXCLUDED.season_number,
      episode_number = EXCLUDED.episode_number,
      position_seconds = EXCLUDED.position_seconds,
      duration_seconds = EXCLUDED.duration_seconds,
      is_completed = EXCLUDED.is_completed,
      updated_at = EXCLUDED.updated_at,
      preferred_audio_index = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_audio_idx, public.watch_progress.preferred_audio_index) END,
      preferred_audio_lang = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_audio_lang, public.watch_progress.preferred_audio_lang) END,
      preferred_audio_label = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_audio_label, public.watch_progress.preferred_audio_label) END,
      preferred_subtitle_index = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_sub_idx, public.watch_progress.preferred_subtitle_index) END,
      preferred_subtitle_lang = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_sub_lang, public.watch_progress.preferred_subtitle_lang) END,
      preferred_subtitle_label = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_sub_label, public.watch_progress.preferred_subtitle_label) END;
  ELSE
    INSERT INTO public.watch_progress (
      user_id, media_id, episode_id, season_number, episode_number,
      position_seconds, duration_seconds, is_completed, updated_at,
      preferred_audio_index, preferred_audio_lang, preferred_audio_label,
      preferred_subtitle_index, preferred_subtitle_lang, preferred_subtitle_label
    )
    VALUES (
      v_user_id, p_media_id, NULL, NULL, NULL,
      p_position_seconds, p_duration_seconds, v_is_completed, timezone('utc'::text, now()),
      v_audio_idx, v_audio_lang, v_audio_label,
      v_sub_idx, v_sub_lang, v_sub_label
    )
    ON CONFLICT (user_id, media_id) WHERE episode_id IS NULL DO UPDATE
    SET
      position_seconds = EXCLUDED.position_seconds,
      duration_seconds = EXCLUDED.duration_seconds,
      is_completed = EXCLUDED.is_completed,
      updated_at = EXCLUDED.updated_at,
      preferred_audio_index = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_audio_idx, public.watch_progress.preferred_audio_index) END,
      preferred_audio_lang = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_audio_lang, public.watch_progress.preferred_audio_lang) END,
      preferred_audio_label = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_audio_label, public.watch_progress.preferred_audio_label) END,
      preferred_subtitle_index = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_sub_idx, public.watch_progress.preferred_subtitle_index) END,
      preferred_subtitle_lang = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_sub_lang, public.watch_progress.preferred_subtitle_lang) END,
      preferred_subtitle_label = CASE WHEN v_is_completed THEN NULL ELSE COALESCE(v_sub_label, public.watch_progress.preferred_subtitle_label) END;
  END IF;

  -- 2. Upsert Watch History
  IF p_episode_id IS NOT NULL THEN
    INSERT INTO public.watch_history (
      user_id, media_id, episode_id, season_number, episode_number,
      position_seconds, duration_seconds, watched_at
    )
    VALUES (
      v_user_id, p_media_id, p_episode_id, v_season_num, v_ep_num,
      p_position_seconds, p_duration_seconds, timezone('utc'::text, now())
    )
    ON CONFLICT (user_id, episode_id) WHERE episode_id IS NOT NULL DO UPDATE
    SET
      season_number = EXCLUDED.season_number,
      episode_number = EXCLUDED.episode_number,
      position_seconds = EXCLUDED.position_seconds,
      duration_seconds = EXCLUDED.duration_seconds,
      watched_at = EXCLUDED.watched_at;
  ELSE
    INSERT INTO public.watch_history (
      user_id, media_id, episode_id, season_number, episode_number,
      position_seconds, duration_seconds, watched_at
    )
    VALUES (
      v_user_id, p_media_id, NULL, NULL, NULL,
      p_position_seconds, p_duration_seconds, timezone('utc'::text, now())
    )
    ON CONFLICT (user_id, media_id) WHERE episode_id IS NULL DO UPDATE
    SET
      position_seconds = EXCLUDED.position_seconds,
      duration_seconds = EXCLUDED.duration_seconds,
      watched_at = EXCLUDED.watched_at;
  END IF;

  -- 3. Telemetry Integration
  SELECT title INTO v_media_title FROM public.media_items WHERE id = p_media_id;
  IF p_episode_id IS NOT NULL THEN
    SELECT title INTO v_ep_title FROM public.episodes WHERE id = p_episode_id;
    v_title := COALESCE(v_media_title, 'Series') || ' - S' || COALESCE(v_season_num, 1) || 'E' || COALESCE(v_ep_num, 1) || ': ' || COALESCE(v_ep_title, 'Episode');
  ELSE
    v_title := COALESCE(v_media_title, 'Movie');
  END IF;

  SELECT last_seen_at INTO v_last_seen FROM public.profiles WHERE id = v_user_id;
  IF v_last_seen IS NULL OR v_last_seen < NOW() - INTERVAL '4 seconds' THEN
    v_should_increment := TRUE;
  END IF;

  IF v_should_increment THEN
    UPDATE public.profiles
    SET
      last_seen_at = NOW(),
      is_signed_out = FALSE,
      current_activity = 'Watching: ' || v_title,
      total_watch_seconds = COALESCE(total_watch_seconds, 0) + 5,
      total_site_seconds = COALESCE(total_site_seconds, 0) + 5
    WHERE id = v_user_id;
  ELSE
    UPDATE public.profiles
    SET
      last_seen_at = NOW(),
      is_signed_out = FALSE,
      current_activity = 'Watching: ' || v_title
    WHERE id = v_user_id;
  END IF;

  SELECT id INTO v_latest_session_id
  FROM public.user_login_logs
  WHERE user_id = v_user_id
  ORDER BY logged_in_at DESC
  LIMIT 1;

  IF v_latest_session_id IS NOT NULL THEN
    IF v_should_increment THEN
      UPDATE public.user_login_logs
      SET
        last_heartbeat_at = NOW(),
        logged_out_at = NULL,
        session_watch_seconds = COALESCE(session_watch_seconds, 0) + 5,
        session_site_seconds = COALESCE(session_site_seconds, 0) + 5
      WHERE id = v_latest_session_id;
    ELSE
      UPDATE public.user_login_logs
      SET
        last_heartbeat_at = NOW(),
        logged_out_at = NULL
      WHERE id = v_latest_session_id;
    END IF;
  ELSE
    INSERT INTO public.user_login_logs (
      user_id, ip_address, country, city, user_agent, device_type, browser, os_name,
      session_site_seconds, session_watch_seconds, last_heartbeat_at, logged_in_at
    ) VALUES (
      v_user_id, '127.0.0.1', 'Local Network', 'Localhost', 'Browser Web Client', 'Desktop', 'Chrome', 'Linux',
      5, 5, NOW(), NOW()
    );
  END IF;

END;
$$;
GRANT EXECUTE ON FUNCTION public.sync_watch_progress(uuid, uuid, integer, integer, double precision, double precision, integer, text, text, integer, text, text) TO authenticated, service_role;

-- 6.10 Get Home Feed Aggregated RPC (High Performance Netflix Home Rails)
CREATE OR REPLACE FUNCTION public.get_home_feed(current_user_id uuid DEFAULT NULL::uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $function$
DECLARE
  result JSONB;
  effective_user_id UUID;
  v_caller_role TEXT;
BEGIN
  v_caller_role := COALESCE(auth.role(), (current_setting('request.jwt.claims', true)::jsonb ->> 'role'), '');
  IF v_caller_role = 'service_role' THEN
    effective_user_id := current_user_id;
  ELSE
    effective_user_id := auth.uid();
  END IF;

  SELECT jsonb_build_object(
    'continue_watching', CASE WHEN effective_user_id IS NULL THEN '[]'::jsonb ELSE COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object(
          'progress_id', wp.id,
          'media_id', m.id,
          'episode_id', wp.episode_id,
          'title', m.title,
          'type', m.type,
          'poster_url', m.poster_url,
          'backdrop_url', m.backdrop_url,
          'trailer_youtube_key', m.trailer_youtube_key,
          'season_number', wp.season_number,
          'episode_number', wp.episode_number,
          'episode_title', ep.title,
          'episode_synopsis', ep.synopsis,
          'episode_still_url', ep.still_url,
          'episode_duration_seconds', ep.duration_seconds,
          'position_seconds', wp.position_seconds,
          'duration_seconds', wp.duration_seconds,
          'progress_pct', CASE WHEN wp.duration_seconds > 0 THEN ROUND(((wp.position_seconds / wp.duration_seconds) * 100)::numeric, 1) ELSE 0 END,
          'updated_at', wp.updated_at,
          'badge', CASE WHEN m.badge_expires_at IS NOT NULL AND m.badge_expires_at <= NOW() THEN NULL ELSE m.badge END,
          'badge_expires_at', m.badge_expires_at
        ) ORDER BY wp.updated_at DESC
      )
      FROM (
        SELECT DISTINCT ON (wp.media_id) wp.*
        FROM public.watch_progress wp
        WHERE wp.user_id = effective_user_id
          AND wp.is_completed = false
          AND wp.position_seconds > 5.0
        ORDER BY wp.media_id, wp.updated_at DESC
      ) wp
      JOIN public.media_items m ON m.id = wp.media_id
      LEFT JOIN public.episodes ep ON (ep.id = wp.episode_id AND ep.media_id = m.id AND ep.is_published = true)
      WHERE m.is_published = true
        AND (wp.episode_id IS NULL OR (ep.id IS NOT NULL AND ep.is_published = true))
      LIMIT 15
    ), '[]'::jsonb) END,

    'recently_added', COALESCE((
      SELECT jsonb_agg(row_to_json(m))
      FROM (
        SELECT id, title, slug, type, poster_url, backdrop_url, trailer_youtube_key, release_year, maturity_rating, genres, critic_rating, badge_type,
               CASE WHEN badge_expires_at IS NOT NULL AND badge_expires_at <= NOW() THEN NULL ELSE badge END AS badge,
               badge_expires_at
        FROM public.media_items
        WHERE is_published = true
        ORDER BY created_at DESC
        LIMIT 15
      ) m
    ), '[]'::jsonb),

    'most_watched', COALESCE((
      SELECT jsonb_agg(row_to_json(m))
      FROM (
        SELECT id, title, slug, type, poster_url, backdrop_url, trailer_youtube_key, release_year, maturity_rating, genres, critic_rating, views_count, badge_type,
               CASE WHEN badge_expires_at IS NOT NULL AND badge_expires_at <= NOW() THEN NULL ELSE badge END AS badge,
               badge_expires_at
        FROM public.media_items
        WHERE is_published = true
        ORDER BY views_count DESC, created_at DESC
        LIMIT 10
      ) m
    ), '[]'::jsonb),

    'most_liked', COALESCE((
      SELECT jsonb_agg(row_to_json(m))
      FROM (
        SELECT id, title, slug, type, poster_url, backdrop_url, trailer_youtube_key, release_year, maturity_rating, genres, critic_rating, likes_count, badge_type,
               CASE WHEN badge_expires_at IS NOT NULL AND badge_expires_at <= NOW() THEN NULL ELSE badge END AS badge,
               badge_expires_at
        FROM public.media_items
        WHERE is_published = true
        ORDER BY likes_count DESC, views_count DESC
        LIMIT 15
      ) m
    ), '[]'::jsonb),

    'critics_picks', COALESCE((
      SELECT jsonb_agg(row_to_json(m))
      FROM (
        SELECT id, title, slug, type, poster_url, backdrop_url, trailer_youtube_key, release_year, maturity_rating, genres, critic_rating, badge_type,
               CASE WHEN badge_expires_at IS NOT NULL AND badge_expires_at <= NOW() THEN NULL ELSE badge END AS badge,
               badge_expires_at
        FROM public.media_items
        WHERE is_published = true
          AND critic_rating >= 8.5
        ORDER BY critic_rating DESC
        LIMIT 15
      ) m
    ), '[]'::jsonb)
  ) INTO result;

  RETURN result;
END;
$function$;
GRANT EXECUTE ON FUNCTION public.get_home_feed(UUID) TO anon, authenticated, service_role;

-- 6.13 Server-Agnostic Dynamic Discovery Endpoint RPC
CREATE OR REPLACE FUNCTION public.get_active_server_endpoint()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_row RECORD;
BEGIN
  SELECT active_server_url, active_tunnel_url, cloudflare_pages_url, server_heartbeat_at
  INTO v_row
  FROM public.system_settings
  WHERE id = 1
  LIMIT 1;

  RETURN jsonb_build_object(
    'active_server_url', v_row.active_server_url,
    'active_tunnel_url', v_row.active_tunnel_url,
    'cloudflare_pages_url', v_row.cloudflare_pages_url,
    'server_heartbeat_at', v_row.server_heartbeat_at
  );
END;
$$;
GRANT EXECUTE ON FUNCTION public.get_active_server_endpoint() TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 7. DEFAULT SEED DATA
-- ------------------------------------------------------------------------------
INSERT INTO public.system_settings (
  id, transcoding_enabled, max_transcode_threads, hw_accel_type,
  cpu_thread_limit, allocated_cpu_cores, allocated_ram_mb, gpu_enabled
)
VALUES (1, FALSE, 4, 'auto', 'auto', 2, 2048, FALSE)
ON CONFLICT (id) DO NOTHING;

NOTIFY pgrst, 'reload schema';

