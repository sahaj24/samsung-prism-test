"""LiveKit event wiring kept independent of transport/model construction."""

from dataclasses import asdict, is_dataclass


def attach_session_events(session, coordinator):
    trace = coordinator.trace

    @session.on("user_input_transcribed")
    def transcript(event):
        coordinator.observe_transcript(
            event.transcript,
            is_final=event.is_final,
            source="audio",
            item_id=getattr(event, "item_id", None),
            event_time=getattr(event, "created_at", None),
        )

    @session.on("conversation_item_added")
    def conversation(event):
        item = event.item
        if not hasattr(item, "role"):
            return
        if item.role == "user":
            coordinator.observe_transcript(
                item.text_content or "",
                is_final=True,
                source="conversation",
                item_id=getattr(item, "id", None),
                event_time=getattr(item, "created_at", None),
            )
        trace.event(
            "conversation_item",
            role=item.role,
            text=item.text_content,
            message_id=getattr(item, "id", None),
            interrupted=getattr(item, "interrupted", False),
        )
        if item.role == "assistant":
            speech = getattr(session, "current_speech", None)
            response_id = getattr(speech, "id", None) or coordinator.last_response_id
            context = coordinator.response_contexts.get(response_id, {})
            with trace.bind(**context):
                trace.event(
                    "assistant_response",
                    response_id=response_id,
                    message_id=getattr(item, "id", None),
                    text=item.text_content,
                    interrupted=getattr(item, "interrupted", False),
                    linkage_basis="sdk_speech" if speech else "latest_tool_response",
                    related_proposal_ids=coordinator.response_proposals.get(response_id, []),
                    response_metrics=_values(getattr(item, "metrics", None)),
                )

    @session.on("agent_state_changed")
    def agent_state(event):
        trace.event("agent_state", state=event.new_state)
        if event.new_state == "listening" and event.old_state == "speaking":
            coordinator.finish_response()

    @session.on("error")
    def error(event):
        trace.event("session_error", error_type=type(event.error).__name__)

    @session.on("metrics_collected")
    def metrics(event):
        trace.event("model_metrics", metrics=_values(event.metrics))

    @session.on("session_usage_updated")
    def usage(event):
        # Cumulative SDK snapshot: report the last value, never sum these events.
        trace.event("session_usage", usage=_values(event.usage))

    @session.on("tool_execution_updated")
    def tool_lifecycle(event):
        trace.event("sdk_tool_lifecycle", update=_values(event.update))


def _values(value):
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _values(asdict(value))
    if isinstance(value, dict):
        return {k: _values(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_values(v) for v in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return {"type": type(value).__name__}
