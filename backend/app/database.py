import logging
from typing import Any, Dict, List, Optional
import httpx
from .config import settings

logger = logging.getLogger(__name__)

class SupabaseClient:
    def __init__(self):
        self.base_url = f"{settings.SUPABASE_URL.rstrip('/')}/rest/v1"
        self.anon_key = settings.SUPABASE_ANON_KEY
        self.service_key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY

    def _headers(self, use_service_role: bool = True) -> Dict[str, str]:
        key = self.service_key if use_service_role else self.anon_key
        return {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }

    async def select(self, table: str, params: Optional[Dict[str, str]] = None, use_service_role: bool = True) -> List[Dict[str, Any]]:
        headers = self._headers(use_service_role)
        url = f"{self.base_url}/{table}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
            if resp.status_code >= 400:
                logger.error(f"Supabase select error on {table}: {resp.status_code} {resp.text}")
                return []
            return resp.json()

    async def select_with_count(self, table: str, params: Optional[Dict[str, str]] = None, use_service_role: bool = True) -> tuple[List[Dict[str, Any]], int]:
        headers = self._headers(use_service_role)
        headers["Prefer"] = "count=exact"
        url = f"{self.base_url}/{table}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
            if resp.status_code >= 400:
                logger.error(f"Supabase select_with_count error on {table}: {resp.status_code} {resp.text}")
                return [], 0
            
            data = resp.json()
            total_count = len(data)
            cr = resp.headers.get("content-range") or resp.headers.get("Content-Range")
            if cr and "/" in cr:
                try:
                    total_count = int(cr.split("/")[-1])
                except (ValueError, TypeError):
                    pass
            return data, total_count

    async def insert(self, table: str, data: Dict[str, Any], use_service_role: bool = True) -> Optional[Dict[str, Any]]:
        headers = self._headers(use_service_role)
        url = f"{self.base_url}/{table}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=data)
            if resp.status_code >= 400:
                logger.error(f"Supabase insert error on {table}: {resp.status_code} {resp.text}")
                return None
            if resp.status_code == 204 or not resp.text.strip():
                return {}
            res = resp.json()
            return res[0] if isinstance(res, list) and res else res

    async def update(self, table: str, match_column: str, match_value: Any, data: Dict[str, Any], use_service_role: bool = True) -> Optional[Dict[str, Any]]:
        headers = self._headers(use_service_role)
        url = f"{self.base_url}/{table}"
        params = {match_column: f"eq.{match_value}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(url, headers=headers, params=params, json=data)
            if resp.status_code >= 400:
                logger.error(f"Supabase update error on {table}: {resp.status_code} {resp.text}")
                return None
            if resp.status_code == 204 or not resp.text.strip():
                return {}
            res = resp.json()
            return res[0] if isinstance(res, list) and res else res

    async def update_where(self, table: str, params: Dict[str, str], data: Dict[str, Any], use_service_role: bool = True) -> List[Dict[str, Any]]:
        headers = self._headers(use_service_role)
        url = f"{self.base_url}/{table}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(url, headers=headers, params=params, json=data)
            if resp.status_code >= 400:
                logger.error(f"Supabase update_where error on {table}: {resp.status_code} {resp.text}")
                return []
            if resp.status_code == 204 or not resp.text.strip():
                return []
            res = resp.json()
            return res if isinstance(res, list) else [res]

    async def delete(self, table: str, match_column: str, match_value: Any, use_service_role: bool = True) -> bool:
        headers = self._headers(use_service_role)
        url = f"{self.base_url}/{table}"
        params = {match_column: f"eq.{match_value}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(url, headers=headers, params=params)
            if resp.status_code >= 400:
                logger.error(f"Supabase delete error on {table}: {resp.status_code} {resp.text}")
                return False
            return True

    async def rpc(self, func_name: str, payload: Optional[Dict[str, Any]] = None, use_service_role: bool = True) -> Any:
        headers = self._headers(use_service_role)
        url = f"{settings.SUPABASE_URL.rstrip('/')}/rest/v1/rpc/{func_name}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload or {})
            if resp.status_code >= 400:
                logger.error(f"Supabase RPC error on {func_name}: {resp.status_code} {resp.text}")
                return None
            if resp.status_code == 204 or not resp.text.strip():
                return None
            try:
                return resp.json()
            except Exception:
                return resp.text

db = SupabaseClient()
