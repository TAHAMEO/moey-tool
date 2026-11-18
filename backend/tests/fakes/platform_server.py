"""Local fake of the documented Google (YouTube Data/Analytics, Drive, OAuth) and
Instagram Graph API behaviour, for CONTRACT tests only.

It checks request shapes CLIPFORGE sends (auth headers, resumable-upload
Content-Range semantics, container lifecycle) and returns documented response
shapes. It is never reported as real platform integration.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class FakePlatforms:
    def __init__(self) -> None:
        self.videos: dict[str, dict] = {}
        self.channel_id = "UC_OWNER"
        self.captions: dict[str, list[dict]] = {}
        self.caption_text: dict[str, str] = {}
        self.ig_media: list[dict] = []
        self.ig_user_id = "17841400000000001"
        self.uploads: dict[str, dict] = {}
        self.uploaded_videos: dict[str, dict] = {}
        self.inserted_captions: list[dict] = []
        self.containers: dict[str, dict] = {}
        self.published: dict[str, dict] = {}
        self.fail_next_chunk = False
        self.quota_exceeded = False
        self.requests: list[dict] = []
        self.analytics_rows: dict[str, list] = {}
        self.ig_insights: dict[str, dict] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload=None, headers=None, raw: bytes | None = None, ctype="application/json"):
                body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else b"")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))

            def _route(self, method: str):
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                body = self._body() if method in ("POST", "PUT") else b""
                server.requests.append({"method": method, "path": parts.path, "query": query, "headers": dict(self.headers),
                                        "size": len(body)})
                return parts.path, query, body

            # ---------------------------------------------------------------- GET
            def do_GET(self):
                path, q, _ = self._route("GET")
                if path == "/oembed":
                    vid = re.search(r"v=([\w-]{11})", q.get("url", ""))
                    item = server.videos.get(vid.group(1)) if vid else None
                    if item and item["status"]["privacyStatus"] == "public":
                        return self._send(200, {"title": item["snippet"]["title"], "author_name": item["snippet"]["channelTitle"],
                                                "thumbnail_url": "https://i.ytimg.com/vi/x/hq.jpg"})
                    return self._send(401 if item else 404, {})
                if path == "/youtube/v3/videos":
                    if server.quota_exceeded:
                        return self._send(403, {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
                    ids = q.get("id", "").split(",")
                    items = [server.videos[i] for i in ids if i in server.videos]
                    items += [server.uploaded_videos[i] for i in ids if i in server.uploaded_videos]
                    return self._send(200, {"items": items})
                if path == "/youtube/v3/channels":
                    return self._send(200, {"items": [{"id": server.channel_id, "snippet": {"title": "Owner Channel"}}]})
                if path == "/youtube/v3/captions":
                    return self._send(200, {"items": server.captions.get(q.get("videoId"), [])})
                if m := re.fullmatch(r"/youtube/v3/captions/([\w-]+)", path):
                    return self._send(200, raw=server.caption_text.get(m.group(1), "").encode(), ctype="text/plain")
                if path == "/v2/reports":
                    vid = q.get("filters", "").split("==")[-1]
                    rows = server.analytics_rows.get(vid)
                    headers = [{"name": "video"}] + [{"name": n} for n in q.get("metrics", "").split(",")]
                    return self._send(200, {"columnHeaders": headers, "rows": [[vid, *rows]] if rows else []})
                if path == "/drive/v3/files":
                    return self._send(200, {"files": []})
                # ------------------------------------------------- Instagram
                if m := re.fullmatch(r"/v[\d.]+/(\d+)/media", path):
                    return self._send(200, {"data": server.ig_media, "paging": {"cursors": {"after": None}}})
                if path.endswith("/me"):
                    return self._send(200, {"user_id": server.ig_user_id, "username": "owner_reels", "account_type": "BUSINESS"})
                if m := re.fullmatch(r"/v[\d.]+/(\d+)/content_publishing_limit", path):
                    return self._send(200, {"data": [{"quota_usage": 1, "config": {"quota_total": 50}}]})
                if m := re.fullmatch(r"/v[\d.]+/(\d+)/insights", path):
                    metrics = q.get("metric", "").split(",")
                    data = server.ig_insights.get(m.group(1), {})
                    unsupported = [x for x in metrics if x not in data]
                    if unsupported and len(metrics) > 1:
                        return self._send(400, {"error": {"code": 100, "message": f"metric {unsupported[0]} not supported"}})
                    if unsupported:
                        return self._send(400, {"error": {"code": 100, "message": f"metric {unsupported[0]} not supported"}})
                    return self._send(200, {"data": [{"name": x, "values": [{"value": data[x]}]} for x in metrics]})
                if m := re.fullmatch(r"/v[\d.]+/(\d+)", path):
                    obj_id = m.group(1)
                    if obj_id in server.containers:
                        c = server.containers[obj_id]
                        c["polls"] = c.get("polls", 0) + 1
                        status = "FINISHED" if c["polls"] >= 2 and c.get("uploaded") else "IN_PROGRESS"
                        return self._send(200, {"id": obj_id, "status_code": status, "status": status})
                    if obj_id in server.published:
                        return self._send(200, server.published[obj_id])
                return self._send(404, {"error": {"message": f"no route {path}"}})

            # --------------------------------------------------------------- POST
            def do_POST(self):
                path, q, body = self._route("POST")
                form = {k: v[0] for k, v in parse_qs(body.decode(errors="ignore")).items()} if body and b"=" in body[:200] else {}
                if path == "/token":
                    return self._send(200, {"access_token": "ya29.refreshed", "expires_in": 3600, "token_type": "Bearer"})
                if path == "/upload/youtube/v3/videos" and q.get("uploadType") == "resumable":
                    if server.quota_exceeded:
                        return self._send(403, {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
                    sid = uuid.uuid4().hex
                    meta = json.loads(body or b"{}")
                    server.uploads[sid] = {"received": 0, "total": int(self.headers["X-Upload-Content-Length"]), "meta": meta}
                    host = self.headers.get("Host")
                    return self._send(200, {}, headers={"Location": f"http://{host}/upload-session/{sid}"})
                if path == "/upload/youtube/v3/captions":
                    match = re.search(rb'\{"snippet".*?\}\}', body)
                    snippet = json.loads(match.group(0))["snippet"] if match else {}
                    cid = "cap_" + uuid.uuid4().hex[:8]
                    server.inserted_captions.append({"id": cid, **snippet, "bytes": len(body)})
                    server.captions.setdefault(snippet.get("videoId"), []).append(
                        {"id": cid, "snippet": {**snippet, "status": "serving", "trackKind": "standard"}})
                    return self._send(200, {"id": cid, "snippet": {**snippet, "status": "serving"}})
                if path == "/upload/youtube/v3/thumbnails/set":
                    return self._send(200, {"items": [{"default": {"url": "https://i.ytimg.com/x.jpg"}}]})
                if path == "/youtube/v3/playlistItems":
                    return self._send(200, {"id": "pli_1"})
                if m := re.fullmatch(r"/v[\d.]+/(\d+)/media", path):
                    cid = str(18000000000000000 + len(server.containers) + 1)
                    params = {**q, **form}
                    server.containers[cid] = {"params": params, "uploaded": "video_url" in params}
                    host = self.headers.get("Host")
                    resp = {"id": cid}
                    if params.get("upload_type") == "resumable":
                        resp["uri"] = f"http://{host}/rupload/{cid}"
                    return self._send(200, resp)
                if m := re.fullmatch(r"/rupload/(\d+)", path):
                    if not self.headers.get("Authorization", "").startswith("OAuth "):
                        return self._send(401, {"error": {"code": 190, "message": "bad auth"}})
                    server.containers[m.group(1)]["uploaded"] = len(body) == int(self.headers.get("file_size", -1))
                    return self._send(200, {"success": True})
                if m := re.fullmatch(r"/v[\d.]+/(\d+)/media_publish", path):
                    cid = {**q, **form}["creation_id"]
                    mid = str(17900000000000000 + len(server.published) + 1)
                    server.published[mid] = {"id": mid, "permalink": f"https://www.instagram.com/reel/FAKE{mid[-4:]}/",
                                             "media_type": "VIDEO", "media_product_type": "REELS", "timestamp": "2026-11-18T12:00:00+0000",
                                             "caption": server.containers[cid]["params"].get("caption")}
                    return self._send(200, {"id": mid})
                return self._send(404, {"error": {"message": f"no route {path}"}})

            # ---------------------------------------------------------------- PUT
            def do_PUT(self):
                path, _q, body = self._route("PUT")
                m = re.fullmatch(r"/upload-session/(\w+)", path)
                if not m or m.group(1) not in server.uploads:
                    return self._send(404, {})
                up = server.uploads[m.group(1)]
                rng = self.headers.get("Content-Range", "")
                if rng.startswith("bytes */"):
                    if up["received"] >= up["total"]:
                        return self._send(200, up["resource"])
                    return self._send(308, {}, headers={"Range": f"bytes=0-{up['received'] - 1}"} if up["received"] else {})
                start, end = (int(x) for x in rng.split(" ")[1].split("/")[0].split("-"))
                if start != up["received"]:
                    return self._send(308, {}, headers={"Range": f"bytes=0-{up['received'] - 1}"})
                if server.fail_next_chunk:
                    server.fail_next_chunk = False
                    up["received"] += len(body) // 2  # server stored only part of the chunk
                    return self._send(503, {"error": {"message": "backend error"}})
                up["received"] = end + 1
                if up["received"] >= up["total"]:
                    vid = "VID" + uuid.uuid4().hex[:8]
                    up["resource"] = {"id": vid, "snippet": up["meta"].get("snippet"), "status": up["meta"].get("status")}
                    server.uploaded_videos[vid] = {"id": vid, "snippet": up["meta"].get("snippet", {}),
                                                   "status": {**up["meta"].get("status", {}), "uploadStatus": "processed"},
                                                   "processingDetails": {"processingStatus": "succeeded"},
                                                   "statistics": {"viewCount": "120", "likeCount": "9", "commentCount": "2"}}
                    return self._send(200, up["resource"])
                return self._send(308, {}, headers={"Range": f"bytes=0-{up['received'] - 1}"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def add_video(self, video_id: str, *, title: str, channel_id: str | None = None, privacy: str = "public",
                  live: str = "none", ended: bool = False, duration: str = "PT1M16S", captions: bool = False):
        details = {}
        if live == "live":
            details = {"actualStartTime": "2026-11-18T10:00:00Z"}
        if live == "upcoming":
            details = {"scheduledStartTime": "2026-11-19T10:00:00Z"}
        if ended:
            details = {"actualStartTime": "2026-11-18T08:00:00Z", "actualEndTime": "2026-11-18T09:00:00Z"}
        self.videos[video_id] = {
            "id": video_id,
            "snippet": {"title": title, "channelId": channel_id or self.channel_id, "channelTitle": "Some Channel",
                        "liveBroadcastContent": live, "publishedAt": "2026-11-01T00:00:00Z", "description": "desc"},
            "contentDetails": {"duration": duration, "caption": "true" if captions else "false"},
            "status": {"privacyStatus": privacy},
            "liveStreamingDetails": details or None,
        }
        if not details:
            del self.videos[video_id]["liveStreamingDetails"]

    def __enter__(self) -> FakePlatforms:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
