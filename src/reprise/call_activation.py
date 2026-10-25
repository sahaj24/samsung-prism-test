"""Deterministic, session-local activation for shared-microphone Demo Live calls."""
import re

from .coordinator import Superseded


ACTIVATION_VERSION = 2


def activation_enabled(room_name, metadata):
    """Demo rooms fail closed unless ordinary Live was explicitly negotiated."""
    if not room_name.startswith("reprise-demo-"):
        return False  # Existing benchmark/non-browser clients retain their protocol.
    return not (metadata.get("presentation") == "live"
                and metadata.get("activation_version") == ACTIVATION_VERSION)


PROMPT = """
When Demo Live call listening is enabled, transcribe the entire shared-microphone
conversation without assigning speaker names. Stay silent and do not call tools
until a final utterance clearly addresses Reprise with a request. Use the recent
conversation to understand that request, but do not act on background conversation.
While active, listen for corrections from either voice and follow the latest valid
intent through the controller. An addressed dismissal (such as 'Thanks, Reprise'
or 'That's all, Reprise') ends assistance: do not speak an acknowledgement or use
tools. Standalone final ok/okay/done also end assistance. Generic hey or thanks
are neither activation nor dismissal; ok/done followed by instructions are not dismissals. A wake phrase does
not establish speaker identity or authorize consequential actions. Preserve the
existing risk-based consent policy; ordinary reversible actions need no extra click.
In ordinary Live mode, respond normally without requiring a wake phrase.
"""

_PREFIX = re.compile(r"^(?:(?:hey|hi|okay|ok)[,\s]+)?reprise\b[,\s:!.-]*(.*)$", re.I)
_SUFFIX = re.compile(r"^(.+?)[,\s]+reprise[.!?\s]*$", re.I)
_DISMISS = re.compile(
    r"(?:thanks(?:\s+you)?|thank\s+you|that['’]?s\s+all|that\s+is\s+all|"
    r"we['’]?re\s+done|we\s+are\s+done|stop\s+listening|go\s+(?:back\s+)?to\s+standby)"
    r"(?:\s+for\s+now)?", re.I
)


class CallActivation:
    def __init__(self, coordinator, *, enabled=False):
        self.coordinator = coordinator
        self.enabled = enabled
        self.state = 'standby' if enabled else 'active'
        self.epoch = 0
        self.on_change = None
        self.responses = {}
        self._seen_items = set()
        self._last_final = None
        self._times = {}
        self.publish('session_started')

    @property
    def allows_tools(self):
        return not self.enabled or self.state != 'standby'

    def publish(self, reason):
        self.coordinator.trace.event('call_activation', enabled=self.enabled,
                                     state=self.state, epoch=self.epoch, reason=reason,
                                     activation_version=ACTIVATION_VERSION)
        if self.on_change:
            self.on_change()

    def _standby(self, reason):
        self.epoch += 1
        self.state = 'standby'
        self.coordinator.suspend_pending(reason)
        self.publish(reason)

    def set_enabled(self, enabled):
        if self.enabled == enabled:
            return
        self.enabled = enabled
        if enabled:
            self._standby('demo_live_selected')
        else:
            self.epoch += 1
            self.state = 'active'
            self.publish('live_selected')

    def observe(self, text, *, is_final, source, item_id=None, event_time=None):
        if not self.enabled:
            return True
        if source == 'conversation':
            return self.allows_tools
        # Conversation echoes and late finals must not reactivate a dismissed call.
        if (item_id and item_id in self._seen_items) or (
            event_time is not None and event_time < self._times.get(source, event_time)
        ):
            return self.allows_tools
        if event_time is not None:
            self._times[source] = event_time
        normalized = re.sub(r'\s+', ' ', text.casefold()).strip(' .,!?:;')
        if is_final:
            if item_id:
                self._seen_items.add(item_id)
            if normalized == self._last_final:
                return self.allows_tools
            self._last_final = normalized
        if is_final and normalized in {'ok', 'okay', 'done'}:
            if self.state != 'standby':
                self._standby('short_dismissal')
            return False
        match = _PREFIX.fullmatch(text) or _SUFFIX.fullmatch(text)
        addressed = match.group(1).strip(' .,!?:;') if match else None
        if addressed is not None and _DISMISS.fullmatch(addressed):
            if self.state != 'standby':
                self._standby('addressed_dismissal')
            return False
        if self.state == 'standby':
            if is_final and addressed and re.match(
                r"(?:please\s+)?(?:can|could|would|will|help|find|search|look|check|show|tell|read|"
                r"create|add|make|schedule|move|reschedule|update|change|edit|delete|remove|cancel|"
                r"send|draft|save|set|remind|list|what|when|where|who|how|do|does|i need|i want|i would like)\b",
                addressed, re.I,
            ):
                self.epoch += 1
                self.state = 'active'
                self.publish('addressed_request')
            else:
                return False
        elif is_final and self.state == 'interruption':
            self.state = 'active'
            self.publish('transcript_committed')
        return True

    def speech_started(self):
        if self.enabled and self.state == 'active':
            self.state = 'interruption'
            self.publish('speech_started')

    def speech_ended(self):
        if self.enabled and self.state == 'interruption':
            self.state = 'active'
            self.publish('speech_ended')

    def register_response(self, response_id):
        self.responses[response_id] = self.epoch if self.allows_tools else None

    def allows_response(self, response_id):
        return not self.enabled or (
            self.allows_tools and self.responses.get(response_id) == self.epoch
        )

    def check(self):
        if not self.allows_tools:
            self.coordinator.trace.event('activation_tool_blocked', state=self.state)
            raise Superseded('Reprise is in standby; no tool execution is permitted.')

    async def filter_output(self, stream, response_id=None):
        # Drain suppressed streams, keeping Gemini's audio input/transcription alive.
        epoch = self.epoch
        allowed = self.allows_tools and (response_id is None or self.allows_response(response_id))
        async for value in stream:
            allowed = allowed and self.allows_tools and epoch == self.epoch
            if allowed:
                yield value
