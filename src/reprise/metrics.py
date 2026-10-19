"""Offline summaries of measured events. State timings are not aligned audio timings."""

import math
from collections import Counter, defaultdict


def distribution(values):
    values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))

    def percentile(p):
        return values[max(0, math.ceil(p * len(values)) - 1)] if values else None

    return {
        "count": len(values),
        "mean": sum(values) / len(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "p50": percentile(0.5),
        "p95": percentile(0.95),
    }


def summarize_events(events):
    counts = Counter(e.get("event") for e in events)
    timings = defaultdict(list)
    tool_outcomes, api_outcomes = Counter(), Counter()
    state, pending_interruption, last_end = None, None, None
    interruptions = 0
    usage = None
    request_metrics = {}
    for event in events:
        kind, elapsed = event.get("event"), event.get("elapsed_s")
        if kind == "tool_executed":
            tool_outcomes[event.get("outcome", "unrecorded")] += 1
            timings["tool_duration_s"].append(event.get("duration_s"))
        if kind == "external_api_completed":
            api_outcomes[event.get("outcome", "unrecorded")] += 1
            timings["external_api_duration_s"].append(event.get("duration_s"))
        if kind == "controller_decision":
            timings["gate_duration_s"].append(event.get("gate_duration_s"))
        if kind == "tool_prepared":
            timings["preparation_duration_s"].append(event.get("duration_s"))
        if kind == "speech_started" and state == "speaking" and pending_interruption is None:
            interruptions += 1
            pending_interruption = elapsed
        if kind == "speech_ended":
            last_end = elapsed
        if kind == "agent_state":
            new = event.get("state")
            if (
                new == "speaking"
                and state != "speaking"
                and last_end is not None
                and elapsed is not None
            ):
                timings["vad_to_assistant_state_s"].append(elapsed - last_end)
                last_end = None
            if (
                state == "speaking"
                and new != "speaking"
                and pending_interruption is not None
                and elapsed is not None
            ):
                timings["interruption_to_state_stop_s"].append(elapsed - pending_interruption)
                pending_interruption = None
            state = new
        if kind == "model_metrics":
            metric = event.get("metrics") or {}
            if metric.get("type") == "realtime_model_metrics" and metric.get("request_id"):
                request_metrics[metric["request_id"]] = metric
        if kind == "session_usage":
            usage = event.get("usage")
    for metric in request_metrics.values():
        if metric.get("ttft", -1) >= 0:
            timings["sdk_ttft_s"].append(metric["ttft"])
    names = (
        "tool_duration_s",
        "external_api_duration_s",
        "gate_duration_s",
        "preparation_duration_s",
        "vad_to_assistant_state_s",
        "interruption_to_state_stop_s",
        "sdk_ttft_s",
    )
    return {
        "event_counts": dict(counts),
        "tool_outcomes": dict(tool_outcomes),
        "external_api_outcomes": dict(api_outcomes),
        "timings": {name: distribution(timings[name]) for name in names},
        "user_interruptions_observed": interruptions,
        "interrupted_assistant_messages": sum(
            bool(e.get("interrupted")) for e in events if e.get("event") == "assistant_response"
        ),
        "sdk_usage_latest": usage,
        "sdk_realtime_requests": list(request_metrics.values()),
        "trace_closed": bool(counts["session_closed"]),
        "official_audio_alignment_available": False,
        "timing_note": "VAD and SDK state transitions are local proxies, not listener playback or upstream ASR alignment.",
        "billable_cost": None,
    }
