import base64
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from ..config import settings

logger = logging.getLogger(__name__)

class Aria2RPCClient:
    def __init__(self):
        protocol = "https" if settings.ARIA2_RPC_SSL else "http"
        self.endpoint = f"{protocol}://{settings.ARIA2_RPC_HOST}:{settings.ARIA2_RPC_PORT}/jsonrpc"
        self.secret = settings.ARIA2_RPC_SECRET

    @property
    def rpc_secret(self) -> str:
        return self.secret or settings.ARIA2_RPC_SECRET

    async def call(self, method: str, params: Optional[List[Any]] = None) -> Any:
        payload_params = []
        secret = self.rpc_secret
        if secret:
            payload_params.append(f"token:{secret}")
        if params:
            payload_params.extend(params)

        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": f"aria2.{method}",
            "params": payload_params
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(self.endpoint, json=body)
                if res.status_code == 200:
                    resp_json = res.json()
                    if "error" in resp_json:
                        err_msg = resp_json["error"].get("message", "Aria2 RPC error")
                        logger.error(f"Aria2 RPC error response: {resp_json['error']}")
                        raise RuntimeError(f"Aria2 RPC error: {err_msg}")
                    return resp_json.get("result")
                else:
                    logger.error(f"Aria2 RPC HTTP error: {res.status_code} {res.text}")
                    raise RuntimeError(f"Aria2 HTTP status {res.status_code}: {res.text}")
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            logger.warning(f"Failed to communicate with Aria2 RPC at {self.endpoint}: {e}")
            raise ConnectionError(f"Aria2 RPC daemon offline: {e}")

    async def add_uri(self, uris: List[str], options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        Add direct URL or BitTorrent Magnet link.
        """
        opts = options or {}
        if "dir" not in opts:
            opts["dir"] = settings.ARIA2_DOWNLOAD_DIR
        opts.setdefault("seed-time", "0")
        opts.setdefault("bt-stop-timeout", "600")
        opts.setdefault("follow-torrent", "mem")
        proxy = getattr(settings, "ARIA2_EGRESS_PROXY", "")
        if proxy and "all-proxy" not in opts:
            opts["all-proxy"] = proxy
        params = [uris, opts]
        return await self.call("addUri", params)

    async def add_torrent(self, torrent_bytes: bytes, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        Add .torrent file.
        """
        opts = options or {}
        if "dir" not in opts:
            opts["dir"] = settings.ARIA2_DOWNLOAD_DIR
        opts.setdefault("seed-time", "0")
        opts.setdefault("bt-stop-timeout", "600")
        opts.setdefault("follow-torrent", "mem")
        proxy = getattr(settings, "ARIA2_EGRESS_PROXY", "")
        if proxy and "all-proxy" not in opts:
            opts["all-proxy"] = proxy
        b64_content = base64.b64encode(torrent_bytes).decode("utf-8")
        params = [b64_content, [], opts]
        return await self.call("addTorrent", params)

    async def multicall(self, methods: List[Dict[str, Any]]) -> List[Any]:
        """
        Executes multiple Aria2 JSON-RPC methods in a single HTTP POST via system.multicall.
        """
        secret = self.rpc_secret
        token_prefix = [f"token:{secret}"] if secret else []
        calls = []
        for m in methods:
            m_name = m.get("methodName", "")
            if not m_name.startswith("aria2."):
                m_name = f"aria2.{m_name}"
            m_params = token_prefix + list(m.get("params", []))
            calls.append({"methodName": m_name, "params": m_params})

        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "system.multicall",
            "params": [calls]
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(self.endpoint, json=body)
                if res.status_code == 200:
                    resp_json = res.json()
                    if "error" in resp_json:
                        logger.error(f"Aria2 multicall error: {resp_json['error']}")
                        return []
                    results = resp_json.get("result", [])
                    unwrapped = []
                    for item in results:
                        if isinstance(item, list) and len(item) > 0:
                            unwrapped.append(item[0])
                        else:
                            unwrapped.append(item)
                    return unwrapped
                else:
                    logger.error(f"Aria2 multicall HTTP error: {res.status_code}")
                    return []
        except Exception as e:
            logger.warning(f"Failed Aria2 multicall at {self.endpoint}: {e}")
            return []

    async def tell_all_downloads(self, waiting_offset: int = 0, waiting_num: int = 10, stopped_offset: int = 0, stopped_num: int = 15) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Batches tellActive, tellWaiting, and tellStopped into a single HTTP POST.
        """
        active_keys = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed", "numSeeders", "connections", "files", "bittorrent"]
        waiting_keys = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "files", "bittorrent"]
        stopped_keys = ["gid", "status", "totalLength", "completedLength", "files", "bittorrent", "errorMessage"]

        results = await self.multicall([
            {"methodName": "aria2.tellActive", "params": [active_keys]},
            {"methodName": "aria2.tellWaiting", "params": [waiting_offset, waiting_num, waiting_keys]},
            {"methodName": "aria2.tellStopped", "params": [stopped_offset, stopped_num, stopped_keys]}
        ])

        active = results[0] if len(results) > 0 and isinstance(results[0], list) else []
        waiting = results[1] if len(results) > 1 and isinstance(results[1], list) else []
        stopped = results[2] if len(results) > 2 and isinstance(results[2], list) else []
        return active, waiting, stopped

    async def tell_active(self) -> List[Dict[str, Any]]:
        keys = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed", "numSeeders", "connections", "files", "bittorrent"]
        return await self.call("tellActive", [keys]) or []

    async def tell_waiting(self, offset: int = 0, num: int = 20) -> List[Dict[str, Any]]:
        keys = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "files", "bittorrent"]
        return await self.call("tellWaiting", [offset, num, keys]) or []

    async def tell_stopped(self, offset: int = 0, num: int = 20) -> List[Dict[str, Any]]:
        keys = ["gid", "status", "totalLength", "completedLength", "files", "bittorrent", "errorMessage"]
        return await self.call("tellStopped", [offset, num, keys]) or []

    async def tell_status(self, gid: str) -> Optional[Dict[str, Any]]:
        return await self.call("tellStatus", [gid])

    async def pause(self, gid: str) -> Optional[str]:
        return await self.call("pause", [gid])

    async def unpause(self, gid: str) -> Optional[str]:
        return await self.call("unpause", [gid])

    async def remove(self, gid: str) -> Optional[str]:
        return await self.call("remove", [gid])

    async def force_remove(self, gid: str) -> Optional[str]:
        return await self.call("forceRemove", [gid])

    async def remove_download_result(self, gid: str) -> Optional[str]:
        return await self.call("removeDownloadResult", [gid])

    async def purge_download_result(self) -> Optional[str]:
        return await self.call("purgeDownloadResult")

    async def get_global_stat(self) -> Dict[str, Any]:
        return await self.call("getGlobalStat") or {
            "downloadSpeed": "0",
            "uploadSpeed": "0",
            "numActive": "0",
            "numWaiting": "0",
            "numStopped": "0"
        }

aria2_client = Aria2RPCClient()
