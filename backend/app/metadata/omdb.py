import logging
from typing import Any, Dict, Optional
import httpx
from ..config import settings

logger = logging.getLogger(__name__)

class OMDbClient:
    def __init__(self):
        self.api_key = settings.OMDB_API_KEY
        self.base_url = "https://www.omdbapi.com"

    async def get_ratings(self, imdb_id: Optional[str] = None, title: Optional[str] = None, year: Optional[int] = None) -> float:
        """
        Fetches IMDb and Rotten Tomatoes ratings from OMDb and calculates a 0.0 - 10.0 score.
        """
        if not self.api_key:
            return 7.5 # Default fallback score

        params = {"apikey": self.api_key}
        if imdb_id:
            params["i"] = imdb_id
        elif title:
            params["t"] = title
            if year:
                params["y"] = str(year)
        else:
            return 7.5

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(self.base_url, params=params)
                if res.status_code != 200:
                    return 7.5
                data = res.json()
                if data.get("Response") != "True":
                    return 7.5

                scores = []
                
                # 1. IMDb Rating (e.g. "8.6")
                imdb_str = data.get("imdbRating")
                if imdb_str and imdb_str != "N/A":
                    try:
                        scores.append(float(imdb_str))
                    except ValueError:
                        pass

                # 2. Rotten Tomatoes (e.g. "94%")
                ratings = data.get("Ratings", [])
                for r in ratings:
                    if r.get("Source") == "Rotten Tomatoes":
                        val = r.get("Value", "").replace("%", "")
                        try:
                            # Convert 0-100% to 0-10 scale
                            scores.append(round(float(val) / 10.0, 1))
                        except ValueError:
                            pass
                    elif r.get("Source") == "Metacritic":
                        val = r.get("Value", "").split("/")[0]
                        try:
                            scores.append(round(float(val) / 10.0, 1))
                        except ValueError:
                            pass

                if scores:
                    return round(sum(scores) / len(scores), 1)
                return 7.5
        except Exception as e:
            logger.warning(f"OMDb rating fetch failed: {e}")
            return 7.5

omdb_client = OMDbClient()
