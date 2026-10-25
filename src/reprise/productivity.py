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
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .catalog import S, ToolSpec
from .config import ROOT
from .google_workspace import GoogleAPIError, GoogleWorkspace, MutationRevoked
from .intent import GOAL_RESET, IMPLICIT_CORRECTION, affected_indices
from .workspace_management import CHANGE_FIELDS, MANAGEMENT_NAMES, MANAGEMENT_TOOLS, REMINDERS, TASK_STATUS, WorkspaceManagement, event_time, reminder_values


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


PRODUCTIVITY_TOOLS = MANAGEMENT_TOOLS + [
    ToolSpec("search_gmail", "Search the authorized mailbox using Gmail search syntax. Returns up to five message sources, not booking details.",
             {"query": S}, ("query",), cache_result=False),
    ToolSpec("get_gmail_message", "Read a message selected from Gmail search. Extract booking facts only from its actual text and cite its source. Missing facts are unknown.",
             {"message_id": S}, ("message_id",), cache_result=False),
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
                {"title": S, "start": S, "end": S, "notes": S,
                 "attendees": {"type": "array", "items": S, "maxItems": 30}, "reminders": REMINDERS, "location": {"type":"string"}}, ["title", "start", "end"]
            ),
            "tasks": records({"title": S, "notes": S, "due_date": S, "task_status": TASK_STATUS, "task_list_id": S}, ["title"]),
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
        "Execute settled personal actions automatically. Sends, invitations and deletions require contextual confirmation. Completed actions are never repeated; approval_required is not completion.",
        {"plan_id": S},
        ("plan_id",),
        True,
        cache_result=False,
    ),
    ToolSpec(
        "prepare_change",
        "Correct or undo a saved Calendar/Tasks action using its plan ID and zero-based action index. Never recreate a changed event. Deletion requires confirmation. No sent-email undo.",
        {"plan_id": S, "action_index": {"type": "integer", "minimum": 0},
         "operation": {"type": "string", "enum": ["update", "delete"]},
         "changes": {"type": "object", "properties": CHANGE_FIELDS, "additionalProperties": False}},
        ("plan_id", "action_index", "operation"), cache_result=False,
    ),
    ToolSpec(
        "reconcile_plan", "Read remote state for uncertain actions. Never retries a write. Use before further correction or undo after an unknown outcome.",
        {"plan_id": S}, ("plan_id",), cache_result=False,
    ),
    ToolSpec(
        "get_plan",
        "Read the plan and its actual execution status, including Google IDs.",
        {"plan_id": S},
        ("plan_id",),
        cache_result=False,
    ),
]

# Runtime policy metadata only: public Gemini/Gemma JSON tool declarations are unchanged.
ACTION_SCOPE = {
    "calendar": ("calendar", "event", "meeting", "appointment"),
    "task": ("task", "todo"), "email": ("email", "mail", "message", "draft"),
    "shopping": ("shopping", "item"),
}
READ_KIND = {"search_gmail": "email", "get_gmail_message": "email",
             "list_tasks": "task", "list_task_lists": "task", "list_calendar_events": "calendar"}
PRODUCTIVITY_TOOLS = [replace(s, intent_scope=ACTION_SCOPE[READ_KIND[s.name]], reuse_for_s=15)
                      if s.name in READ_KIND else s for s in PRODUCTIVITY_TOOLS]

APPROVAL = re.compile(
    r"\s*(?:yes[,.! ]*)?(?:yes|yeah|sure|okay|ok|please do|do it|do that|go ahead|go ahead and do it|"
    r"confirm(?: the plan)?|approve(?: the plan)?|execute(?: the plan)?|"
    r"proceed|looks good|that'?s correct)[.! ]*\s*",
    re.IGNORECASE,
)


def requires_confirmation(action):
    """Fail closed for unknown capabilities; personal reversible writes are automatic."""
    if action.get("owner_confirmation_required"):
        return True
    if action.get("operation") == "delete" or action.get("consequential"):
        return True
    if action["kind"] == "email":
        return action.get("delivery") != "draft"
    if action["kind"] == "calendar":
        return bool(action.get("attendees") or action.get("target_attendees"))
    return action["kind"] not in {"task", "shopping", "alarm"}


def pending_confirmation(plan):
    return any(requires_confirmation(a) and a["status"] in {"pending", "approval_required"}
               for a in plan["actions"]) and not plan.get("approved")


class PlanStore:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / "runs/productivity.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, room TEXT, status TEXT, document TEXT, created REAL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS resource_owners (resource TEXT PRIMARY KEY, plan_id TEXT, action_index INTEGER)")
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
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document,status FROM plans WHERE id=? AND room=?",
                             (plan_id, room)).fetchone()
            if row and row[1] != "superseded":
                return {**json.loads(row[0]), "status": row[1]}
            # Transfer overlapping *pending* work atomically. A complete revised
            # plan may repeat unchanged actions; only its new owner may send them.
            # Unrelated actions and all dispatched receipts keep their ownership.
            rows = db.execute("SELECT document,status FROM plans WHERE room=? AND status IN ('prepared','running')",
                              (room,)).fetchall()
            for document, status in rows:
                old = json.loads(document)
                selected = set()
                for index, action in enumerate(old["actions"]):
                    if not any(all(action.get(k) == v for k, v in new.items()) for new in actions):
                        continue
                    if action["status"] in {"running", "unknown", "completed"}:
                        raise ValueError("A matching action was already dispatched in plan " + old["plan_id"] + "; inspect its outcome and prepare only remaining actions.")
                    if action["status"] in {"pending", "approval_required"}:
                        selected.add(index)
                        action["status"] = "not_executed" if status == "running" else "superseded"
                if not selected:
                    continue
                old.update(approved=False, revoked_at_revision=revision,
                           revoked_actions=sorted(set(old.get("revoked_actions", [])) | selected))
                if status == "prepared" and not any(
                    a["status"] in {"pending", "approval_required"} for a in old["actions"]
                ):
                    old["status"] = "superseded" if all(a["status"] == "superseded" for a in old["actions"]) else "partial"
                db.execute("UPDATE plans SET status=?,document=? WHERE id=?",
                           (old["status"], json.dumps(old), old["plan_id"]))
            db.execute(
                "INSERT OR REPLACE INTO plans VALUES (?,?,?,?,?)",
                (plan_id, room, "prepared", json.dumps(value), time.time()),
            )
        return value

    def pending(self, room):
        with self.connect() as db:
            row = db.execute("SELECT id FROM plans WHERE room=? AND status='prepared' ORDER BY created DESC LIMIT 1", (room,)).fetchone()
        if not row:
            raise ValueError("No pending action in this conversation.")
        return self.get(room, row[0])

    def invalidate(self, room, revision, select=None):
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
                selected = set(range(len(value["actions"]))) if select is None else select(value["actions"])
                value["revoked_actions"] = sorted(set(value.get("revoked_actions", [])) | selected)
                for index, action in enumerate(value["actions"]):
                    if index in selected and action["status"] in {"pending", "approval_required"}:
                        action["status"] = "not_executed" if status == "running" else "superseded"
                if status == "prepared" and not any(
                    a["status"] in {"pending", "approval_required"} for a in value["actions"]
                ):
                    value["status"] = (
                        "superseded" if all(a["status"] == "superseded" for a in value["actions"])
                        else "partial"
                    )
                elif status == "running" and select is None:
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
            if current.get("revoked_at_revision") is not None:
                value["revoked_at_revision"] = current["revoked_at_revision"]
                value["revoked_actions"] = current.get("revoked_actions", [])
                if not current.get("approved"):
                    value["approved"] = False
                for index in value["revoked_actions"]:
                    if value["actions"][index]["status"] in {"pending", "approval_required"}:
                        value["actions"][index]["status"] = current["actions"][index]["status"]
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

    def approve(self, room, plan_id, source="dashboard", expected_revision=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT document,status FROM plans WHERE id=? AND room=?", (plan_id, room)
            ).fetchone()
            if not row or row[1] != "prepared":
                raise ValueError("Only the current prepared plan can be approved.")
            value = json.loads(row[0])
            if expected_revision is not None and value.get("revision", 0) != expected_revision:
                raise ValueError("The plan revision changed; review the current event.")
            if any(a.get("owner_confirmation_required") for a in value["actions"]) and (source != "dashboard" or expected_revision is None):
                raise ValueError("The owner must confirm the current revision using Owner confirm; shared microphone speech cannot authorize a write.")
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
            approved = value["approved"] and value.get("approved_at_revision") == value.get("revision", 0)
            if not approved and not any(
                a["status"] == "pending" and not requires_confirmation(a) for a in value["actions"]
            ):
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

    def has_resource_owner(self, resource):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM resource_owners WHERE resource=?", (resource,)).fetchone() is not None

    def claim_resource(self, resource, plan, index, aliases=()):
        """Serialize a particular remote resource, including across backend instances."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT p.status,p.document,r.action_index FROM resource_owners r JOIN plans p ON p.id=r.plan_id WHERE r.resource IN (" + ",".join("?" for _ in (resource, *aliases)) + ")", (resource, *aliases)).fetchall()
            for row in rows:
                owner = json.loads(row[1])
                action = owner["actions"][row[2]]
                if owner["plan_id"] != plan["plan_id"] and (
                    action["status"] == "unknown" or
                    (action["status"] == "running" and owner.get("lease_expires_at", 0) > time.time())
                    or (action["status"] == "running" and row[0] == "running")
                ):
                    raise MutationRevoked("A prior operation on this resource is running or uncertain; reconcile plan " + owner["plan_id"] + " first. No new write was sent.")
            db.execute("INSERT OR REPLACE INTO resource_owners VALUES (?,?,?)", (resource, plan["plan_id"], index))

    def remember_resource(self, target, values=None, deleted=False, expected=None):
        """Keep original receipts immutable; record the verified current resource separately."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM plans WHERE id=? AND room=?", (target["plan_id"], target["room"])).fetchone()
            if not row:
                return
            plan = json.loads(row[0])
            action = plan["actions"][target["action_index"]]
            if ((action.get("resource_deleted") and not deleted) or
                (expected is not None and not same_values(action.get("current_values") or action_values(action), expected))):
                return
            if (deleted != bool(action.get("resource_deleted")) or
                not same_values(action.get("current_values") or action_values(action), values or {})):
                action["resource_revision"] = action.get("resource_revision", 0) + 1
            action.update(resource_deleted=deleted, current_values=values or {},
                          resource_result=("deleted" if target.get("origin") == "lookup" else "undone") if deleted else "updated", resource_verified_at=time.time())
            db.execute("UPDATE plans SET document=? WHERE id=?", (json.dumps(plan), plan["plan_id"]))

    def has_dispatched_kind(self, room, kinds):
        with self.connect() as db:
            rows = db.execute("SELECT document FROM plans WHERE room=?", (room,)).fetchall()
        return any(a.get("operation") != "lookup" and a["kind"] in kinds and a["status"] in {"running", "unknown", "completed"}
                   for (document,) in rows for a in json.loads(document)["actions"])

    def merge_reconciled(self, plan):
        """Merge confirmed reads only; never overwrite a concurrent executor or newer receipt."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,document FROM plans WHERE id=? AND room=?", (plan["plan_id"], plan["room"])).fetchone()
            if not row or row[0] == "running":
                return
            current = json.loads(row[1])
            for index, checked in enumerate(plan["actions"]):
                stored = current["actions"][index]
                if (stored["status"] == "unknown" and checked["status"] == "completed"
                    and stored.get("operation_id") == checked.get("operation_id")):
                    current["actions"][index] = checked
            if all(a["status"] == "completed" for a in current["actions"]):
                current["status"] = "completed"
            db.execute("UPDATE plans SET status=?,document=? WHERE id=?", (current["status"], json.dumps(current), current["plan_id"]))


def validate_plan(args, time_zone):
    zone = ZoneInfo(time_zone)
    actions = []
    for event in args.get("calendar_events", []):
        if any(not re.fullmatch(r"[^\s@,<>]+@[^\s@,<>]+\.[^\s@,<>]+", email)
               for email in event.get("attendees", [])):
            raise ValueError("Use actual email addresses for calendar guests.")
        all_day = all(len(event[key]) == 10 for key in ("start", "end"))
        if all_day:
            start, end = (date.fromisoformat(event[key]) for key in ("start", "end"))
        else:
            start, end = (datetime.fromisoformat(event[key].replace("Z", "+00:00")) for key in ("start", "end"))
            if not start.tzinfo or not end.tzinfo:
                raise ValueError("Calendar times need timezone offsets.")
            if any(t.utcoffset() != t.astimezone(zone).utcoffset() for t in (start, end)):
                raise ValueError(f"Calendar offsets must match {time_zone} on the event date.")
        if end <= start:
            raise ValueError("Calendar end must follow start (all-day end date is exclusive).")
        if "reminders" in event:
            reminder_values(event["reminders"])
        actions.append({"kind": "calendar", **event})
    for task in args.get("tasks", []):
        if task.get("task_status", "needsAction") not in {"needsAction", "completed"}:
            raise ValueError("Task status must be needsAction or completed.")
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


def action_values(action):
    keys = ("title", "start", "end", "notes", "attendees", "reminders", "location") if action["kind"] == "calendar" else ("title", "notes", "due_date", "task_status")
    defaults = {"attendees": [], "reminders": {"useDefault": True}, "task_status": "needsAction"}
    return {key: action.get(key, defaults.get(key, "")) for key in keys}


def remote_values(kind, resource):
    if kind == "calendar":
        return {"title": resource.get("summary", ""), "notes": resource.get("description", ""),
                "start": resource.get("start", {}).get("dateTime", resource.get("start", {}).get("date", "")),
                "end": resource.get("end", {}).get("dateTime", resource.get("end", {}).get("date", "")),
                "attendees": [a["email"] for a in resource.get("attendees", []) if a.get("email")],
                "reminders": reminder_values(resource.get("reminders")), "location": resource.get("location", "")}
    return {"title": resource.get("title", ""), "notes": resource.get("notes", ""),
            "due_date": (resource.get("due") or "")[:10], "task_status": resource.get("status", "needsAction")}


def same_values(left, right):
    def canonical(values):
        values = dict(values)
        for key in ("start", "end"):
            if values.get(key):
                values[key] = datetime.fromisoformat(values[key].replace("Z", "+00:00"))
        if "reminders" in values:
            values["reminders"] = reminder_values(values["reminders"])
        if "attendees" in values:
            values["attendees"] = sorted(email.casefold() for email in values["attendees"])
        return values
    return canonical(left) == canonical(right)


class ProductivityBackend:
    requires_final_input = True
    recovery_limit = 3

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
        self.confirmation_context = None
        self.management = WorkspaceManagement(self)
        self.editing_kinds = set()
        self.creation_exceptions = {}
        self.response_report = []
        self.response_reports = {}

    def response_correction(self, text):
        """Conservative check of explicit operation claims, not a semantic judge.

        Native audio can precede this transcript. The caller keeps the actual
        transcript and requests a correction; it never relabels already heard audio.
        """
        claims = re.finditer(
            r"\b(?:i(?:['’]ve| have)?|we(?:['’]ve| have)?)(?: successfully)?\s+"
            r"(created|added|updated|deleted|removed|scheduled|rescheduled|moved|changed|sent|completed)\b|"
            r"\b(?:has|have) been (created|added|updated|deleted|removed|scheduled|rescheduled|moved|changed|sent|completed)\b",
            text or "", re.I)
        unsupported = False
        facts = [(turn, r) for turn, rows in self.response_reports.values() for r in rows]
        generic = {"your", "the", "a", "an", "new", "existing", "all", "requested", "that", "this", "it", "them", "for", "of", "time", "notes", "date", "status", "details", "now", "successfully"}
        for claim in claims:
            verb = next(v for v in claim.groups() if v).casefold()
            clause = re.split(r"[.!?;]|\b(?:but|shall|should)\b", text[claim.end():], maxsplit=1, flags=re.I)[0]
            named = [r for _, r in facts if r["title"].casefold() not in generic | {"task", "event", "email"}
                     and re.search(r"(?<!\w)" + re.escape(r["title"]) + r"(?!\w)", clause, re.I)]
            kind_clause = clause
            for row in named:
                kind_clause = re.sub(r"(?<!\w)" + re.escape(row["title"]) + r"(?!\w)", "", kind_clause, flags=re.I)
            kind = re.search(r"\b(tasks?|events?|calendar|drafts?|emails?|messages?)\b", kind_clause, re.I)
            kinds = ({"task"} if kind and kind.group(0).lower().startswith("task") else
                     {"calendar"} if kind and kind.group(0).lower() in {"calendar", "event", "events"} else
                     {"email"} if kind else {"task", "calendar", "email", "shopping"})
            if named:
                candidates = [r for r in named if r["kind"] in kinds]
            else:
                prefix = kind_clause[:kind.start()] if kind else kind_clause
                unknown_name = set(re.findall(r"[a-z]+", prefix.casefold())) - generic
                candidates = [r for turn, r in facts if turn == self.input_counter and r["kind"] in kinds] if not unknown_name else []
            confirmed = [r for r in candidates if r["confirmed"]]
            operations = {r["operation"] for r in confirmed}
            if verb == "completed":
                valid = bool(confirmed) and len(confirmed) == len(candidates)
                if re.search(r"\btasks?\b", clause, re.I):
                    valid = valid and any(r["operation"] in {"create", "update"} and r.get("task_status") == "completed" for r in confirmed)
            elif verb == "sent":
                valid = any(r.get("delivery_result") == "sent" for r in confirmed)
            else:
                operation = "delete" if verb in {"deleted", "removed"} else "update" if verb in {"updated", "changed", "moved", "rescheduled"} else "create"
                valid = operation in operations
            unsupported |= not valid
        if re.search(r"\b(?:still working|working on deleting|slight delay|update you shortly)\b", text or "", re.I):
            unsupported |= not any(r["status"] == "running" for _, r in facts)
        if unsupported:
            return "I cannot confirm that claimed action. " + " ".join(r["summary"] for r in self.response_report) + " Please clarify or confirm the pending action before I proceed."
        return None

    def _observe_resource_intent(self, text, is_final):
        if GOAL_RESET.search(text) and is_final:
            self.editing_kinds.clear()
            self.creation_exceptions.clear()
        edited, created = set(), {}
        for clause in re.split(r"\b(?:and|also|then)\b|[.;]", text, flags=re.I):
            kinds = {kind for kind, labels in ACTION_SCOPE.items() if kind in {"task", "calendar"}
                     and any(re.search(r"\b" + label + r"s?\b", clause, re.I) for label in labels)}
            creating = bool(re.search(
                r"\b(?:create|add|make)\s+(?:(?:a|an|one|two|three|few|some|another|new|separate)\s+)*(?:tasks?|todos?|events?|calendar event)\b|\badd\b.+\bto (?:my |the )?tasks\b",
                clause, re.I))
            if re.search(r"\bschedule\b", clause, re.I) and not re.search(r"\b(?:that|existing)\b", clause, re.I):
                creating, kinds = True, {"calendar"}
            if creating:
                created.update({kind: clause for kind in kinds})
            elif re.search(r"\b(?:move|reschedule|rename|update|edit|change|delete|remove|complete|reopen|mark|fill)\b", clause, re.I):
                if not kinds:
                    group = self.management.latest_group or {}
                    kinds = {i["kind"] for i in group.get("items", [])} or self.editing_kinds
                edited.update(kinds)
            if re.search(r"\b(?:move|copy|put)\b.*\b(?:to|into|on) (?:the |my )?calendar\b", clause, re.I):
                edited.discard("calendar")  # Explicit task-to-calendar creation.
                created["calendar"] = clause
            if re.search(r"\bremind me to\b", clause, re.I):
                created["task"] = clause
        self.editing_kinds.update(edited)
        for kind in edited:
            self.creation_exceptions.pop(kind, None)
        if is_final:
            additive = bool(re.match(r"\s*(?:also|and|then|in addition)\b", text, re.I))
            for kind, clause in created.items():
                if kind in edited or (additive and kind in self.editing_kinds):
                    self.creation_exceptions[kind] = clause
                else:
                    self.editing_kinds.discard(kind)
                    self.creation_exceptions.pop(kind, None)

    def _guard_creation(self, actions):
        def explicit_new(action):
            clause = self.creation_exceptions.get(action["kind"], "")
            match = re.search(r"\b(?:tasks?|events?|calendar event)\s+(?:(?:to|called|named|titled|for)\s+)?(.+)|^\s*schedule\s+(.+)|\bremind me to\s+(.+)", clause, re.I)
            if not match or not action.get("title"):
                return False
            body = next(v for v in match.groups() if v).strip()
            # Match the new item's complete noun phrase, not a substring shared
            # with the edited item ("Exercise" vs "Buy Exercise equipment").
            return bool(re.match(re.escape(action["title"]) + r"(?:\s*$|[,.]|\s+(?:at|on|due|tomorrow|today|next)\b)", body, re.I))
        if any(a["kind"] in self.editing_kinds and a.get("operation", "create") == "create"
               and not explicit_new(a)
               for a in actions):
            raise ValueError("This request modifies an existing resource, not a new creation. Use list_tasks/list_calendar_events, resolve the user's selection, then prepare_resource_change and execute_plan on its real ID. Ask which item if ambiguous. No replacement was created.")

    def reference_is_locator(self, name, key, value):
        # Configured/listed containers remain read authority when an unrelated
        # plan containing the same ID is superseded. The Google adapter validates
        # membership again; an ID's occurrence inside a plan is not a dependency.
        return name == "list_tasks" and key == "task_list_id" and value in self.management.known_lists

    def suspend_pending(self, revision):
        self.confirmation_context = None
        self.revision = revision
        for plan in self.store.invalidate(self.room, revision, lambda actions: set(range(len(actions)))):
            self.publish(plan)

    def observe_input(self, text, *, is_final=True, revision=None):
        if text and text.strip():
            previous = self.latest_input
            if not APPROVAL.fullmatch(text):
                self.confirmation_context = None
            self.latest_input = text.strip()
            self.input_final = is_final
            self._observe_resource_intent(text, is_final)
            self.revision = self.revision if revision is None else revision
            # Interim text may revoke approval, but can never grant it. Any
            # substantive follow-up requires a fresh review; acknowledgements do not.
            if not APPROVAL.fullmatch(text) and text.strip().casefold().strip('.!') not in {
                "mhm", "uhhuh", "okay", "ok", "right", "yeah", "thanks"
            }:
                additive = bool(re.match(r"\s*(?:also|and|then|meanwhile|in addition)\b", text, re.I))
                def select(actions):
                    if additive and not (GOAL_RESET.search(text) or IMPLICIT_CORRECTION.search(text)
                                         or re.search(r"\bactually\b", text, re.I)):
                        return set()
                    return affected_indices(text, previous, [(a, ACTION_SCOPE.get(a["kind"], (a["kind"],))) for a in actions])
                invalidated = self.store.invalidate(self.room, self.revision, select)
                if self.trace:
                    for plan in invalidated:
                        if plan.get("approval"):
                            self.trace.event("approval_revoked", plan_id=plan["plan_id"],
                                             approval=plan.get("approval"), status=plan["status"])
                        self.publish(plan)
            if is_final:
                self.input_counter += 1
                if not APPROVAL.fullmatch(text) and text.strip().casefold().strip('.!') not in {"mhm", "uhhuh", "okay", "ok", "right", "yeah", "thanks"}:
                    self.management.observe_input(text)
                if APPROVAL.fullmatch(text) and self.confirmation_context:
                    plan_id, prompted_at = self.confirmation_context
                    if prompted_at == self.input_counter - 1:
                        try:
                            self.store.approve(self.room, plan_id, source="voice")
                        except ValueError:
                            pass  # A corrected, completed or already running plan cannot be approved.
                    self.confirmation_context = None

    def proposal_subject(self, name, args):
        if name in {"execute_plan", "get_plan", "reconcile_plan", "prepare_change"}:
            try:
                actions = self.store.get(self.room, args["plan_id"])["actions"]
                return actions, tuple(label for a in actions for label in ACTION_SCOPE.get(a["kind"], ()))
            except (KeyError, ValueError):
                pass
        return args, ()

    def can_reuse(self, name, args, result):
        if name in {"list_tasks", "list_calendar_events"}:
            # Never restore an old result group's selection/numbering over a newer lookup.
            ignored = {"task_list_id", "page_token"} if name == "list_tasks" else {"page_token"}
            key = (READ_KIND[name], json.dumps({k:v for k,v in args.items() if k not in ignored}, sort_keys=True))
            group = self.management.searches.get(key)
            return group is self.management.latest_group and group is not None and all(
                self.management.groups.get(item["resource_ref"]) is group for item in result.get("items", []))
        return True

    def invalidates_reads(self, name, args):
        if name != "execute_plan":
            return set()
        try:
            kinds = {a["kind"] for a in self.store.get(self.room, args["plan_id"])["actions"]
                     if a["status"] in {"pending", "running", "approval_required"}}
        except ValueError:
            return set()
        return {read for read, kind in READ_KIND.items() if kind in kinds} | {"get_workspace_item"}

    def observe_assistant(self, text, *, interrupted=False):
        """Bare affirmatives require an actual, action-specific confirmation question."""
        self.confirmation_context = None
        if interrupted or not text or not re.search(r"\?|\b(?:confirm|shall I|should I)\b", text, re.I):
            return
        try:
            plan = self.store.pending(self.room)
        except ValueError:
            return
        pending = [a for a in plan["actions"] if requires_confirmation(a)
                   and a["status"] in {"pending", "approval_required"}]
        # Bind the whole pending group only if every risky action was identified.
        def identified(a):
            verb = "delete|undo|remove" if a.get("operation") == "delete" else "send" if a["kind"] == "email" else "invite|invitation|update|change|move|reschedule|complete|reopen|mark"
            label = a.get("subject") or a.get("title", "")
            return (re.search(r"\b(?:" + verb + r")\b", text, re.I)
                    and label.casefold() in text.casefold()
                    and all(address.casefold() in text.casefold() for address in set(a.get("to", []) + a.get("attendees", []) + a.get("target_attendees", []))))
        if pending and all(identified(a) for a in pending):
            self.confirmation_context = (plan["plan_id"], self.input_counter)

    def publish(self, plan):
        plan["requires_confirmation"] = pending_confirmation(plan)
        if self.trace:
            self.trace.event("plan_updated", plan=plan)
        report = []
        for index, action in enumerate(plan["actions"]):
            operation = action.get("operation", "create")
            resource_id = action.get("google_id") or action.get("target", {}).get("id")
            receipt = action.get("receipt", {})
            confirmed = (action["status"] == "completed" and bool(resource_id)
                         and receipt.get("id") == resource_id
                         and (operation != "delete" or receipt.get("verified_absent") is True))
            verb = {"create": "Created", "update": "Updated", "delete": "Deleted", "lookup": "Retrieved"}[operation]
            label = action.get("title") or action.get("subject") or action.get("item", "item")
            status = action["status"] if action["status"] != "completed" or confirmed else "unverified"
            report.append({"action_index": index, "operation": operation, "resource_id": resource_id,
                           "kind": action["kind"], "title": label,
                           "confirmed": confirmed, "status": status,
                           "summary": f'{verb} {action["kind"]} "{label}".' if confirmed else f'{action["kind"]} "{label}": {status}; no confirmed {operation} completion.',
                           **({"task_status": action.get("current_values", {}).get("task_status", action.get("task_status")),
                               "timing": "Google Tasks dates are date-only; a time in notes is text, not a timed notification."} if action["kind"] == "task" else {})})
            if action["kind"] == "email":
                report[-1]["delivery_result"] = action.get("delivery_result")
        self.response_report = report
        self.response_reports[plan["plan_id"]] = (self.input_counter, report)
        return {"status": "success", "plan": plan, "report": report,
                "response_guidance": "Use only the confirmed operation and resource ID in report for completion claims. Plan titles are not outcomes. Prepared/pending/approval_required are not completed. Failed or unknown is not deleted/updated; ask clarification or confirmation as required, never promise a background retry. Task action completion does not mean the task was marked complete. Do not invent a reason for duplicate objects."}

    async def prepare(self, name):
        await asyncio.sleep(0)

    async def call(self, name, args):
        try:
            if name in MANAGEMENT_NAMES:
                return await self.management.call(name, args)
            if name == "search_gmail":
                return await self.google.search_messages(args["query"])
            if name == "get_gmail_message":
                return await self.google.get_message(args["message_id"])
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
                self._guard_creation(actions)
                if any(a.get("task_list_id", self.google.config.task_list_id) not in self.management.known_lists for a in actions if a["kind"] == "task"):
                    raise ValueError("Use a task list ID returned by list_task_lists.")
                correcting = IMPLICIT_CORRECTION.search(self.latest_input) or re.search(r"\b(?:actually|instead|undo)\b", self.latest_input, re.I)
                if (correcting and not re.search(r"\b(?:also|another|additional|new)\b", self.latest_input, re.I)
                    and self.store.has_dispatched_kind(self.room, {a["kind"] for a in actions} & {"calendar", "task"})):
                    raise ValueError("A related action was already dispatched. Use get_plan, reconcile_plan if needed, then prepare_change with its saved action index; do not create a duplicate.")
                plan = self.store.prepare(self.room, args["title"], actions, self.revision)
                self.prepared_after[plan["plan_id"]] = self.input_counter
                return self.publish(plan)
            if name == "get_plan":
                return self.publish(self.store.get(self.room, args["plan_id"]))
            if name == "prepare_change":
                return self.prepare_change(args)
            if name == "reconcile_plan":
                return await self.reconcile(args["plan_id"])
            if name == "execute_plan":
                return await self.execute(args["plan_id"])
            raise ValueError("Unsupported planning tool.")
        except (ValueError, GoogleAPIError) as exc:
            return {"status": "error", "message": str(exc)}

    def prepare_change(self, args):
        plan = self.store.get(self.room, args["plan_id"])
        index = args["action_index"]
        if not isinstance(index, int) or index < 0 or index >= len(plan["actions"]):
            raise ValueError("Select a saved action index.")
        original = plan["actions"][index]
        if original["kind"] not in {"calendar", "task"}:
            raise ValueError("Only Calendar events and Tasks have a supported inverse; sent emails cannot be undone.")
        if original.get("operation") == "lookup":
            self.management.selected(plan["plan_id"])
        resource_id = original.get("google_id")
        if not resource_id or original["status"] != "completed":
            raise ValueError("A confirmed resource ID is required. Reconcile an uncertain or running action first.")
        root = original
        if original.get("target"):
            target = original["target"]
            root = self.store.get(self.room, target["plan_id"])["actions"][target["action_index"]]
        if root.get("resource_deleted"):
            raise ValueError("This resource is already confirmed deleted.")
        operation, changes = args["operation"], args.get("changes", {})
        if operation not in {"update", "delete"} or (operation == "delete" and changes):
            raise ValueError("Choose an update with changed fields or a deletion without changed fields.")
        before = root.get("current_values") or action_values(root)
        allowed = set(before)
        if set(changes) - allowed or (operation == "update" and not changes):
            raise ValueError("Provide supported fields for this resource.")
        values = {**before, **changes}
        if original["kind"] == "calendar" and "start" in changes and "end" not in changes:
            parser = date.fromisoformat if len(before["start"]) == 10 else datetime.fromisoformat
            duration = parser(before["end"]) - parser(before["start"])
            values["end"] = (parser(values["start"]) + duration).isoformat()
        validated = validate_plan({"calendar_events" if original["kind"] == "calendar" else "tasks": [values]}, self.google.config.time_zone)[0]
        target = original.get("target") or {
            "room": self.room, "plan_id": plan["plan_id"], "action_index": index, "origin": plan.get("origin", "creation"),
            "kind": original["kind"], "id": resource_id,
            "container": original.get("container_id") or (self.google.config.calendar_id if original["kind"] == "calendar" else self.google.config.task_list_id),
        }
        action = {**validated, "operation": operation, "target": target,
                  "expected_values": before, "target_attendees": before.get("attendees", []),
                  "resource_revision": root.get("resource_revision", 0),
                  "consequential": original.get("consequential", False)}
        updated = self.store.prepare(self.room, (("Delete " if target.get("origin") == "lookup" else "Undo ") if operation == "delete" else "Update ") + values["title"], [action], self.revision)
        self.prepared_after[updated["plan_id"]] = self.input_counter
        return self.publish(updated)

    async def reconcile(self, plan_id):
        plan = self.store.get(self.room, plan_id)
        if plan["status"] == "running":
            return self.execution_result(plan)
        for action in plan["actions"]:
            if action["status"] != "unknown":
                continue
            target = action.get("target")
            if not target and action["kind"] == "calendar" and action.get("operation_id"):
                target = {"kind": "calendar", "id": action["operation_id"],
                          "container": action.get("container_id", self.google.config.calendar_id)}
            if not target:
                continue  # No safe ID-based lookup for a lost Tasks/Gmail creation receipt.
            remote = await self.google.get_resource(target["kind"], target["id"], target["container"])
            if (action.get("operation") == "delete" and remote is None) or (
                remote is not None and same_values(remote_values(action["kind"], remote), action_values(action))
            ):
                self._confirmed(action, remote, target["id"])
                action["reconciled_at"] = time.time()
        if all(a["status"] == "completed" for a in plan["actions"]):
            plan["status"] = "completed"
        # Reconciliation is read-only; completed evidence may merge after a dead lease.
        self.store.merge_reconciled(plan)
        return self.execution_result(self.store.get(self.room, plan_id))

    async def execute(self, plan_id):
        self.store.recover_expired(self.room)
        plan = self.store.get(self.room, plan_id)
        if plan["status"] != "prepared":
            return self.execution_result(plan)
        if (not plan["approved"] and self.input_final
            and self.input_counter > self.prepared_after.get(plan_id, self.input_counter)
            and re.fullmatch(r"confirm(?: the plan)?[.! ]*", self.latest_input, re.I)):
            plan = self.store.approve(self.room, plan_id, source="voice")
        automatic = any(a["status"] == "pending" and not requires_confirmation(a) for a in plan["actions"])
        if not self.input_final or (not plan["approved"] and not automatic):
            self.publish(plan)
            return {
                "status": "approval_required",
                "message": "Confirmation required for the pending consequential actions." if self.input_final else "Waiting for final input.",
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
        result["status"] = "success" if plan["status"] == "completed" else "approval_required" if pending_confirmation(plan) and plan["status"] == "prepared" else plan["status"]
        return result

    async def _execute_claimed(self, plan):
        plan_id = plan["plan_id"]
        self.publish(plan)
        for index, action in enumerate(plan["actions"]):
            if action["status"] not in {"pending", "approval_required"}:
                continue
            current = self.store.get(self.room, plan_id)
            if index in current.get("revoked_actions", []):
                action["status"] = "not_executed"
                continue
            if requires_confirmation(action) and not current.get("approved"):
                action["status"] = "approval_required"
                continue
            if current.get("cancel_requested") or not self.store.renew(self.room, plan_id, plan["lease_owner"]):
                break
            operation_id = hashlib.sha256(f"{plan_id}:{index}".encode()).hexdigest()[:32]
            action.update(status="running", operation_id=operation_id, started_at=time.time())
            action_started = time.monotonic()
            if not self.store.save(plan):
                return self.execution_result(self.store.get(self.room, plan_id))
            self.publish(plan)
            try:
                with self.google.authorize_writes(lambda: self._authorize_action(plan, action)):
                    if action.get("target"):
                        target = action["target"]
                        container, aliases = target["container"], []
                        if target["kind"] == "task":
                            container = await self.google.canonical_task_list(container)
                            legacy = json.dumps(["task", "@default", target["id"]])
                            if (self.google.config.task_list_id == "@default" or target["container"] == "@default"
                                or self.store.has_resource_owner(legacy)):
                                if container == await self.google.canonical_task_list("@default"):
                                    aliases.append(legacy)
                        self.store.claim_resource(json.dumps([target["kind"], container, target["id"]]), plan, index, aliases)
                    if self.trace:
                        with self.trace.bind(operation_id=operation_id, action_index=index):
                            result = await self._send_action(plan, action, operation_id)
                    else:
                        result = await self._send_action(plan, action, operation_id)
                if not isinstance(result.get("id"), str) or not result["id"].strip():
                    raise ValueError("Missing Google receipt ID")
                self._confirmed(action, result, result["id"])
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
                    if remaining["status"] in {"pending", "approval_required"}:
                        remaining["status"] = "not_executed"
                raise
            except MutationRevoked as exc:
                action.update(status="not_executed", error=str(exc))
                current = self.store.get(self.room, plan_id)
                if current.get("cancel_requested") or not self.input_final:
                    break
                continue  # A scoped correction must not discard unrelated personal actions.
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
            if action["status"] == "pending" or (action["status"] == "approval_required" and current.get("cancel_requested")):
                action["status"] = "not_executed"
        plan["status"] = (
            "completed" if all(a["status"] == "completed" for a in plan["actions"])
            else "prepared" if any(a["status"] == "approval_required" for a in plan["actions"]) and all(
                a["status"] in {"completed", "approval_required", "superseded", "not_executed"}
                for a in plan["actions"]
            )
            else "partial"
        )
        self.store.save(plan)
        return self.execution_result(plan)

    def _authorize_action(self, plan, action=None):
        current = self.store.get(self.room, plan["plan_id"])
        revoked = action is not None and any(a is action and i in current.get("revoked_actions", [])
                                            for i, a in enumerate(plan["actions"]))
        if (current["status"] != "running" or current.get("cancel_requested")
            or revoked
            or (action is not None and requires_confirmation(action) and not current.get("approved"))
            or not self.input_final or current.get("lease_owner") != plan["lease_owner"]):
            raise MutationRevoked("Approval or execution ownership changed before dispatch; no action was sent.")
        if action is not None:
            try:
                self._guard_creation([action])
            except ValueError as exc:
                raise MutationRevoked(str(exc)) from None

    def _confirmed(self, action, remote, resource_id):
        operation = action.get("operation", "create")
        action.update(status="completed", google_id=resource_id,
                      result=("deleted" if action.get("target", {}).get("origin") == "lookup" else "undone") if operation == "delete" else "updated" if operation == "update" else "completed",
                      current_values=action_values(action) if action["kind"] in {"calendar", "task"} else {},
                      url=(remote or {}).get("htmlLink", (remote or {}).get("webViewLink", "")),
                      receipt={"id": resource_id, "etag": (remote or {}).get("etag"),
                               **(remote or {}).get("_mutation_http", (remote or {}).get("_http", {})),
                               "verified_absent": remote is None or operation == "delete",
                               "received_at": time.time()})
        action.pop("error", None)
        if action.get("target"):
            self.store.remember_resource(action["target"], action["current_values"], operation == "delete",
                                         expected=action.get("expected_values"))

    async def _send_change(self, action):
        target = action["target"]
        kind, resource_id, container = target["kind"], target["id"], target["container"]
        remote = await self.google.get_resource(kind, resource_id, container)
        delete = action["operation"] == "delete"
        if remote is None:
            if delete:
                return {"id": resource_id, "_http": {"status_code": 404}, "verified_absent": True}
            raise GoogleAPIError("The saved resource is absent; no replacement was created.")
        if not same_values(remote_values(kind, remote), action["expected_values"]):
            raise GoogleAPIError("The remote resource changed since this proposal; review it before changing it.")
        desired = action_values(action)
        if not delete and same_values(remote_values(kind, remote), desired):
            return remote  # A verified no-op must not produce another mutation.
        if kind == "calendar":
            body = {"summary": desired["title"], "description": desired["notes"],
                    "start": event_time(desired["start"], self.google.config.time_zone),
                    "end": event_time(desired["end"], self.google.config.time_zone),
                    "reminders": reminder_values(desired["reminders"]), "location": desired["location"],
                    "attendees": [{"email": email} for email in desired["attendees"]]}
            fields = {"summary": "title", "description": "notes", "start": "start", "end": "end",
                      "reminders": "reminders", "location": "location", "attendees": "attendees"}
            body = {key: value for key, value in body.items() if not same_values(
                {fields[key]: action["expected_values"][fields[key]]},
                {fields[key]: desired[fields[key]]})}
            if "attendees" in body:
                # Calendar PATCH replaces arrays. Retained guests keep their writable metadata.
                writable = {"email", "displayName", "optional", "resource", "responseStatus", "comment", "additionalGuests"}
                existing = {guest.get("email", "").casefold(): guest for guest in remote.get("attendees", [])}
                body["attendees"] = [
                    {**{k: v for k, v in existing.get(email.casefold(), {}).items() if k in writable}, "email": email}
                    for email in desired["attendees"]
                ]
        else:
            body = {"title": desired["title"], "notes": desired["notes"],
                    "due": desired["due_date"] + "T00:00:00.000Z" if desired["due_date"] else None,
                    "status": desired["task_status"],
                    **({"completed": None} if desired["task_status"] == "needsAction" else {})}
        response = await self.google.change_resource(kind, resource_id, container,
                    body=None if delete else body, etag=remote.get("etag"), delete=delete,
                    notify=bool(action.get("attendees") or action.get("target_attendees")))
        try:
            checked = await self.google.get_resource(kind, resource_id, container)
        except GoogleAPIError as exc:
            raise GoogleAPIError("The mutation was dispatched but its final state could not be verified.", uncertain=True,
                                 status_code=exc.status_code, request_id=exc.request_id) from None
        if (delete and checked is not None) or (not delete and (
            checked is None or not same_values(remote_values(kind, checked), desired)
        )):
            raise GoogleAPIError("Google has not confirmed the requested final state.", uncertain=True)
        return {**(checked or {}), "id": resource_id, "_mutation_http": response.get("_http", {})}

    async def _send_action(self, plan, action, operation_id):
        if action.get("operation") in {"update", "delete"}:
            return await self._send_change(action)
        if action["kind"] == "calendar":
            action["container_id"] = self.google.config.calendar_id
            self.store.save(plan)
            return await self.google.create_event(action, operation_id)
        if action["kind"] == "task":
            action["container_id"] = await self.google.canonical_task_list(action.get("task_list_id", self.google.config.task_list_id))
            self.store.save(plan)
            return await self.google.create_task(action, action["container_id"])
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
