"""LiveKit event wiring kept independent of transport/model construction."""

import asyncio

from dataclasses import asdict, is_dataclass


def attach_session_events(session, coordinator):
    trace = coordinator.trace
    activation = getattr(coordinator, "activation", None)

    @session.on("speech_created")
    def speech_created(event):
        if activation:
            activation.register_response(event.speech_handle.id)


    @session.on("user_input_transcribed")
    def transcript(event):
        was_standby = activation and activation.enabled and activation.state == "standby"
        coordinator.observe_transcript(
            event.transcript,
            is_final=event.is_final,
            source="audio",
            item_id=getattr(event, "item_id", None),
            event_time=getattr(event, "created_at", None),
        )
        if was_standby and activation.allows_tools and event.is_final:
            epoch = activation.epoch
            def respond_to_committed_request():
                if activation.enabled and activation.allows_tools and activation.epoch == epoch:
                    session.generate_reply(instructions=(
                        "Respond to the user's latest addressed request, using the recent conversation. "
                        "Earlier assistant output generated in standby was not delivered. "
                        "Use the controller tools and actual receipts; do not assume that work was done."
                    ))
            # Gemini emits final transcription at turn_complete, before it closes
            # the old generation. Schedule after that synchronous callback finishes.
            asyncio.get_running_loop().call_soon(respond_to_committed_request)

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
        if item.role == "assistant" and activation:
            speech = getattr(session, "current_speech", None)
            if not activation.allows_response(getattr(speech, "id", None)):
                trace.event("assistant_output_suppressed", message_id=getattr(item, "id", None),
                            text=item.text_content, state=activation.state)
                return
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
            current = context.get("input_id", coordinator.current_input_id) == coordinator.current_input_id
            checker = getattr(coordinator.backend, "response_correction", None)
            correction = checker(item.text_content) if checker and current and not getattr(item, "interrupted", False) else None
            if correction:
                trace.event("assistant_claim_unverified", message_id=getattr(item, "id", None),
                            actual_text=item.text_content, correction=correction)
                if coordinator.current_input_id not in coordinator._grounding_notices:
                    coordinator._grounding_notices.add(coordinator.current_input_id)
                    session.interrupt()
                    session.generate_reply(
                        instructions="Correct the preceding unsupported claim using only these actual receipt facts. Do not use tools or promise background work: " + correction,
                        tool_choice="none", tools=[],
                    )
            observer = getattr(coordinator.backend, "observe_assistant", None)
            if observer and current and not correction:
                observer(item.text_content, interrupted=getattr(item, "interrupted", False))
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
        if getattr(event.error, "type", None) == "realtime_model_error":
            trace.event("gemini_connection", state=(
                "reconnecting" if getattr(event.error, "recoverable", False) else "disconnected"
            ))

    @session.on("close")
    def closed(event):
        trace.event("gemini_connection", state="disconnected")

    @session.on("metrics_collected")
    def metrics(event):
        values = _values(event.metrics)
        trace.event("model_metrics", metrics=values)
        # SDK emits this zero-usage metric after the Gemini Live setup handshake.
        # Agent readiness or a LiveKit room join alone does not prove this connection.
        if (values and values.get("type") == "realtime_model_metrics"
                and values.get("request_id") == ""
                and values.get("connection_reused") is False
                and values.get("acquire_time") is not None):
            trace.event("gemini_connection", state="connected")

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
