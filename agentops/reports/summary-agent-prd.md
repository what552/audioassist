# PRD: Interactive Summary Agent for AudioAssist

## 1. Overview

AudioAssist currently supports:

- File transcription with Qwen3-ASR / Whisper
- Speaker diarization with pyannote
- Realtime microphone transcription with Silero VAD + Qwen3-ASR
- Transcript viewing, inline editing, and save
- Summary generation through an OpenAI-compatible API using prompt templates

The current summary capability is a one-shot generator:

1. User selects a template
2. User clicks Generate
3. Transcript text is sent to the LLM
4. A summary is streamed back
5. The app stores the latest three summary versions

This is sufficient for initial draft generation, but it does not support iterative editing, multi-turn interaction, cross-session memory, or app-wide retrieval and synthesis.

This PRD proposes a new **Interactive Summary Agent** built on **OpenAI Agents SDK** to evolve AudioAssist from a summary generator into a meeting knowledge assistant.

## 2. Problem Statement

The current summary flow has the following limitations:

- No conversational editing after generation
- No persistent agent memory across turns or sessions
- No ability to answer follow-up questions from transcript + summary context
- No app-wide retrieval across historical meetings
- No structured workflow for revising, comparing, and managing summaries
- No clear path toward long-lived assistant behavior

Users need to be able to:

- Refine a summary through natural language instructions
- Ask questions about a meeting after summary generation
- Retrieve and compare insights across multiple meetings
- Keep ongoing context between sessions
- Use one assistant interface for both per-meeting editing and app-level analysis

## 3. Goals

### 3.1 Primary Goals

1. Add a **single-meeting interactive summary agent** for per-meeting editing and Q&A
2. Add an **app-wide agent** for cross-meeting retrieval, synthesis, and analysis
3. Preserve multi-turn context using session-based memory
4. Keep transcript, summary, and agent history as separate assets
5. Integrate cleanly into the current Python backend + PyWebView frontend architecture

### 3.2 Non-Goals

- Replacing the existing one-shot summary generation flow immediately
- Building a multi-agent orchestration platform in v1
- Introducing LangGraph or other heavy workflow frameworks
- Building voice interaction in the first release
- Rewriting the app UI architecture from scratch

## 4. Users

### 4.1 Primary Users

- Individual users reviewing a single meeting and refining its summary
- Users who revisit prior meetings and need conversational follow-up
- Power users who want to search and summarize insights across all meetings

### 4.2 Internal Users

- Builder: implements backend/runtime and UI integration
- Leader: reviews architecture, scope, and rollout phases

## 5. Product Scope

The product should evolve in two layers:

### 5.1 Meeting Agent

Scoped to a single `job_id`, supports:

- Interactive editing of the current summary
- Summary Q&A grounded in transcript + current summary
- Section-level rewrite
- Version management
- Citation or source anchoring to transcript segments

### 5.2 Global Agent

Scoped to the entire app data set, supports:

- Cross-meeting search
- Cross-meeting summarization
- Action item aggregation
- Project/customer/topic-level synthesis
- Long-lived user preference memory

## 6. User Stories

### 6.1 Meeting Agent

- As a user, I want to say "expand the third point" so the summary becomes more detailed without regenerating everything.
- As a user, I want to say "make the action items more concrete" so the assistant rewrites only the relevant section.
- As a user, I want to ask "what was the final decision?" and get an answer based on the transcript and current summary.
- As a user, I want to see prior summary versions and restore one if the latest change is worse.
- As a user, I want the assistant to explain which transcript content supports a conclusion.

### 6.2 Global Agent

- As a user, I want to ask "find all meetings mentioning ACME" and get matching meetings.
- As a user, I want to ask "summarize blockers from the last five weekly syncs" and get a merged view.
- As a user, I want to ask "show open action items by owner" and get a cross-meeting analysis.
- As a user, I want the assistant to remember I prefer a boss-style summary format.

## 7. Current State

Relevant existing code:

- [src/summary.py](/Users/feifei/programing/audioassist/audioassist-researcher/src/summary.py)
- [app.py](/Users/feifei/programing/audioassist/audioassist-researcher/app.py)
- [ui/js/summary.js](/Users/feifei/programing/audioassist/audioassist-researcher/ui/js/summary.js)

Current behavior:

- `app.py::summarize(job_id, template)` runs in a background thread
- Reads transcript from `output/{job_id}.json`
- Concatenates transcript text
- Calls OpenAI-compatible API through `src.summary.summarize`
- Streams chunks back via `evaluate_js`
- Stores up to three versions in `output/{job_id}_summary.json`

There is no session memory, tool loop, or app-level retrieval abstraction today.

## 8. Why OpenAI Agents SDK

This PRD recommends using **OpenAI Agents SDK** as the agent runtime.

### 8.1 Reasons

- Built-in session model for multi-turn continuity
- Tool abstraction fits transcript/summary/search actions well
- Better fit than a fully custom loop once we add cross-session retrieval and app-wide assistant behavior
- Lighter than LangGraph
- Extensible toward future tracing, memory, and more advanced orchestration

### 8.2 Constraints

- The SDK is not the storage layer; local files remain app-owned
- The SDK is not the retrieval layer; search and indexing still need to be implemented in-app
- Provider compatibility needs validation if the team wants to support non-OpenAI models inside the agent runtime

## 9. Proposed Architecture

### 9.1 High-Level Model

Two agent entry points:

1. **Meeting Agent**
   - Bound to one `job_id`
   - Uses transcript + current summary + summary history + chat history

2. **Global Agent**
   - Bound to app-level session
   - Uses meeting index + summary store + transcript search + user preferences

### 9.2 Suggested Backend Modules

- `src/agent/meeting_agent.py`
- `src/agent/global_agent.py`
- `src/agent/tools.py`
- `src/agent/store.py`
- `src/agent/search.py`
- `src/agent/models.py`

### 9.3 Suggested Data Assets

Keep assets separated by concern:

- `output/{job_id}.json`: transcript source of truth
- `output/{job_id}.md`: transcript markdown sidecar
- `output/{job_id}_summary.json`: summary versions
- `output/{job_id}_summary.md`: current summary markdown
- `output/{job_id}_summary_chat.json`: meeting agent history
- `output/agent/global_sessions/{session_id}.json`: global agent histories
- `output/agent/user_preferences.json`: long-lived user preferences
- `output/agent/index.json`: lightweight meeting index

## 10. Core Features

### 10.1 Meeting Agent Features

#### F1. Conversational Summary Editing

The user can type iterative requests such as:

- "Expand the third point"
- "Make the action items more concrete"
- "Rewrite this like a boss update"

Expected behavior:

- Agent reads current summary and transcript context
- Agent updates the draft or section
- Agent explains changes
- Agent saves a new version if the draft is accepted/applied

#### F2. Section-Level Rewrite

The user can target specific sections:

- Key conclusions
- Action items
- Risks
- Next steps

Expected behavior:

- The assistant rewrites only the target section
- Other sections remain stable unless explicitly changed

#### F3. Meeting Q&A

The user can ask:

- "What was the final decision?"
- "Who owns the migration task?"
- "What risks did we mention?"

Expected behavior:

- Agent answers using transcript + current summary
- Where possible, the answer references transcript segments or timestamps

#### F4. Version History and Restore

Expected behavior:

- Show summary versions
- Show differences at a high level
- Support restore / rollback

#### F5. Source Anchoring

Expected behavior:

- Agent can cite transcript segments or timestamps for important claims
- This improves trust and editability

### 10.2 Global Agent Features

#### F6. Cross-Meeting Search

The user can ask:

- "Find all meetings mentioning ACME"
- "Search all summaries for pricing concerns"

Expected behavior:

- Search transcript and summary assets
- Return relevant meetings with metadata

#### F7. Cross-Meeting Synthesis

The user can ask:

- "Summarize blockers from the last five weekly syncs"
- "What changed between the last three customer calls?"

Expected behavior:

- Agent retrieves a relevant set of meetings
- Agent synthesizes a combined summary

#### F8. Action Item Aggregation

The user can ask:

- "List open action items grouped by owner"
- "Show all follow-ups related to Project X"

Expected behavior:

- Parse or retrieve action items from summaries
- Aggregate across meetings
- Return grouped outputs

#### F9. User Preference Memory

Expected behavior:

- Remember preferred summary style
- Remember persistent terminology preferences
- Reuse defaults in later sessions

#### F10. Incremental Summarization

Expected behavior:

- Use previous related meeting summaries to generate deltas
- Emphasize changes, new decisions, unresolved risks, and carry-over items

## 11. Tool Design

### 11.1 Meeting Agent Tools

- `get_transcript_context(job_id, query=None, max_chars=None, segment_range=None)`
- `get_current_summary(job_id)`
- `get_summary_versions(job_id)`
- `patch_summary_section(job_id, section, instruction)`
- `replace_summary(job_id, new_text, reason)`
- `save_summary_version(job_id, text)`
- `get_meeting_metadata(job_id)`

### 11.2 Global Agent Tools

- `search_meetings(query, filters)`
- `search_transcripts(query, filters)`
- `search_summaries(query, filters)`
- `list_recent_meetings(limit, filters=None)`
- `get_meeting_summary(job_id)`
- `get_meeting_transcript(job_id, max_chars=None)`
- `aggregate_action_items(meeting_ids=None, owner=None)`
- `get_user_preferences()`
- `save_user_preference(key, value)`

### 11.3 Web Tools

For later phases:

- `web_fetch(url, max_content_tokens=None)`
- `web_search(query)`

These should not block v1.

## 12. Session Design

### 12.1 Meeting Agent Session

Suggested shape:

- `session_id`
- `job_id`
- `chat_history`
- `current_summary_version`
- `working_draft`
- `tool_events`
- `last_updated_at`

### 12.2 Global Agent Session

Suggested shape:

- `session_id`
- `chat_history`
- `retrieved_meeting_ids`
- `working_notes`
- `user_context`
- `last_updated_at`

### 12.3 Context Strategy

Avoid sending everything on every turn.

Use three layers:

1. System instructions
2. Working context
   - current user input
   - current summary
   - recent conversation
3. Tool-driven retrieval
   - transcript excerpts
   - historical summaries
   - search results

## 13. UX Proposal

### 13.1 Meeting Agent UX

Place the assistant in the right summary panel:

- Keep Generate button and template selection
- Add a chat input below the summary
- Show assistant messages
- Show tool execution status
- Show draft update notifications
- Show version updates

### 13.2 Global Agent UX

Add an app-level entry point:

- "Ask AudioAssist" in top bar or sidebar
- Separate assistant panel or overlay
- Supports app-wide search and synthesis

## 14. Frontend Event Contract

### 14.1 Meeting Agent Events

- `onSummaryAgentTurnStarted(jobId, turnId)`
- `onSummaryAgentMessageDelta(jobId, turnId, textChunk)`
- `onSummaryAgentToolStart(jobId, turnId, toolName, input)`
- `onSummaryAgentToolResult(jobId, turnId, toolName, resultPreview)`
- `onSummaryAgentDraftUpdated(jobId, draftText)`
- `onSummaryAgentTurnComplete(jobId, turnId, finalMessage, savedVersion)`
- `onSummaryAgentError(jobId, turnId, message)`

### 14.2 Global Agent Events

- `onGlobalAgentTurnStarted(sessionId, turnId)`
- `onGlobalAgentMessageDelta(sessionId, turnId, textChunk)`
- `onGlobalAgentToolStart(sessionId, turnId, toolName, input)`
- `onGlobalAgentToolResult(sessionId, turnId, toolName, resultPreview)`
- `onGlobalAgentTurnComplete(sessionId, turnId, finalMessage)`
- `onGlobalAgentError(sessionId, turnId, message)`

## 15. Backend API Proposal

### 15.1 Meeting Agent APIs

- `start_summary_agent_turn(job_id: str, user_input: str) -> dict`
- `get_summary_agent_session(job_id: str) -> dict`
- `clear_summary_agent_session(job_id: str) -> bool`
- `patch_summary_section(job_id: str, section: str, instruction: str) -> dict`

### 15.2 Global Agent APIs

- `create_global_agent_session() -> dict`
- `start_global_agent_turn(session_id: str, user_input: str) -> dict`
- `get_global_agent_session(session_id: str) -> dict`
- `clear_global_agent_session(session_id: str) -> bool`

### 15.3 Search APIs

- `search_meetings(query: str, filters: dict | None = None) -> list[dict]`
- `search_transcripts(query: str, filters: dict | None = None) -> list[dict]`
- `search_summaries(query: str, filters: dict | None = None) -> list[dict]`

## 16. Rollout Plan

### Phase 1: Meeting Agent MVP

Scope:

- Meeting-level agent only
- Multi-turn editing
- Section rewrite
- Summary Q&A
- Summary versioning
- Basic session persistence

Out of scope:

- App-wide search
- Cross-meeting synthesis
- Web search
- Long-term preference memory

### Phase 2: Global Search and Synthesis

Scope:

- Global assistant entry point
- Search across meetings
- Summarize multiple meetings
- Aggregate action items

### Phase 3: Long-Term Memory and Advanced Analysis

Scope:

- User preference memory
- Incremental summarization
- Topic/project/customer views
- Optional web tools

## 17. Risks

### 17.1 Provider Compatibility

OpenAI Agents SDK can support non-OpenAI models, but provider behavior may differ for:

- tool calling
- structured outputs
- tracing
- response formatting

The team must validate the target providers before committing to a broad compatibility promise.

### 17.2 Token Growth

Cross-session memory, transcript excerpts, and retrieved meetings can grow quickly.

Mitigations:

- truncation
- summary compression
- retrieval on demand

### 17.3 Data Boundary Confusion

Transcript is source truth. Summary is derived. Agent memory is separate.

Do not mix these assets into one file shape.

### 17.4 User Trust

Users may over-trust the assistant. The UI and prompts should make clear:

- the agent should not invent facts
- external sources must be surfaced
- uncertain data should trigger clarification

## 18. Success Criteria

### 18.1 Meeting Agent Success

- Users can iteratively refine summaries without full regeneration
- Summary edits feel controllable and understandable
- Version history remains reliable
- Meeting Q&A accuracy is acceptable

### 18.2 Global Agent Success

- Users can retrieve relevant historical meetings conversationally
- Cross-meeting summaries are useful and concise
- Action item aggregation provides clear operational value

## 19. Recommended First Build

The first build should focus on the smallest valuable slice:

1. Meeting-level interactive summary agent
2. Summary editing via chat
3. Section rewrite
4. Session persistence
5. Version save + restore

This creates user-visible value quickly while preserving a clean path toward app-wide assistant behavior later.
