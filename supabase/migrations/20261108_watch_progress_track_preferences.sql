-- ==============================================================================
-- STREAMVAULT MIGRATION: PERSIST AUDIO & SUBTITLE PREFERENCES PER TITLE
-- ==============================================================================

-- 1. Add Track Preference Columns to public.watch_progress
ALTER TABLE public.watch_progress
  ADD COLUMN IF NOT EXISTS preferred_audio_index INTEGER DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS preferred_audio_lang TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS preferred_audio_label TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS preferred_subtitle_index INTEGER DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS preferred_subtitle_lang TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS preferred_subtitle_label TEXT DEFAULT NULL;

-- 2. Drop existing 6-parameter sync_watch_progress to prevent parameter overload ambiguity
DROP FUNCTION IF EXISTS public.sync_watch_progress(uuid, uuid, integer, integer, double precision, double precision);

-- 3. Create upgraded sync_watch_progress with track preference parameters
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

NOTIFY pgrst, 'reload schema';

