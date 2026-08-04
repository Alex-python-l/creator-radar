"""
GeeLark API client — Token auth (Bearer token from GeeLark client settings).
All requests POST JSON to openapi.geelark.com.
"""
import uuid
import requests


BASE_URL = "https://openapi.geelark.com"
YOUTUBE_TITLE_SAFE_UTF16_UNITS = 90
YOUTUBE_TITLE_FALLBACK = "Short"


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _truncate_utf16_units(value: str, max_units: int) -> str:
    used = 0
    out = []
    for char in value:
        char_units = _utf16_units(char)
        if used + char_units > max_units:
            break
        out.append(char)
        used += char_units
    return "".join(out)


def _youtube_title(value: str | None) -> str:
    title = (value or "").strip()
    if not title:
        return YOUTUBE_TITLE_FALLBACK
    title = _truncate_utf16_units(
        title, YOUTUBE_TITLE_SAFE_UTF16_UNITS).strip()
    return title or YOUTUBE_TITLE_FALLBACK


_GEELARK_STATIC_PROXY_TYPE_ID = {"socks5": 1, "http": 2, "https": 3}


def proxy_url_to_static_config(url: str) -> dict:
    """Parse a `scheme://user:pass@host:port` URL into the
    `proxyConfig` dict that GeeLark's `/phone/detail/update` expects for
    static proxies.

    Raises ValueError if the URL is missing a host/port or uses a
    non-static scheme (socks5h/socks4 aren't in GeeLark's static list).
    """
    from urllib.parse import urlsplit, unquote

    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    type_id = _GEELARK_STATIC_PROXY_TYPE_ID.get(scheme)
    if type_id is None:
        raise ValueError(f"proxy scheme {scheme!r} not supported by GeeLark "
                         "static proxy config (expected socks5/http/https)")
    if not parts.hostname or not parts.port:
        raise ValueError(f"proxy URL {url!r} missing host or port")
    return {
        "typeId": type_id,
        "server": parts.hostname,
        "port": parts.port,
        "username": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
    }


class GeeLarkClient:
    def __init__(self, token: str):
        self._token = token

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
            "traceId": str(uuid.uuid4()).upper(),
        }

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{BASE_URL}{path}"
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"GeeLark API error [{body.get('code')}]: {body.get('msg')} | path={path}")
        return body.get("data", {})

    # ── Cloud Phone ──────────────────────────────────────────────────────────

    def create_phones(self, entries: list[dict]) -> dict:
        """
        entries: list of EnvRowApi dicts.
        Returns the full data dict with details[].
        """
        payload = {
            "mobileType": "Android 15",
            "chargeMode": 0,  # pay per minute
            "data": entries,
        }
        return self._post("/open/v1/phone/addNew", payload)

    def start_phones(self, ids: list[str]) -> dict:
        return self._post("/open/v1/phone/start", {"ids": ids})

    def stop_phones(self, ids: list[str]) -> dict:
        return self._post("/open/v1/phone/stop", {"ids": ids})

    def delete_phones(self, ids: list[str]) -> dict:
        return self._post("/open/v1/phone/delete", {"ids": ids})

    def get_phone_status(self, ids: list[str]) -> dict:
        return self._post("/open/v1/phone/status", {"ids": ids})

    def list_phones(self, page: int = 1, page_size: int = 100) -> dict:
        return self._post("/open/v1/phone/list", {"page": page, "pageSize": page_size})

    def update_phone_detail(
        self,
        env_id: str,
        *,
        name: str | None = None,
        proxy_config: dict | None = None,
    ) -> dict:
        """Modify GeeLark cloud phone details via
        /open/v1/phone/detail/update.

        `proxy_config` mirrors GeeLark's static-proxy shape: `{typeId,
        server, port, username, password}`. typeId 1=socks5, 2=http,
        3=https. Use `proxy_url_to_static_config()` to build it from our
        internal `scheme://user:pass@host:port` URL.

        The endpoint also accepts `groupID`, `tagIDs`, `remark`,
        `chargeMode`, `phoneNumber` — not wired up here because nothing
        in this app needs them yet.
        """
        payload: dict = {"id": env_id}
        if name is not None:
            payload["name"] = name
        if proxy_config is not None:
            payload["proxyConfig"] = proxy_config
        return self._post("/open/v1/phone/detail/update", payload)

    # ── Applications ─────────────────────────────────────────────────────────

    def get_installable_apps(self, env_id: str, name: str = "") -> dict:
        payload = {
            "envId": env_id,
            "name": name,
            "getUploadApp": False,
            "page": 1,
            "pageSize": 20,
        }
        return self._post("/open/v1/app/installable/list", payload)

    def install_app(self, env_id: str, app_version_id: str) -> dict:
        return self._post("/open/v1/app/install", {"envId": env_id, "appVersionId": app_version_id})

    def get_installed_apps(self, env_id: str) -> dict:
        return self._post("/open/v1/app/list", {"envId": env_id, "page": 1, "pageSize": 100})

    # ── Automation ───────────────────────────────────────────────────────────

    def create_warmup_task(self, env_id: str, duration_minutes: int) -> str:
        """
        Schedule a 'browse video' warmup task starting now.
        Returns the task ID.
        """
        import time as _time
        payload = {
            "taskType": 2,
            "list": [{
                "scheduleAt": int(_time.time()),
                "envId": env_id,
                "action": "browse video",
                "duration": duration_minutes,
            }],
        }
        data = self._post("/open/v1/task/add", payload)
        return data["taskIds"][0]

    def query_tasks(self, task_ids: list[str]) -> list[dict]:
        data = self._post("/open/v1/task/query", {"ids": task_ids})
        return data.get("items", [])

    def cancel_tasks(self, task_ids: list[str]) -> dict:
        """Cancel one or more RPA tasks.

        Verified 2026-06-15 (Phase 0a probe) on production M8 against
        a status=1 (waiting) task: HTTP 200, code=0, body
        {"totalAmount": N, "successAmount": N, "failAmount": 0}.
        Task detail flips to status=7 (cancelled) within ~6 seconds.

        Schema is `{"ids": [...]}` (lower-case plural list). The
        openapi-en.yaml file claims a singular `id` field — it's
        wrong; server returns 40004 "TaskListCancelReq.IDs required"
        when that schema is sent.
        """
        return self._post("/open/v1/task/cancel", {"ids": task_ids})

    def get_task_detail(self, task_id: str) -> dict:
        """Full detail for one task: status, failCode, failDesc,
        resultImages (RPA screenshots; the last frame shows where the flow
        got stuck) and step logs. /open/v1/task/detail expects the param key
        `ID` (capitalised) and returns the task object directly."""
        return self._post("/open/v1/task/detail", {"ID": task_id})

    def get_upload_url(self, file_type: str) -> dict:
        """
        Request a temporary upload URL for a file. file_type is the extension
        without the dot, e.g. "mp4", "jpg".
        Returns dict with `uploadUrl` (temp PUT target) and `resourceUrl`
        (permanent URL to use in tasks). Temp URL valid 30 min, file lives 3 days.
        """
        return self._post("/open/v1/upload/getUrl", {"fileType": file_type})

    # GeeLark's /upload/getUrl only accepts a narrow allowlist of fileType
    # strings; anything else (e.g. "mov" from iPhone exports) returns
    # [40004] wrong argument. Normalize common aliases to an accepted form —
    # iPhone .mov is H.264+AAC in ISO-BMFF, fully playable when uploaded as
    # .mp4 to GeeLark's OSS bucket.
    _FILETYPE_ALIASES = {
        "mov": "mp4",
        "m4v": "mp4",
        "qt":  "mp4",
        "jpeg": "jpg",
    }

    def upload_file(self, file_path: str, file_type: str) -> str:
        """
        Two-step upload: get an upload URL, PUT the file to it.
        Returns the permanent resourceUrl to use in subsequent task payloads.

        `file_type` is normalized (lowercased + aliased) so callers can pass
        the raw extension from a filename without worrying about GeeLark's
        accepted set.
        """
        ft = file_type.lower()
        ft = self._FILETYPE_ALIASES.get(ft, ft)
        info = self.get_upload_url(ft)
        upload_url = info["uploadUrl"]
        with open(file_path, "rb") as f:
            resp = requests.put(upload_url, data=f, timeout=600)
        resp.raise_for_status()
        return info["resourceUrl"]

    def create_video_post_task(
        self,
        env_id: str,
        video_url: str,
        video_desc: str = None,
        cover_url: str = None,
    ) -> str:
        """
        Schedule a TikTok video post (taskType 1) starting now.
        video_url must be a pre-uploaded resourceUrl from upload_file().
        Returns the task ID.

        `needShareLink` is set to True so /open/v1/task/query returns the
        public TikTok URL on completion. GeeLark's default is False, which
        leaves `shareLink` as an empty string on the task object.
        """
        import time as _time
        item = {
            "scheduleAt": int(_time.time()),
            "envId": env_id,
            "video": video_url,
            "needShareLink": True,
        }
        if video_desc:
            item["videoDesc"] = video_desc
        if cover_url:
            item["cover"] = cover_url
        payload = {"taskType": 1, "list": [item]}
        data = self._post("/open/v1/task/add", payload)
        return data["taskIds"][0]

    def create_youtube_post_task(
        self,
        env_id: str,
        video_url: str,
        title: str,
    ) -> str:
        """Schedule a YouTube Shorts post. Returns the task ID.

        Defaults: sameStyleVoice=0, originalVoice=100,
        isDisclosureMandatory=False. Title is capped below YouTube's mobile
        100-unit validator so the Upload Short button remains enabled."""
        import time as _time
        payload = {
            "scheduleAt": int(_time.time()),
            "id": env_id,
            "title": _youtube_title(title),
            "video": video_url,
            "sameStyleVoice": 0,
            "originalVoice": 100,
            "isDisclosureMandatory": False,
        }
        data = self._post("/open/v1/rpa/task/youtubePubShort", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(
                f"youtube task created but no taskId: {data}")
        return task_id

    def create_instagram_post_task(
        self,
        env_id: str,
        video_urls: list[str],
        description: str,
    ) -> str:
        """Schedule an Instagram Reels post. Returns the task ID.

        `video_urls` is a list (API supports up to 10); single-clip use
        passes `[url]`. Defaults: aiTag=False, needShareLink=False.
        Description truncated to 2200 chars."""
        import time as _time
        payload = {
            "scheduleAt": int(_time.time()),
            "id": env_id,
            "description": description[:2200],
            "video": list(video_urls),
            "aiTag": False,
            "needShareLink": False,
        }
        data = self._post("/open/v1/rpa/task/instagramPubReels", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(
                f"instagram task created but no taskId: {data}")
        return task_id

    def create_facebook_post_task(
        self,
        env_id: str,
        video_url: str,
        description: str,
    ) -> str:
        """Schedule a Facebook Reels post. Returns the task ID.

        Defaults: needShareLink=False, page omitted. Description
        truncated to 500 chars."""
        import time as _time
        payload = {
            "scheduleAt": int(_time.time()),
            "id": env_id,
            "description": description[:500],
            "video": video_url,
            "needShareLink": False,
        }
        data = self._post("/open/v1/rpa/task/faceBookPubReels", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(
                f"facebook task created but no taskId: {data}")
        return task_id

    def create_tiktok_ai_comment_task(
        self,
        env_id: str,
        links: list[str],
        *,
        schedule_at: int | None = None,
        comment_probability: int = 100,
        comment: str | None = None,
        name: str | None = None,
        remark: str | None = None,
    ) -> str:
        """Schedule a TikTok random-comment task. Returns the task ID.

        When `comment` is provided we use GeeLark's manual-comment mode
        (`useAi=2`). Otherwise GeeLark generates the comment itself
        (`useAi=1`).
        """
        import time as _time
        cleaned_comment = (comment or "").strip()
        payload = {
            "scheduleAt": int(schedule_at if schedule_at is not None else _time.time()),
            "id": env_id,
            "useAi": 2 if cleaned_comment else 1,
            "links": list(links),
            "commentProbability": int(comment_probability),
        }
        if cleaned_comment:
            payload["comment"] = cleaned_comment[:500]
        if name:
            payload["name"] = name[:128]
        if remark:
            payload["remark"] = remark[:200]
        data = self._post("/open/v1/rpa/task/tiktokRandomComment", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(f"tiktok AI comment task created but no taskId: {data}")
        return task_id

    def create_facebook_auto_comment_task(
        self,
        env_id: str,
        post_address: str,
        comments: list[str],
        keywords: list[str],
        *,
        schedule_at: int | None = None,
        name: str | None = None,
        remark: str | None = None,
    ) -> str:
        """Schedule a Facebook auto-comment task. Returns the task ID.

        `post_address` is NOT truncated here — service layer guarantees
        the URL is acceptable. `comments` / `keywords` are truncated per
        the GeeLark API limits as a defense-in-depth bound.
        """
        import time as _time
        payload = {
            "scheduleAt": int(schedule_at if schedule_at is not None else _time.time()),
            "id": env_id,
            "postAddress": post_address,
            "comment": [c[:8000] for c in comments],
            "keyword": [k[:100] for k in keywords],
        }
        if name:
            payload["name"] = name[:128]
        if remark:
            payload["remark"] = remark[:200]
        data = self._post("/open/v1/rpa/task/faceBookAutoComment", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(
                f"facebook comment task created but no taskId: {data}"
            )
        return task_id

    def edit_tiktok_profile(
        self,
        env_id: str,
        avatar: str = None,
        nickname: str = None,
        bio: str = None,
        site: str = None,
    ) -> str:
        """
        Schedule a TikTok profile edit RPA task starting now.
        Avatar must be a 1:1 aspect ratio image URL.
        Returns the task ID.
        """
        import time as _time
        payload = {
            "scheduleAt": int(_time.time()),
            "id": env_id,
        }
        if avatar:    payload["avatar"]   = avatar
        if nickname:  payload["nickName"] = nickname
        if bio:       payload["bio"]      = bio
        if site:      payload["site"]     = site
        data = self._post("/open/v1/rpa/task/tiktokEdit", payload)
        task_ids = data.get("taskIds") or [data.get("taskId")]
        return task_ids[0]

    def create_tiktok_login_task(self, env_id: str, account: str, password: str) -> str:
        """
        Schedule a TikTok account login RPA task starting now.
        Returns the task ID.
        """
        import time as _time
        payload = {
            "scheduleAt": int(_time.time()),
            "id": env_id,
            "account": account,
            "password": password,
        }
        data = self._post("/open/v1/rpa/task/tiktokLogin", payload)
        return data.get("taskId")

    # ── Billing ──────────────────────────────────────────────────────────────

    def get_wallet(self) -> dict:
        """
        Account wallet balance. GeeLark rate-limits this to 10 req/min.
        Returns {"balance": float, "giftMoney": float, "availableTimeAddOn": int}.
        """
        return self._post("/open/v1/pay/wallet", {})

    def list_billing_transactions(self, limit: int = 1000) -> dict:
        """Newest billing transactions, up to `limit` rows (max 1000).

        Verified 2026-06-09: the server schema is `EnvFlowSearchReq`
        and the only honored field is `Limit` (max 1000). Every other
        param we tried — `page`, `pageNo`, `pageIndex`, `pageSize`,
        `size`, `offset`, `Offset`, `Skip`, `cursor`, `marker`,
        `before`, `after`, `lastId`, `pageToken`, `startTime`,
        `BeginTime`, `EnvId`, etc. — is silently dropped. There is no
        pagination and no time filter on this endpoint.
        `Limit > 1000` returns `[40004] EnvFlowSearchReq.Limit max`.
        The response's `total` shows the real backend count (~1700+)
        but anything beyond the newest 1000 is not reachable here.

        Each item has shape:

            {"id": "<txn id>", "envId": "<phone id>", "amount": -0.049,
             "usedTime": 7, "type": 1|2, "chargeType": 2,
             "createdTime": 1780990801}

        amount is USD (negative for charges), usedTime is minutes
        (rounded up), createdTime is unix epoch (seconds).
        """
        # Server cap is 1000; clamp defensively so callers asking for
        # more get a deterministic max-size response instead of a 400.
        capped = max(1, min(int(limit), 1000))
        return self._post(
            "/open/v1/billing/transaction/detail",
            {"limit": capped},
        )
