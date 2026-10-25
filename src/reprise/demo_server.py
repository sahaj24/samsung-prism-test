"""Local-only browser demo. Secrets stay on the server."""
import base64
import asyncio
import hashlib
import json
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from livekit import api

from .config import ROOT, Settings
from .call_activation import ACTIVATION_VERSION
from .google_workspace import GoogleAPIError, GoogleWorkspace
from .productivity import PlanStore, pending_confirmation

app = FastAPI(title="Reprise local demo", docs_url=None, redoc_url=None)
DEMO = ROOT / "demo"
issued: set[str] = set()
oauth_states: dict[str, tuple[str, float]] = {}
local_sessions: dict = {}
closing_sessions: set = set()


class LocalMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PlanApproval(BaseModel):
    expected_revision: int | None = None


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


@app.get("/phone.js")
async def phone_script():
    return FileResponse(DEMO / "phone.js", media_type="text/javascript")


@app.get("/vendor/livekit-client.umd.js")
async def livekit_bundle():
    path = DEMO / "node_modules/livekit-client/dist/livekit-client.umd.js"
    if not path.exists():
        raise HTTPException(503, "Run npm ci --prefix demo first")
    return FileResponse(path, media_type="text/javascript")


@app.post("/session")
async def session(mode: Literal["productivity", "benchmark", "call_assist"] = "productivity",
                  planner: Literal["gemini", "local"] = "gemini",
                  presentation: Literal["live", "demo-live"] = "live"):
    Settings.from_env()
    import os

    provider = None
    if mode == "call_assist":
        from .call_assist import workspace_backend
        try:
            provider = workspace_backend()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if planner != "gemini":
            raise HTTPException(400, "Live Call Assist uses the existing Gemini Live backend")

    if presentation == "demo-live" and planner != "gemini":
        raise HTTPException(400, "Demo Live requires the realtime Gemini audio session")

    if planner == "local":
        if mode != "productivity":
            raise HTTPException(400, "Local text planner supports Google Workspace only")
        if sum(not s.closed for s in local_sessions.values()) >= 4:
            raise HTTPException(409, "End an existing local session first")
        from .local_planner import LocalPlanner, LocalSession
        from .productivity import ProductivityBackend
        from .trace import Trace
        model = LocalPlanner()
        try:
            await model.health()
        except Exception:
            await model.close()
            raise HTTPException(503, "Local model unavailable; no fallback attempted") from None
        room = f"reprise-demo-{uuid.uuid4().hex[:14]}"
        trace = Trace(room, ROOT / "runs" / room)
        local_sessions[room] = LocalSession(room, model, ProductivityBackend(room, trace),
                                            trace, Settings.from_env())
        trace.event("local_connection", state="connected")
        issued.add(room)
        return {"room": room, "planner": "local", "modality": "text-only"}

    url = os.getenv("LIVEKIT_URL")
    key = os.getenv("LIVEKIT_API_KEY")
    secret = os.getenv("LIVEKIT_API_SECRET")
    if not all((url, key, secret)):
        raise HTTPException(503, "LiveKit configuration is incomplete")
    room = f"reprise-demo-{uuid.uuid4().hex[:14]}"
    async with api.LiveKitAPI() as lk:
        await lk.room.create_room(api.CreateRoomRequest(
            name=room, empty_timeout=90, metadata=json.dumps({"reprise_mode": mode, "presentation": presentation, "activation_version": ACTIVATION_VERSION, **({"workspace_backend": provider} if provider else {})}),
        ))
    token = (api.AccessToken(key, secret).with_identity(f"demo-{uuid.uuid4().hex[:8]}")
             .with_name("Demo visitor").with_grants(api.VideoGrants(
                 room_join=True, room=room, can_publish=True, can_subscribe=True,
             )).to_jwt())
    issued.add(room)
    return {"url": url, "room": room, "token": token, "presentation": presentation,
            "activation_version": ACTIVATION_VERSION, **({"workspace_backend": provider, "owner_confirmation_required": True} if provider else {})}


@app.get("/events/{room}")
async def events(room: str, after: int = 0):
    if room not in issued or not re.fullmatch(r"reprise-demo-[0-9a-f]{14}", room):
        raise HTTPException(404)
    if after < 0 or after > 50000:
        raise HTTPException(400)
    path: Path = ROOT / "runs" / room / "events.jsonl"
    if not path.exists():
        return {"events": [], "cursor": 0}
    # A polling reader can observe the writer midway through its last append.
    # Advance only past newline-terminated records, so that record is retried.
    lines = path.read_text().split("\n")[:-1]
    allowed = {"conversation_item", "agent_state", "tool_executed", "tool_superseded",
               "tool_rejected", "session_error", "plan_updated",
               "transcript_received", "speech_started", "speech_ended", "intent_revised",
               "tool_raw_proposed", "tool_proposed", "tool_dispatch", "tool_validation_failed",
               "tool_outcome_unknown", "tool_caller_detached", "stale_result_suppressed",
               "duplicate_coalesced", "approval_revoked", "approval_checked",
               "external_api_started", "external_api_completed", "session_closed", "agent_ready", "gemini_connection", "workspace_items", "local_connection", "local_model_metrics", "mock_calendar_receipt", "assistant_claim_unverified", "call_activation"}
    selected = [json.loads(line) for line in lines[after:] if line]
    return {"events": [e for e in selected if e.get("event") in allowed], "cursor": len(lines)}


@app.get("/google/status")
async def google_status():
    Settings.from_env()
    async with GoogleWorkspace() as google:
        return google.status()


@app.get("/google/connect")
async def google_connect():
    Settings.from_env()
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    async with GoogleWorkspace() as google:
        try:
            target = google.authorization_url(state, challenge)
        except GoogleAPIError as exc:
            raise HTTPException(503, str(exc)) from None
    for expired in [key for key, (_, deadline) in oauth_states.items() if deadline < time.time()]:
        oauth_states.pop(expired, None)
    oauth_states[state] = (verifier, time.time() + 600)
    response = RedirectResponse(target, status_code=303)
    response.set_cookie("reprise_oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return response


@app.get("/google/callback")
async def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    cookie = request.cookies.get("reprise_oauth_state", "")
    if not state or not cookie or not secrets.compare_digest(state, cookie):
        raise HTTPException(400, "Google connection state did not match. Start Connect Google again.")
    saved = oauth_states.pop(state, None)
    if not saved or saved[1] < time.time() or error or not code:
        raise HTTPException(400, "Google connection expired or was cancelled. Start Connect Google again.")
    async with GoogleWorkspace() as google:
        try:
            await google.exchange_code(code, saved[0])
        except GoogleAPIError as exc:
            raise HTTPException(503, str(exc)) from None
    response = RedirectResponse("/?google=connected", status_code=303)
    response.delete_cookie("reprise_oauth_state")
    return response


def check_room(room):
    if room not in issued or not re.fullmatch(r"reprise-demo-[0-9a-f]{14}", room):
        raise HTTPException(404)


@app.post("/local/{room}/message", status_code=202)
async def local_message(room: str, message: LocalMessage):
    check_room(room)
    target = local_sessions.get(room)
    if not target or not message.text.strip():
        raise HTTPException(400, "No local session or empty message")
    try:
        target.submit(message.text.strip())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"status": "queued"}


@app.post("/local/{room}/end", status_code=202)
async def local_end(room: str):
    check_room(room)
    target = local_sessions.pop(room, None)
    if target:
        target.closed = True  # Revoke generation authority before scheduling shutdown.
        task = asyncio.create_task(target.close())
        closing_sessions.add(task)
        task.add_done_callback(closing_sessions.discard)
    return {"status": "closing"}


@app.get("/plans/{room}")
async def latest_plan(room: str):
    check_room(room)
    try:
        plan = PlanStore().get(room)
        plan["requires_confirmation"] = pending_confirmation(plan)
        return {"plan": plan}
    except ValueError:
        return {"plan": None}


@app.post("/plans/{room}/{plan_id}/approve")
async def approve_plan(room: str, plan_id: str, approval: PlanApproval | None = None):
    check_room(room)
    try:
        plan = PlanStore().approve(room, plan_id, expected_revision=approval.expected_revision if approval else None)
        plan["requires_confirmation"] = pending_confirmation(plan)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"plan": plan}
