import logging
from typing import Any, Dict, List, Optional
import httpx
from ..config import settings

logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.tmdb.org/3"
TMDB_FALLBACK_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"

class TMDBClient:
    def __init__(self):
        self.read_token = settings.TMDB_API_READ_ACCESS_TOKEN
        self.api_key = settings.TMDB_API_KEY

    def _get_headers_and_params(self) -> tuple[Dict[str, str], Dict[str, str]]:
        headers = {"accept": "application/json"}
        params = {}
        if self.read_token:
            headers["Authorization"] = f"Bearer {self.read_token}"
        elif self.api_key:
            params["api_key"] = self.api_key
        return headers, params

    async def _fetch(self, endpoint: str, extra_params: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Optional[httpx.Response]:
        """
        Fetches an endpoint from TMDB, preferring api.tmdb.org (CDN, unblocked globally)
        and falling back to api.themoviedb.org if needed.
        """
        headers, base_params = self._get_headers_and_params()
        params = {**base_params, **(extra_params or {})}

        for base_url in [TMDB_BASE_URL, TMDB_FALLBACK_URL]:
            url = f"{base_url}/{endpoint.lstrip('/')}"
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    res = await client.get(url, headers=headers, params=params)
                    return res
            except (httpx.ConnectTimeout, httpx.ConnectError, httpx.RequestError) as e:
                logger.warning(f"Connection to TMDB at {base_url} failed ({type(e).__name__}): {e}. Trying fallback if available...")
                continue
            except Exception as e:
                logger.exception(f"Unexpected error requesting TMDB {url}: {e}")
                return None
        return None

    async def search(self, query: str, media_type: str = "multi") -> List[Dict[str, Any]]:
        """
        Search TMDB for movies, TV series, or both.
        """
        endpoint = "search/multi" if media_type == "multi" else f"search/{media_type}"
        try:
            res = await self._fetch(endpoint, extra_params={"query": query}, timeout=8.0)
            if res and res.status_code == 200:
                results = res.json().get("results", [])
                formatted = []
                for item in results:
                    m_type = item.get("media_type") or media_type
                    if m_type not in ["movie", "tv"]:
                        continue
                    title = item.get("title") or item.get("name")
                    release_date = item.get("release_date") or item.get("first_air_date") or ""
                    year = int(release_date.split("-")[0]) if release_date and "-" in release_date else None
                    poster_path = item.get("poster_path")
                    backdrop_path = item.get("backdrop_path")
                    
                    formatted.append({
                        "tmdb_id": item.get("id"),
                        "type": "series" if m_type == "tv" else "movie",
                        "title": title,
                        "release_year": year,
                        "synopsis": item.get("overview"),
                        "poster_url": f"{TMDB_IMAGE_BASE}/w500{poster_path}" if poster_path else None,
                        "backdrop_url": f"{TMDB_IMAGE_BASE}/original{backdrop_path}" if backdrop_path else None,
                        "popularity": item.get("popularity", 0.0)
                    })
                return formatted
            else:
                code = res.status_code if res else "No Response"
                text = res.text[:200] if res else ""
                logger.warning(f"TMDB search error: {code} {text}")
                return []
        except Exception as e:
            logger.exception(f"TMDB search failed: {e}")
            return []

    async def get_details(self, tmdb_id: int, media_type: str = "movie") -> Optional[Dict[str, Any]]:
        """
        Fetch full details including cast, crew, videos, release dates.
        """
        endpoint = f"tv/{tmdb_id}" if media_type in ["series", "tv"] else f"movie/{tmdb_id}"
        extra_params = {"append_to_response": "credits,videos,release_dates,content_ratings,external_ids"}

        try:
            res = await self._fetch(endpoint, extra_params=extra_params, timeout=10.0)
            if not res or res.status_code != 200:
                code = res.status_code if res else "No Response"
                logger.error(f"Failed to fetch TMDB details for {tmdb_id}: {code}")
                return None
            data = res.json()

            # Title & Dates
            title = data.get("title") or data.get("name")
            release_date = data.get("release_date") or data.get("first_air_date") or ""
            year = int(release_date.split("-")[0]) if release_date and "-" in release_date else None

            # Genres
            genres = [g.get("name") for g in data.get("genres", []) if g.get("name")]

            # Cast
            cast_raw = data.get("credits", {}).get("cast", [])[:10]
            cast_members = [{"name": c.get("name"), "character": c.get("character")} for c in cast_raw]

            # Directors / Creators
            creators_directors = []
            if media_type in ["tv", "series"]:
                for cr in data.get("created_by", []):
                    creators_directors.append({"name": cr.get("name"), "role": "Creator"})
            else:
                crew = data.get("credits", {}).get("crew", [])
                for member in crew:
                    if member.get("job") == "Director":
                        creators_directors.append({"name": member.get("name"), "role": "Director"})

            # YouTube Trailer Key
            videos = data.get("videos", {}).get("results", [])
            trailer_key = None
            for v in videos:
                if v.get("site") == "YouTube" and v.get("type") in ["Trailer", "Teaser"]:
                    trailer_key = v.get("key")
                    if v.get("type") == "Trailer":
                        break

            # IMDb ID
            external_ids = data.get("external_ids", {})
            imdb_id = external_ids.get("imdb_id") or data.get("imdb_id")

            # Maturity Rating
            maturity_rating = "U/A 16+"
            if media_type in ["tv", "series"]:
                content_ratings = data.get("content_ratings", {}).get("results", [])
                for cr in content_ratings:
                    if cr.get("iso_3166_1") in ["US", "IN", "GB"]:
                        maturity_rating = cr.get("rating")
                        break
            else:
                releases = data.get("release_dates", {}).get("results", [])
                for r in releases:
                    if r.get("iso_3166_1") in ["US", "IN"]:
                        dates = r.get("release_dates", [])
                        if dates and dates[0].get("certification"):
                            maturity_rating = dates[0].get("certification")
                            break

            poster_path = data.get("poster_path")
            backdrop_path = data.get("backdrop_path")

            return {
                "tmdb_id": tmdb_id,
                "imdb_id": imdb_id,
                "type": "series" if media_type in ["tv", "series"] else "movie",
                "title": title,
                "synopsis": data.get("overview"),
                "release_year": year,
                "maturity_rating": maturity_rating or "U/A 16+",
                "runtime_minutes": data.get("runtime") or (data.get("episode_run_time", [45])[0] if data.get("episode_run_time") else 45),
                "poster_url": f"{TMDB_IMAGE_BASE}/w500{poster_path}" if poster_path else None,
                "backdrop_url": f"{TMDB_IMAGE_BASE}/original{backdrop_path}" if backdrop_path else None,
                "trailer_youtube_key": trailer_key,
                "genres": genres,
                "cast_members": cast_members,
                "creators_directors": creators_directors,
                "seasons": data.get("seasons", []) if media_type in ["tv", "series"] else []
            }
        except Exception as e:
            logger.exception(f"Error fetching TMDB details for {tmdb_id}: {e}")
            return None

    async def get_tv_season(self, tmdb_id: int, season_number: int) -> Optional[Dict[str, Any]]:
        """
        Fetch season episodes.
        """
        endpoint = f"tv/{tmdb_id}/season/{season_number}"
        try:
            res = await self._fetch(endpoint, timeout=10.0)
            if res and res.status_code == 200:
                return res.json()
            return None
        except Exception as e:
            logger.exception(f"Error fetching TV season {season_number} for {tmdb_id}: {e}")
            return None

tmdb_client = TMDBClient()
