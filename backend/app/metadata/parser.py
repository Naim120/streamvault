import re
from pathlib import Path
from typing import Any, Dict, Optional
from ..database import db

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}

def parse_video_filename(filename: str) -> Dict[str, Any]:
    """
    Parses standard movie and TV show filenames into title, year, season, episode, and TMDB ID.
    Examples:
      - 'Inception (2010).mp4' -> title='Inception', year=2010, type='movie'
      - 'The.Dark.Knight.2008.1080p.mkv' -> title='The Dark Knight', year=2008, type='movie'
      - 'Stranger Things S01E01.mkv' -> title='Stranger Things', type='series', s=1, ep=1
      - 'Breaking Bad - S02E04 - Down.mp4' -> title='Breaking Bad', type='series', s=2, ep=4
      - 'Fight Club [tmdb-550].mkv' -> title='Fight Club', tmdb_id=550, type='movie'
    """
    base = Path(filename).stem

    # 1. Check for explicit TMDB ID e.g. [tmdb-155] or [tmdbid-155]
    tmdb_match = re.search(r'\[(?:tmdb|tmdbid)[-_]?(\d+)\]', base, re.IGNORECASE)
    explicit_tmdb_id = int(tmdb_match.group(1)) if tmdb_match else None
    cleaned = re.sub(r'\[(?:tmdb|tmdbid)[-_]?\d+\]', '', base, flags=re.IGNORECASE)

    # 2. Check for Season and Episode: S01E02, s1e2, 1x02, etc.
    s_ep_match = re.search(r'[Ss](\d{1,2})[._\s\-]*[Ee](\d{1,2})', cleaned)
    if not s_ep_match:
        s_ep_match = re.search(r'(\d{1,2})[xX](\d{1,2})', cleaned)

    season_number = None
    episode_number = None
    media_type = "movie"

    if s_ep_match:
        season_number = int(s_ep_match.group(1))
        episode_number = int(s_ep_match.group(2))
        media_type = "series"
        raw_title = cleaned[:s_ep_match.start()]
    else:
        raw_title = cleaned

    # 3. Check for 4-digit Year: (2014) or .2014. or - 2014
    year_match = re.search(r'[\(\[\.\s_\-](19\d\d|20\d\d)[\)\]\.\s_\-]?', raw_title)
    year = None
    if year_match:
        year = int(year_match.group(1))
        raw_title = raw_title[:year_match.start()]

    # 4. Clean common release scene keywords
    junk_patterns = [
        r'\b(1080p|720p|480p|2160p|4k|uhd)\b',
        r'\b(bluray|blu-ray|bdrip|brrip|web-dl|webrip|web|hdtv|dvdrip|remux)\b',
        r'\b(x264|x265|h264|h265|hevc|avc|10bit|hdr|dts|aac|ac3|mp3|5\.1)\b',
        r'\b(yify|rarbg|yts|eztv|galaxytv|sparks|ntg|flux)\b'
    ]
    title_clean = raw_title
    for pat in junk_patterns:
        title_clean = re.sub(pat, ' ', title_clean, flags=re.IGNORECASE)

    # Replace dots, underscores, dashes with space
    title_clean = re.sub(r'[\._\-]+', ' ', title_clean)
    title_clean = re.sub(r'[\[\]\(\)]+', '', title_clean)
    title_clean = re.sub(r'\s+', ' ', title_clean).strip()

    return {
        "raw_filename": filename,
        "clean_title": title_clean,
        "year": year,
        "media_type": media_type,
        "season_number": season_number,
        "episode_number": episode_number,
        "tmdb_id": explicit_tmdb_id
    }

async def find_matching_catalog_media(parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Tries to match a parsed filename against existing Supabase catalog items first.
    """
    try:
        # Match by explicit TMDB ID if present
        if parsed.get("tmdb_id"):
            items = await db.select("media_items", {"tmdb_id": f"eq.{parsed['tmdb_id']}"})
            if items and len(items) > 0:
                return items[0]

        title = parsed.get("clean_title", "")
        if not title:
            return None

        # Exact title match (case-insensitive ILIKE)
        items = await db.select("media_items", {"title": f"ilike.{title}"})
        if items and len(items) > 0:
            if len(items) == 1:
                return items[0]
            # If multiple, check year
            if parsed.get("year"):
                for it in items:
                    if it.get("release_year") == parsed["year"]:
                        return it
            return items[0]

        # Partial / normalized alphanumeric & slug match
        all_items = await db.select("media_items", {"select": "id,title,type,release_year,tmdb_id,slug,is_published"})
        parsed_norm = re.sub(r'[^a-z0-9]', '', title.lower())
        if parsed_norm and all_items:
            alpha_matches = []
            for it in all_items:
                it_clean = re.sub(r'[^a-z0-9]', '', it.get("title", "").lower())
                it_slug = (it.get("slug") or "").lower().replace("-", "")
                if it_clean == parsed_norm:
                    alpha_matches.append(it)
                elif it_slug and (it_slug == parsed_norm or it_slug.startswith(parsed_norm) or parsed_norm.startswith(it_slug)):
                    alpha_matches.append(it)
                elif len(parsed_norm) >= 6 and len(it_clean) >= 6 and (parsed_norm in it_clean or it_clean in parsed_norm):
                    alpha_matches.append(it)

            if len(alpha_matches) == 1:
                return alpha_matches[0]
            elif len(alpha_matches) > 1:
                if parsed.get("year"):
                    for it in alpha_matches:
                        if it.get("release_year") == parsed["year"]:
                            return it
                return alpha_matches[0]

    except Exception:
        pass
    return None
