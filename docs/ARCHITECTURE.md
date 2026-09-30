# Reprise Architecture & Design

This document outlines the core architectural patterns that power Reprise, focusing on how we achieve ultra-low latency while maintaining robust tool execution and graceful interruption recovery.

## 1. System Overview

Reprise is built on a streaming architecture that bypasses traditional speech-to-text-to-llm pipelines.

- **Audio Transport:** [LiveKit WebRTC](https://livekit.io/) handles the raw bidirectional audio stream, ensuring robust packet delivery, echo cancellation, and network resilience.
- **Cognitive Engine:** [Gemini 3.8 Live API](https://ai.google.dev/) receives the raw audio stream directly. This allows the model to respond to voice tone and pacing natively.
- **Action Engine:** The `Local Action Controller` (implemented in `src/reprise/coordinator.py`) manages all tool calling, validation, and side-effects.

## 2. The Local Action Controller

When Gemini decides to execute a tool (e.g., booking a flight or setting a calendar event), it emits a tool call frame over the websocket. Rather than executing this blindly, Reprise intercepts it using the **Local Action Controller**.

### Asynchronous Execution
If an agent pauses to execute an API call (which can take 1-3 seconds), the conversational flow feels broken and unnatural. 
To solve this, the Local Action Controller pushes all tool executions to background async tasks. Gemini is immediately told that the action has been dispatched, allowing it to continue speaking to the user (e.g., *"I'm looking that up for you right now..."*) while the real API call completes in the background.

## 3. Interruption Recovery & The Action Ledger

The most complex challenge in real-time voice agents is handling human interruptions. 
A user might say: *"Create a meeting with John for 3 PM—actually, make it 4 PM."*

By the time the user says *"make it 4 PM"*, Gemini may have already emitted the tool call to create a 3 PM meeting. 

### How Reprise Recovers:
1. **The Ledger:** The Coordinator maintains an active "Ledger" of pending operations.
2. **Settlement Delay:** Tools that write data (like `book_flight` or `add_to_cart`) are held in a brief "settlement" buffer (configurable via `REPRISE_WRITE_SETTLE_MS`).
3. **Supersedence:** If the user interrupts and corrects themselves, Gemini immediately sends an updated tool call or cancellation. The Coordinator matches this against the Ledger.
4. **Interception:** If a stale tool call is found in the buffer, it is gracefully dropped and marked as `Superseded` before it ever reaches the external API.

This pattern ensures that external state (like a Google Calendar or an E-commerce cart) remains perfectly synchronized with the user's final intent, completely eliminating duplicate or erroneous API calls.

## 4. Google Workspace Extension

The codebase extends the core architecture with a real-world Google Workspace planner.

- **OAuth 2.0:** Managed natively in `demo_server.py`. The agent handles the full OAuth flow, securing a refresh token that is stored locally in `.google-workspace-token.json`.
- **REST Adapters:** Implemented in `google_workspace.py`, these adapters translate Gemini's generic `ToolSpec` JSON arguments into strict Google API requests (e.g., formatting ISO dates for Google Calendar or parsing Gmail drafts).
- **Dashboard:** A React/Node.js based dashboard (`demo/`) connects to the Local Action Controller via WebSockets to visually reflect the agent's internal state, providing a transparent view of the Action Ledger as it updates in real-time.
