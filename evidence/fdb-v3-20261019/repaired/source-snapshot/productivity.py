"""Review and execute real Calendar, Tasks, Gmail and shopping actions."""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .catalog import S, ToolSpec
from .config import ROOT
from .google_workspace import GoogleAPIError, GoogleWorkspace, MutationRevoked


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
        cache_result=False,
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
        cache_result=False,
    ),
    ToolSpec(
        "execute_plan",
        "Execute a reviewed plan only after the user confirms it. Returns actual Google IDs and per-action failures; never interpret approval_required as completion.",
        {"plan_id": S},
        ("plan_id",),
        True,
        cache_result=False,
    ),
    ToolSpec(
        "get_plan",
        "Read the plan and its actual execution status, including Google IDs.",
        {"plan_id": S},
        ("plan_id",),
        cache_result=False,
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
        if row[0] == "running" and value.get("lease_expires_at", 0) <= time.time():
            self.recover_expired(room)
            return self.get(room, plan_id)
        return value

    def prepare(self, room, title, actions, revision=0):
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
            "revision": revision,
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

    def invalidate(self, room, revision):
        """Revoke uncommitted plans atomically, including dashboard approvals."""
        invalidated = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT document,status FROM plans WHERE room=? AND status IN ('prepared','running')", (room,)
            ).fetchall()
            for document, status in rows:
                value = json.loads(document)
                value.update(approved=False, revoked_at_revision=revision)
                if status == "prepared":
                    value["status"] = "superseded"
                else:
                    value["cancel_requested"] = True
                db.execute("UPDATE plans SET status=?,document=? WHERE id=?",
                           (value["status"], json.dumps(value), value["plan_id"]))
                invalidated.append(value)
        return invalidated

    def save(self, value):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,document FROM plans WHERE id=? AND room=?",
                             (value["plan_id"], value["room"])).fetchone()
            if not row:
                return False
            current = json.loads(row[1])
            if value.get("lease_owner") and (
                current.get("lease_owner") != value["lease_owner"] or row[0] == "needs_review"
            ):
                return False
            if value.get("lease_owner"):
                value["lease_expires_at"] = max(value.get("lease_expires_at", 0),
                                                current.get("lease_expires_at", 0))
            if current.get("cancel_requested"):
                value.update(cancel_requested=True, approved=False)
            db.execute(
                "UPDATE plans SET status=?,document=? WHERE id=? AND room=?",
                (value["status"], json.dumps(value), value["plan_id"], value["room"]),
            )
            return True

    def recover_expired(self, room):
        """No replay: a dead owner's dispatched write may already exist in Google."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT document FROM plans WHERE room=? AND status='running'",
                              (room,)).fetchall()
            for (document,) in rows:
                value = json.loads(document)
                if value.get("lease_expires_at", 0) > time.time():
                    continue
                value.update(status="needs_review", approved=False, recovered_at=time.time())
                for action in value["actions"]:
                    if action["status"] == "running":
                        action.update(status="unknown", error="Worker lease expired; verify in Google before any new write.")
                    elif action["status"] == "pending":
                        action["status"] = "not_executed"
                db.execute("UPDATE plans SET status='needs_review',document=? WHERE id=?",
                           (json.dumps(value), value["plan_id"]))

    def renew(self, room, plan_id, owner):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,document FROM plans WHERE room=? AND id=?",
                             (room, plan_id)).fetchone()
            if not row or row[0] != "running":
                return False
            value = json.loads(row[1])
            if value.get("lease_owner") != owner or value.get("lease_expires_at", 0) <= time.time():
                return False
            value["lease_expires_at"] = time.time() + 60
            db.execute("UPDATE plans SET document=? WHERE id=?", (json.dumps(value), plan_id))
            return True

    def record_late_receipt(self, room, plan_id, owner, index, action):
        """Merge positive evidence only; an expired executor cannot revive a plan."""
        completed = action.get("status") == "completed" and action.get("google_id")
        if not completed and not action.get("list_receipt", {}).get("id"):
            return False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,document FROM plans WHERE room=? AND id=?",
                             (room, plan_id)).fetchone()
            if not row or row[0] != "needs_review":
                return False
            value = json.loads(row[1])
            current = value["actions"][index]
            if (value.get("lease_owner") != owner or current.get("operation_id") != action.get("operation_id")
                or current["status"] != "unknown"):
                return False
            if completed:
                current.update(action, late_receipt=True)
            else:
                current.update(list_receipt=action["list_receipt"],
                               target_list_id=action.get("target_list_id"), late_receipt=True)
            db.execute("UPDATE plans SET document=? WHERE id=?", (json.dumps(value), plan_id))
            return True

    def approve(self, room, plan_id, source="dashboard"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT document,status FROM plans WHERE id=? AND room=?", (plan_id, room)
            ).fetchone()
            if not row or row[1] != "prepared":
                raise ValueError("Only the current prepared plan can be approved.")
            value = json.loads(row[0])
            value["approved"] = True
            value["approved_at_revision"] = value.get("revision", 0)
            value["approval"] = {"id": uuid.uuid4().hex, "at": time.time(),
                                 "revision": value.get("revision", 0), "source": source}
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
            if not value["approved"] or value.get("approved_at_revision") != value.get("revision", 0):
                return None
            value["status"] = "running"
            value.update(lease_owner=uuid.uuid4().hex, claimed_at=time.time(),
                         lease_expires_at=time.time() + 60)
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
    requires_final_input = True

    def __init__(self, room, trace=None, google=None, store=None):
        self.room, self.trace = room, trace
        self.google = google or GoogleWorkspace(trace=trace)
        if trace:
            self.google.trace = trace
        self.store = store or PlanStore()
        self.store.recover_expired(room)
        self.latest_input = ""
        self.input_counter = 0
        self.prepared_after = {}
        self.revision = 0
        self.input_final = True

    def observe_input(self, text, *, is_final=True, revision=None):
        if text and text.strip():
            self.latest_input = text.strip()
            self.input_final = is_final
            self.revision = self.revision if revision is None else revision
            # Interim text may revoke approval, but can never grant it. Any
            # substantive follow-up requires a fresh review; acknowledgements do not.
            if not APPROVAL.fullmatch(text) and text.strip().casefold().strip('.!') not in {
                "mhm", "uhhuh", "okay", "ok", "right", "yeah", "thanks"
            }:
                invalidated = self.store.invalidate(self.room, self.revision)
                if self.trace:
                    for plan in invalidated:
                        self.trace.event("approval_revoked", plan_id=plan["plan_id"],
                                         approval=plan.get("approval"), status=plan["status"])
            if is_final:
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
                plan = self.store.prepare(self.room, args["title"], actions, self.revision)
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
        self.store.recover_expired(self.room)
        plan = self.store.get(self.room, plan_id)
        if plan["status"] != "prepared":
            return self.execution_result(plan)
        if self.input_final and self.input_counter > self.prepared_after.get(
            plan_id, self.input_counter
        ) and APPROVAL.fullmatch(self.latest_input):
            plan = self.store.approve(self.room, plan_id, source="voice")
        if not plan["approved"] or not self.input_final:
            return {
                "status": "approval_required",
                "message": "Review this plan and say 'confirm the plan', or press Confirm plan.",
                "plan": plan,
            }
        if self.trace:
            self.trace.event("approval_checked", plan_id=plan_id, approval=plan.get("approval"))
        if not self.google.status()["connected"]:
            return {
                "status": "error",
                "message": "Connect Google first. No Google actions have been sent.",
                "plan": plan,
            }
        plan = self.store.claim(self.room, plan_id)
        if plan is None:
            return self.execution_result(self.store.get(self.room, plan_id))
        heartbeat = asyncio.create_task(self._heartbeat(plan))
        try:
            if self.trace:
                with self.trace.bind(plan_id=plan_id):
                    return await self._execute_claimed(plan)
            return await self._execute_claimed(plan)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, plan):
        while True:
            await asyncio.sleep(20)
            if not self.store.renew(self.room, plan["plan_id"], plan["lease_owner"]):
                return

    def execution_result(self, plan):
        result = self.publish(plan)
        result["status"] = "success" if plan["status"] == "completed" else plan["status"]
        return result

    async def _execute_claimed(self, plan):
        plan_id = plan["plan_id"]
        self.publish(plan)
        for index, action in enumerate(plan["actions"]):
            current = self.store.get(self.room, plan_id)
            if current.get("cancel_requested") or not self.store.renew(self.room, plan_id, plan["lease_owner"]):
                break
            operation_id = hashlib.sha256(f"{plan_id}:{index}".encode()).hexdigest()[:32]
            action.update(status="running", operation_id=operation_id, started_at=time.time())
            action_started = time.monotonic()
            if not self.store.save(plan):
                return self.execution_result(self.store.get(self.room, plan_id))
            self.publish(plan)
            try:
                with self.google.authorize_writes(lambda: self._authorize_action(plan)):
                    if self.trace:
                        with self.trace.bind(operation_id=operation_id, action_index=index):
                            result = await self._send_action(plan, action, operation_id)
                    else:
                        result = await self._send_action(plan, action, operation_id)
                if not isinstance(result.get("id"), str) or not result["id"].strip():
                    raise ValueError("Missing Google receipt ID")
                action.update(
                    status="completed",
                    google_id=result["id"],
                    url=result.get("htmlLink", result.get("webViewLink", "")),
                    receipt={"id": result["id"], "etag": result.get("etag"),
                             **result.get("_http", {}), "received_at": time.time()},
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
                for remaining in plan["actions"][index + 1:]:
                    remaining["status"] = "not_executed"
                raise
            except MutationRevoked as exc:
                action.update(status="not_executed", error=str(exc))
                break
            except GoogleAPIError as exc:
                action.update(status="unknown" if exc.uncertain else "failed", error=str(exc),
                              error_receipt={"status_code": exc.status_code, "request_id": exc.request_id,
                                             "uncertain": exc.uncertain})
                if exc.uncertain:
                    break
            except (KeyError, ValueError, TypeError, AttributeError):
                action.update(
                    status="unknown",
                    error="Google returned an incomplete response. Check Google before retrying.",
                )
                break
            except Exception as exc:
                action.update(status="unknown", error=f"Execution failed ({type(exc).__name__}); verify in Google before retrying.")
                break
            finally:
                action["finished_at"] = time.time()
                action["duration_s"] = time.monotonic() - action_started
                if self.store.save(plan):
                    self.publish(plan)
                else:
                    self.store.record_late_receipt(self.room, plan_id, plan["lease_owner"], index, action)
                    self.publish(self.store.get(self.room, plan_id))
        current = self.store.get(self.room, plan_id)
        if current["status"] == "needs_review":
            return self.execution_result(current)
        for action in plan["actions"]:
            if action["status"] == "pending":
                action["status"] = "not_executed"
        plan["status"] = (
            "completed" if all(a["status"] == "completed" for a in plan["actions"]) else "partial"
        )
        self.store.save(plan)
        return self.execution_result(plan)

    def _authorize_action(self, plan):
        current = self.store.get(self.room, plan["plan_id"])
        if (current["status"] != "running" or current.get("cancel_requested")
            or not current.get("approved") or current.get("lease_owner") != plan["lease_owner"]):
            raise MutationRevoked("Approval or execution ownership changed before dispatch; no action was sent.")

    async def _send_action(self, plan, action, operation_id):
        if action["kind"] == "calendar":
            return await self.google.create_event(action, operation_id)
        if action["kind"] == "task":
            return await self.google.create_task(action)
        if action["kind"] == "email":
            return await self.google.create_email(action)
        listing = self.google.config.shopping_list_id or self.store.preference("shopping_list_id")
        if not listing:
            created = await self.google.create_task_list("Reprise Shopping")
            if not isinstance(created.get("id"), str) or not created["id"].strip():
                raise ValueError("Missing shopping list receipt")
            action["list_receipt"] = {"id": created["id"], **created.get("_http", {})}
            listing = self.store.preference("shopping_list_id", created["id"])
        action["target_list_id"] = listing
        if not self.store.save(plan):
            raise ValueError("Execution ownership expired")
        return await self.google.create_task(
            {"title": " ".join(filter(None, [action.get("quantity"), action["item"]])),
             "notes": action.get("notes", "")}, listing)

    async def close(self):
        await self.google.close()
