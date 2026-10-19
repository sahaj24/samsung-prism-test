"""Bounded Google lookups and resource-bound edit proposals; never mutates Google itself."""

import hashlib
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .catalog import S, ToolSpec

REMINDERS = {
    "type": "object",
    "properties": {
        "useDefault": {"type": "boolean"},
        "overrides": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["email", "popup"]},
                    "minutes": {"type": "integer", "minimum": 0, "maximum": 40320},
                },
                "required": ["method", "minutes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["useDefault"],
    "additionalProperties": False,
}
TASK_STATUS = {"type": "string", "enum": ["needsAction", "completed"]}
CHANGE_FIELDS = {
    "title": S,
    "start": S,
    "end": S,
    "notes": {"type": "string"},
    "due_date": {"type": "string"},
    "task_status": TASK_STATUS,
    "attendees": {"type": "array", "items": S},
    "reminders": REMINDERS,
    "location": {"type": "string"},
}
MANAGEMENT_TOOLS = [
    ToolSpec(
        "list_task_lists",
        "List Google Tasks lists and their actual IDs. Follow page tokens for more lists.",
        {"page_token": S},
        (),
        cache_result=False,
    ),
    ToolSpec(
        "list_tasks",
        "Read/search Google Tasks in a configured or previously listed task list. Query matches title/notes in each page; continue page tokens before claiming no matches. Due dates are dates, not timed notifications.",
        {
            "task_list_id": S,
            "query": S,
            "due_from": S,
            "due_before": S,
            "task_status": TASK_STATUS,
            "page_token": S,
        },
        (),
        cache_result=False,
    ),
    ToolSpec(
        "list_calendar_events",
        "Read/search the configured Google Calendar using native search. Times require offsets. Returns actual event/instance IDs and resource references; follow pagination.",
        {"query": S, "time_min": S, "time_max": S, "page_token": S},
        (),
        cache_result=False,
    ),
    ToolSpec(
        "get_workspace_item",
        "Read current details of a resource_ref returned by a task/event lookup. Never invent a reference.",
        {"resource_ref": S},
        ("resource_ref",),
        cache_result=False,
    ),
    ToolSpec(
        "prepare_resource_change",
        "Stage an edit or deletion of a looked-up resource_ref, then execute_plan. Ask which numbered result when ambiguous. task_status completed/needsAction marks complete/incomplete. Do not create a replacement.",
        {
            "resource_ref": S,
            "operation": {"type": "string", "enum": ["update", "delete"]},
            "changes": {
                "type": "object",
                "properties": CHANGE_FIELDS,
                "additionalProperties": False,
            },
        },
        ("resource_ref", "operation"),
        cache_result=False,
    ),
]
MANAGEMENT_NAMES = {s.name for s in MANAGEMENT_TOOLS}


def reminder_values(value):
    value = value or {"useDefault": True}
    if not isinstance(value, dict) or not isinstance(value.get("useDefault"), bool):
        raise ValueError("Calendar reminders need useDefault=true or false.")
    overrides = value.get("overrides", [])
    if not isinstance(overrides, list) or len(overrides) > 5 or (value["useDefault"] and overrides):
        raise ValueError("Use calendar defaults or up to five explicit reminders, not both.")
    for item in overrides:
        if (
            item.get("method") not in {"email", "popup"}
            or type(item.get("minutes")) is not int
            or not 0 <= item["minutes"] <= 40320
        ):
            raise ValueError(
                "Calendar reminders support popup/email, 0–40320 minutes before the event."
            )
    return {
        "useDefault": value["useDefault"],
        **(
            {"overrides": sorted(overrides, key=lambda v: (v["method"], v["minutes"]))}
            if not value["useDefault"]
            else {}
        ),
    }


def event_time(value, time_zone):
    return {"date": value} if len(value) == 10 else {"dateTime": value, "timeZone": time_zone}


class WorkspaceManagement:
    def __init__(self, backend):
        self.backend = backend
        self.groups = {}
        self.focus = None
        self.latest_group = None
        self.searches = {}
        self.known_lists = {backend.google.config.task_list_id}

    def record(self, kind, container, resource):
        from .productivity import remote_values

        if not isinstance(resource.get("id"), str) or not resource["id"]:
            raise ValueError("Google lookup did not return a resource ID.")
        values = remote_values(kind, resource)
        if kind == "calendar":
            for key in ("start", "end"):
                if values[key] and len(values[key]) > 10:
                    values[key] = (
                        datetime.fromisoformat(values[key].replace("Z", "+00:00"))
                        .astimezone(ZoneInfo(self.backend.google.config.time_zone))
                        .isoformat()
                    )
        ref = hashlib.sha256(
            json.dumps(
                [self.backend.room, kind, container, resource["id"], resource.get("etag"), values],
                sort_keys=True,
            ).encode()
        ).hexdigest()[:24]
        action = {
            "kind": kind,
            **values,
            "status": "completed",
            "operation": "lookup",
            "result": "retrieved",
            "google_id": resource["id"],
            "container_id": container,
            "consequential": bool(
                resource.get("assignmentInfo")
                or resource.get("recurrence")
                or resource.get("eventType", "default") != "default"
            ),
            "receipt": {"id": resource["id"], "etag": resource.get("etag"), "read_at": time.time()},
        }
        document = {
            "plan_id": ref,
            "room": self.backend.room,
            "title": values["title"],
            "status": "completed",
            "approved": False,
            "actions": [action],
            "origin": "lookup",
        }
        # A read snapshot never supersedes pending actions or claims that a creation happened.
        with self.backend.store.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO plans VALUES (?,?,?,?,?)",
                (ref, self.backend.room, "completed", json.dumps(document), time.time()),
            )
        return {
            **values,
            "id": resource["id"],
            "resource_ref": ref,
            "kind": kind,
            "container_id": container,
            "etag": resource.get("etag"),
            "consequential": action["consequential"],
        }

    def snapshot(self, ref):
        plan = self.backend.store.get(self.backend.room, ref)
        if plan.get("origin") != "lookup":
            raise ValueError("Use a resource_ref returned by a Google lookup in this conversation.")
        return plan["actions"][0]

    def selected(self, ref):
        item = self.snapshot(ref)
        text = self.backend.latest_input.casefold()
        group = self.groups.get(ref)
        if (
            re.search(
                r"\b(?:id|identifier)\s+"
                + re.escape(item["google_id"].casefold())
                + r"(?:\s|[.?!]|$)",
                text,
            )
            and (not group or sum(row["id"] == item["google_id"] for row in group["items"]) == 1)
            or self.focus == ref
        ):
            return
        if group:
            items = group["items"]
            if group is self.latest_group and len(items) == 1 and not group["more"]:
                self.focus = ref
                return
            # An explicit numbered answer must agree with the actual user's final turn.
            match = re.search(
                r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b|\b(?:number|item|option)\s+(\d+)\b|^\s*(\d+)\s*[.!]?\s*$",
                text,
            )
            ordinal = None
            if match:
                words = [
                    "first",
                    "second",
                    "third",
                    "fourth",
                    "fifth",
                    "sixth",
                    "seventh",
                    "eighth",
                    "ninth",
                    "tenth",
                ]
                ordinal = (
                    words.index(match.group(0)) + 1
                    if match.group(0) in words
                    else int(match.group(1) or match.group(2))
                )
            if (
                group is self.latest_group
                and self.backend.input_counter > group["after"]
                and ordinal
                and 0 < ordinal <= len(items)
                and (items[ordinal - 1].get("container_id"), items[ordinal - 1]["id"])
                == (item["container_id"], item["google_id"])
            ):
                self.focus = ref
                return
            matches = [v for v in items if v["title"] and v["title"].casefold() in text]
            if (
                len(matches) == 1
                and (matches[0].get("container_id"), matches[0]["id"])
                == (item["container_id"], item["google_id"])
                and not group["more"]
            ):
                self.focus = ref
                return
        raise ValueError(
            "Ambiguous or unselected item. Ask which numbered result or exact resource ID the user means before changing it."
        )

    def publish(
        self,
        kind,
        items,
        *,
        next_token=None,
        scanned=None,
        group=True,
        search_key=None,
        source=None,
        page_token=None,
    ):
        if group:
            key = (kind, json.dumps(search_key or {}, sort_keys=True))
            value = self.searches.get(key)
            if not value or (not page_token and value["after"] != self.backend.input_counter):
                value = {"items": [], "after": self.backend.input_counter, "pages": {}}
            combined = {(i.get("container_id"), i["id"]): i for i in value["items"]}
            combined.update({(i.get("container_id"), i["id"]): i for i in items})
            value.update(items=list(combined.values()), after=self.backend.input_counter)
            value["pages"][source] = next_token
            value["more"] = any(value["pages"].values())
            self.searches[key] = value
            self.latest_group = value
            self.focus = (
                value["items"][0]["resource_ref"]
                if len(value["items"]) == 1 and not value["more"]
                else None
            )
            for item in value["items"]:
                self.groups[item["resource_ref"]] = value
        # Bound descriptions for model/UI output; the saved edit snapshot retains full values.
        visible = [
            {
                **item,
                "notes": item.get("notes", "")[:12000],
                "notes_truncated": len(item.get("notes", "")) > 12000,
                "number": next(
                    (
                        n + 1
                        for n, row in enumerate(
                            self.groups.get(item.get("resource_ref"), {}).get("items", [])
                        )
                        if (row.get("container_id"), row["id"])
                        == (item.get("container_id"), item["id"])
                    ),
                    i + 1,
                ),
            }
            for i, item in enumerate(items)
        ]
        result = {
            "status": "success",
            "kind": kind,
            "items": visible,
            "next_page_token": next_token,
            "more_available": value["more"] if group else bool(next_token),
            "scanned": len(items) if scanned is None else scanned,
        }
        if group:
            result["pending_pages"] = [
                {"container_id": container, "page_token": token}
                for container, token in value["pages"].items()
                if token
            ]
        if self.backend.trace:
            self.backend.trace.event(
                "workspace_items",
                resource_kind=kind,
                **{key: value for key, value in result.items() if key != "kind"},
            )
        return result

    async def call(self, name, args):
        google = self.backend.google
        if name == "list_task_lists":
            response = await google.list_task_lists(args.get("page_token"))
            items = [
                {"id": i["id"], "title": i.get("title", ""), "kind": "task_list"}
                for i in response.get("items", [])
            ]
            self.known_lists.update(i["id"] for i in items)
            return self.publish(
                "task_list", items, next_token=response.get("nextPageToken"), group=False
            )
        if name == "list_tasks":
            container = args.get("task_list_id", google.config.task_list_id)
            if container not in self.known_lists:
                raise ValueError("Use a task list ID returned by list_task_lists.")
            container = await google.canonical_task_list(container)
            self.known_lists.add(container)
            response = await google.list_tasks(
                container, **{k: v for k, v in args.items() if k != "task_list_id"}
            )
            return self.publish(
                "task",
                [self.record("task", container, r) for r in response["items"]],
                next_token=response.get("nextPageToken"),
                scanned=response["scanned"],
                search_key={
                    k: v for k, v in args.items() if k not in {"task_list_id", "page_token"}
                },
                source=container,
                page_token=args.get("page_token"),
            )
        if name == "list_calendar_events":
            response = await google.list_events(**args)
            return self.publish(
                "calendar",
                [
                    self.record("calendar", google.config.calendar_id, r)
                    for r in response.get("items", [])
                ],
                next_token=response.get("nextPageToken"),
                search_key={k: v for k, v in args.items() if k != "page_token"},
                source=google.config.calendar_id,
                page_token=args.get("page_token"),
            )
        ref = args["resource_ref"]
        saved = self.snapshot(ref)
        if name == "get_workspace_item":
            resource = await google.get_resource(
                saved["kind"], saved["google_id"], saved["container_id"]
            )
            if resource is None:
                return self.publish(saved["kind"], [], group=False)
            item = self.record(saved["kind"], saved["container_id"], resource)
            # A details read must not manufacture selection of an ambiguous search result.
            if ref in self.groups:
                self.groups[item["resource_ref"]] = self.groups[ref]
            if self.focus == ref:
                self.focus = item["resource_ref"]
            return self.publish(saved["kind"], [item], group=False)
        self.selected(ref)
        return self.backend.prepare_change(
            {
                "plan_id": ref,
                "action_index": 0,
                "operation": args["operation"],
                "changes": args.get("changes", {}),
            }
        )
