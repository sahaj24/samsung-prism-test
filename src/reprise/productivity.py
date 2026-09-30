"""Review and execute real Calendar, Tasks, Gmail and shopping actions."""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .catalog import S, ToolSpec
from .config import ROOT
from .google_workspace import GoogleAPIError, GoogleWorkspace


def records(properties, required):
    return {
        "type": "array",
        "maxItems": 30,
        "items": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


PRODUCTIVITY_TOOLS = [
    ToolSpec(
        "get_planning_context",
        "Get the actual current date, timezone and Google connection status before resolving relative dates.",
        {},
        (),
    ),
    ToolSpec(
        "prepare_plan",
        "Prepare a complete corrected plan for review. This does not write to Google. Include every requested action; exclude abandoned details. Replace an earlier unexecuted plan by preparing the corrected plan.",
        {
            "title": S,
            "calendar_events": records(
                {"title": S, "start": S, "end": S, "notes": S}, ["title", "start", "end"]
            ),
            "tasks": records({"title": S, "notes": S, "due_date": S}, ["title"]),
            "messages": records(
                {
                    "to": {"type": "array", "items": S, "minItems": 1},
                    "subject": S,
                    "body": S,
                    "delivery": {"type": "string", "enum": ["draft", "send"]},
                },
                ["to", "subject", "body", "delivery"],
            ),
            "shopping": records({"item": S, "quantity": S, "notes": S}, ["item"]),
        },
        ("title",),
    ),
    ToolSpec(
        "execute_plan",
        "Execute a reviewed plan only after the user confirms it. Returns actual Google IDs and per-action failures; never interpret approval_required as completion.",
        {"plan_id": S},
        ("plan_id",),
        True,
    ),
    ToolSpec(
        "get_plan",
        "Read the plan and its actual execution status, including Google IDs.",
        {"plan_id": S},
        ("plan_id",),
    ),
]

APPROVAL = re.compile(
    r"\s*(?:yes[,.! ]*)?(?:yes|do it|do that|go ahead|go ahead and do it|"
    r"confirm(?: the plan)?|approve(?: the plan)?|execute(?: the plan)?|"
    r"proceed|looks good|that'?s correct)[.! ]*\s*",
    re.IGNORECASE,
)


class PlanStore:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / "runs/productivity.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, room TEXT, status TEXT, document TEXT, created REAL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT)")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, room, plan_id=None):
        with self.connect() as db:
            row = db.execute(
                "SELECT status, document FROM plans WHERE room=? "
                + ("AND id=?" if plan_id else "ORDER BY created DESC LIMIT 1"),
                (room, plan_id) if plan_id else (room,),
            ).fetchone()
        if not row:
            raise ValueError("No matching plan in this conversation.")
        value = json.loads(row[1])
        value["status"] = row[0]
        return value

    def prepare(self, room, title, actions):
        plan_id = hashlib.sha256(
            json.dumps([room, title, actions], sort_keys=True).encode()
        ).hexdigest()[:24]
        try:
            existing = self.get(room, plan_id)
            if existing["status"] != "superseded":
                return existing
        except ValueError:
            pass
        value = {
            "plan_id": plan_id,
            "room": room,
            "title": title,
            "status": "prepared",
            "approved": False,
            "actions": [{**a, "status": "pending"} for a in actions],
        }
        with self.connect() as db:
            db.execute(
                "UPDATE plans SET status='superseded' WHERE room=? AND status='prepared'", (room,)
            )
            db.execute(
                "INSERT OR REPLACE INTO plans VALUES (?,?,?,?,?)",
                (plan_id, room, "prepared", json.dumps(value), time.time()),
            )
        return value

    def save(self, value):
        with self.connect() as db:
            db.execute(
                "UPDATE plans SET status=?,document=? WHERE id=? AND room=?",
                (value["status"], json.dumps(value), value["plan_id"], value["room"]),
            )

    def approve(self, room, plan_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT document,status FROM plans WHERE id=? AND room=?", (plan_id, room)
            ).fetchone()
            if not row or row[1] != "prepared":
                raise ValueError("Only the current prepared plan can be approved.")
            value = json.loads(row[0])
            value["approved"] = True
            db.execute("UPDATE plans SET document=? WHERE id=?", (json.dumps(value), plan_id))
        return value

    def claim(self, room, plan_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,document FROM plans WHERE id=? AND room=?", (plan_id, room)
            ).fetchone()
            if not row or row[0] != "prepared":
                return None
            value = json.loads(row[1])
            if not value["approved"]:
                return None
            value["status"] = "running"
            db.execute(
                "UPDATE plans SET status='running',document=? WHERE id=?",
                (json.dumps(value), plan_id),
            )
            return value

    def preference(self, key, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO preferences VALUES (?,?)", (key, value))
                return value
            row = db.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
            return row[0] if row else None


def validate_plan(args, time_zone):
    zone = ZoneInfo(time_zone)
    actions = []
    for event in args.get("calendar_events", []):
        start, end = (
            datetime.fromisoformat(event[key].replace("Z", "+00:00")) for key in ("start", "end")
        )
        if not start.tzinfo or not end.tzinfo or end <= start:
            raise ValueError("Calendar start and end need timezone offsets, with end after start.")
        if any(t.utcoffset() != t.astimezone(zone).utcoffset() for t in (start, end)):
            raise ValueError(f"Calendar offsets must match {time_zone} on the event date.")
        actions.append({"kind": "calendar", **event})
    for task in args.get("tasks", []):
        if task.get("due_date"):
            date.fromisoformat(task["due_date"])
        actions.append({"kind": "task", **task})
    for message in args.get("messages", []):
        if any(
            not re.fullmatch(r"[^\s@,<>]+@[^\s@,<>]+\.[^\s@,<>]+", address)
            for address in message["to"]
        ):
            raise ValueError(
                "Ask for the recipient's actual email address; never guess from a name."
            )
        if "\r" in message["subject"] or "\n" in message["subject"]:
            raise ValueError("Email subjects must be a single line.")
        actions.append({"kind": "email", **message})
    actions.extend({"kind": "shopping", **item} for item in args.get("shopping", []))
    if not actions:
        raise ValueError("The plan needs at least one requested action.")
    return actions


class ProductivityBackend:
    def __init__(self, room, trace=None, google=None, store=None):
        self.room, self.trace = room, trace
        self.google = google or GoogleWorkspace()
        self.store = store or PlanStore()
        self.latest_input = ""
        self.input_counter = 0
        self.prepared_after = {}

    def observe_input(self, text):
        if text and text.strip():
            self.latest_input = text.strip()
            self.input_counter += 1

    def publish(self, plan):
        if self.trace:
            self.trace.event("plan_updated", plan=plan)
        return {"status": "success", "plan": plan}

    async def prepare(self, name):
        await asyncio.sleep(0)

    async def call(self, name, args):
        try:
            if name == "get_planning_context":
                now = datetime.now(ZoneInfo(self.google.config.time_zone))
                return {
                    "status": "success",
                    "now": now.isoformat(),
                    "time_zone": self.google.config.time_zone,
                    "google": self.google.status(),
                    "shopping_destination": "Google Tasks shopping list",
                    "task_due_dates": "Google Tasks stores a date only; keep a requested time in notes.",
                    "message_delivery": "Send only when explicitly requested and confirmed; otherwise create a draft.",
                }
            if name == "prepare_plan":
                actions = validate_plan(args, self.google.config.time_zone)
                plan = self.store.prepare(self.room, args["title"], actions)
                self.prepared_after[plan["plan_id"]] = self.input_counter
                return self.publish(plan)
            if name == "get_plan":
                return self.publish(self.store.get(self.room, args["plan_id"]))
            if name == "execute_plan":
                return await self.execute(args["plan_id"])
            raise ValueError("Unsupported planning tool.")
        except (ValueError, GoogleAPIError) as exc:
            return {"status": "error", "message": str(exc)}

    async def execute(self, plan_id):
        plan = self.store.get(self.room, plan_id)
        if plan["status"] != "prepared":
            return self.publish(plan)
        if self.input_counter > self.prepared_after.get(
            plan_id, self.input_counter
        ) and APPROVAL.fullmatch(self.latest_input):
            self.store.approve(self.room, plan_id)
            plan["approved"] = True
        if not plan["approved"]:
            return {
                "status": "approval_required",
                "message": "Review this plan and say 'confirm the plan', or press Confirm plan.",
                "plan": plan,
            }
        if not self.google.status()["connected"]:
            return {
                "status": "error",
                "message": "Connect Google first. No Google actions have been sent.",
                "plan": plan,
            }
        plan = self.store.claim(self.room, plan_id)
        if plan is None:
            return self.publish(self.store.get(self.room, plan_id))
        self.publish(plan)
        for index, action in enumerate(plan["actions"]):
            action["status"] = "running"
            self.store.save(plan)
            self.publish(plan)
            try:
                if action["kind"] == "calendar":
                    operation_id = hashlib.sha256(f"{plan_id}:{index}".encode()).hexdigest()[:32]
                    result = await self.google.create_event(action, operation_id)
                elif action["kind"] == "task":
                    result = await self.google.create_task(action)
                elif action["kind"] == "email":
                    result = await self.google.create_email(action)
                else:
                    listing = self.google.config.shopping_list_id or self.store.preference(
                        "shopping_list_id"
                    )
                    if not listing:
                        created = await self.google.create_task_list("Reprise Shopping")
                        listing = self.store.preference("shopping_list_id", created["id"])
                    result = await self.google.create_task(
                        {
                            "title": " ".join(
                                filter(None, [action.get("quantity"), action["item"]])
                            ),
                            "notes": action.get("notes", ""),
                        },
                        listing,
                    )
                action.update(
                    status="completed",
                    google_id=result["id"],
                    url=result.get("htmlLink", result.get("webViewLink", "")),
                )
                if action["kind"] == "email":
                    action["delivery_result"] = (
                        "sent" if action["delivery"] == "send" else "draft_created"
                    )
            except asyncio.CancelledError:
                action.update(
                    status="unknown",
                    error="Execution was interrupted; verify this action in Google before retrying.",
                )
                plan["status"] = "partial"
                raise
            except GoogleAPIError as exc:
                action.update(status="unknown" if exc.uncertain else "failed", error=str(exc))
                if exc.uncertain:
                    break
            except (KeyError, ValueError):
                action.update(
                    status="unknown",
                    error="Google returned an incomplete response. Check Google before retrying.",
                )
                break
            finally:
                self.store.save(plan)
                self.publish(plan)
        plan["status"] = (
            "completed" if all(a["status"] == "completed" for a in plan["actions"]) else "partial"
        )
        self.store.save(plan)
        return self.publish(plan)

    async def close(self):
        await self.google.close()
