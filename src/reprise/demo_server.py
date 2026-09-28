"""Local-only browser demo. Secrets stay on the server."""
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from livekit import api

from .config import ROOT, Settings

app = FastAPI(title="Reprise local demo", docs_url=None, redoc_url=None)
DEMO = ROOT / "demo"
issued: set[str] = set()


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    origin = request.headers.get("origin")
    parsed = urlparse(origin) if origin else None
    if parsed and (parsed.hostname not in {"127.0.0.1", "localhost"}
                   or parsed.port != request.url.port):
        return JSONResponse({"error": "Origin denied"}, status_code=403)
    return await call_next(request)


@app.get("/")
async def home():
    return FileResponse(DEMO / "index.html")


@app.get("/app.js")
async def script():
    return FileResponse(DEMO / "app.js", media_type="text/javascript")


@app.get("/vendor/livekit-client.umd.js")
async def livekit_bundle():
    path = DEMO / "node_modules/livekit-client/dist/livekit-client.umd.js"
    if not path.exists():
        raise HTTPException(503, "Run npm ci --prefix demo first")
    return FileResponse(path, media_type="text/javascript")


@app.post("/session")
async def session():
    Settings.from_env()
    import os

    url = os.getenv("LIVEKIT_URL")
    key = os.getenv("LIVEKIT_API_KEY")
    secret = os.getenv("LIVEKIT_API_SECRET")
    if not all((url, key, secret)):
        raise HTTPException(503, "LiveKit configuration is incomplete")
    room = f"reprise-demo-{uuid.uuid4().hex[:14]}"
    async with api.LiveKitAPI() as lk:
        await lk.room.create_room(api.CreateRoomRequest(
            name=room, empty_timeout=90, metadata=json.dumps({"reprise_mode": "extension"}),
        ))
    token = (api.AccessToken(key, secret).with_identity(f"demo-{uuid.uuid4().hex[:8]}")
             .with_name("Demo visitor").with_grants(api.VideoGrants(
                 room_join=True, room=room, can_publish=True, can_subscribe=True,
             )).to_jwt())
    issued.add(room)
    return {"url": url, "room": room, "token": token}


@app.get("/events/{room}")
async def events(room: str, after: int = 0):
    if room not in issued or not re.fullmatch(r"reprise-demo-[0-9a-f]{14}", room):
        raise HTTPException(404)
    if after < 0 or after > 50000:
        raise HTTPException(400)
    path: Path = ROOT / "runs" / room / "events.jsonl"
    if not path.exists():
        return {"events": [], "cursor": 0}
    lines = path.read_text().splitlines()
    allowed = {"conversation_item", "agent_state", "tool_executed", "tool_superseded",
               "tool_rejected", "manual_found", "session_error"}
    selected = [json.loads(line) for line in lines[after:] if line]
    return {"events": [e for e in selected if e.get("event") in allowed], "cursor": len(lines)}
