# Requirements Document

## Introduction

This feature adds a built-in, fully automatic token-saving capability to the Omni-Dev CLI coding agent. The agent reduces the number of tokens it sends to and receives from language models transparently, as an always-on optimization layer woven into the agent loop (`src/agent/core.py`). The user never invokes a manual command or tool to benefit from it — there is no manual `/compact`, `/savings`, or equivalent required. Optimizations happen silently but remain observable through a lightweight indicator and tracked savings metrics.

The capability is composed of seven cooperating mechanisms:

1. **Tool-output trimming** — cap and truncate large tool outputs before they enter the context window, and deduplicate repeated outputs already present in history.
2. **Context offloading to memory** — when the context window grows large, summarize older conversation turns into the Cognee memory graph, drop the raw text from the live context, and recall detail on demand.
3. **Automatic conversation compaction** — trigger the existing AI-powered compaction (`src/commands/compact.py`) automatically once a configurable token budget is reached, with no user prompt.
4. **Automatic model routing of cheap subtasks** — route mechanical internal subtasks (summarization, classification, ranking) to a smaller, cheaper model via `src/model_router.py`.
5. **Prompt-cache awareness** — structure stable context as a cacheable prefix so providers that bill cached reads cheaply (e.g. Claude cache-read rates already present in `src/cost_tracker.py`) are hit.
6. **Transparency** — surface that optimization occurred without requiring user action, and track tokens-saved metrics.
7. **Configurability and safety** — thresholds are configurable; the layer must never drop information needed for correctness (offloaded detail must be recoverable), and must degrade gracefully when Cognee is unavailable.

A central design constraint is **correctness preservation**: every byte the optimization layer removes from the live context must either be reconstructable on demand (via Cognee recall or the SimpleMemory fallback) or be provably redundant (an exact duplicate or a truncation that records what was omitted). The layer must also fail safe: any internal failure in an optimization step leaves the agent's behavior unchanged rather than blocking the turn or losing data.

This feature builds on existing components and does not re-specify them: `CostTracker` token/cost accounting and thresholds, the `compact_command` summarization flow, the Cognee/SimpleMemory memory layer, and the `model_router` routing primitives. New requirements describe the automatic optimization layer that orchestrates these pieces.

## Glossary

- **Omni_Dev_Agent**: The agent engine in `src/agent/core.py` (`OmniDevAgent`) whose iterative request/tool-execution/response cycle (`execute_task`) the optimization layer is woven into.
- **Token_Optimizer**: The always-on optimization layer added by this feature that applies token-saving mechanisms within the Omni_Dev_Agent loop without user invocation.
- **Context_Window**: The ordered list of messages (`OmniDevAgent.messages`) sent to the model on each model call, including the system prompt, prior turns, tool results, and the current user prompt.
- **Tool_Output**: The string result produced by a built-in tool (e.g. `file_read_tool`, `grep_tool`, `ls_tool`, `bash_tool`) that is appended to the Context_Window as a tool result.
- **Tool_Output_Trimmer**: The Token_Optimizer sub-component that caps and truncates a Tool_Output before it enters the Context_Window.
- **Truncation_Marker**: A visible textual marker inserted in place of removed content recording how much was omitted (e.g. `... 142 more lines`).
- **Duplicate_Output**: A Tool_Output whose content is byte-for-byte identical to an earlier Tool_Output already present in the Context_Window.
- **Context_Offloader**: The Token_Optimizer sub-component that summarizes older conversation turns into the Memory_Store and removes their raw text from the Context_Window.
- **Memory_Store**: The persistent memory layer, comprising the Cognee hybrid graph-vector store (`src/tools/memory_tools.py`) as primary and the SimpleMemory JSON store (`src/simple_memory.py`) as offline fallback.
- **Offloaded_Content**: Raw conversation text that the Context_Offloader has summarized into the Memory_Store and removed from the Context_Window, recoverable on demand via recall.
- **Auto_Compactor**: The Token_Optimizer sub-component that triggers the existing `compact_command` automatically when the Token_Budget is reached.
- **Token_Budget**: The configurable cumulative-token threshold at which the Auto_Compactor triggers compaction.
- **Cost_Tracker**: The existing `CostTracker` singleton (`src/cost_tracker.py`) that tracks cumulative input/output tokens and per-model cost.
- **Model_Router**: The existing routing component (`src/model_router.py`) that normalizes model identifiers and selects provider configuration.
- **Cheap_Subtask**: An internal, mechanical model call made by the Token_Optimizer itself (summarization for offloading, classification, or ranking) that does not require the user's primary model.
- **Small_Model**: A smaller, lower-cost model selected by the Model_Router to service a Cheap_Subtask.
- **Cacheable_Prefix**: The stable, slowly-changing leading portion of the Context_Window (system instruction plus injected static context) structured so prompt-caching providers can serve it at cached-read rates.
- **Savings_Metrics**: The cumulative count of tokens saved by the Token_Optimizer, attributed by mechanism, tracked for the session.
- **Optimization_Indicator**: The lightweight, non-blocking visual signal shown to the user indicating that an optimization occurred.
- **Optimizer_Config**: The set of configurable settings governing the Token_Optimizer, persisted via `src/config_store.py` (the Global_Config), including enablement flags and thresholds.
- **Global_Config**: The persisted global configuration store (`src/config_store.py`) through which Optimizer_Config values are read and written.
- **Token_Estimate**: An approximate token count for a string or message computed by the Token_Optimizer without issuing a model call.

## Requirements

### Requirement 1: Always-on automatic operation

**User Story:** As a CLI user, I want token-saving to happen automatically without invoking any command, so that I save tokens and cost with zero manual effort.

#### Acceptance Criteria

1. WHILE the Token_Optimizer is enabled, THE Omni_Dev_Agent SHALL apply the enabled token-saving mechanisms on each iteration of the agent loop before the Context_Window is sent to the model, without requiring any user command or tool invocation.
2. WHEN the Omni_Dev_Agent initializes and no valid Optimizer_Config enablement value is present, THE Token_Optimizer SHALL default to enabled.
3. WHERE the Optimizer_Config disables the Token_Optimizer, THE Omni_Dev_Agent SHALL send the Context_Window unmodified by the Token_Optimizer.
4. THE Omni_Dev_Agent SHALL NOT expose a user-invoked command or tool whose sole purpose is to trigger the Token_Optimizer.
5. IF a Token_Optimizer mechanism raises an error during a turn, THEN THE Omni_Dev_Agent SHALL complete the turn using the un-optimized content for that mechanism without aborting the turn and without discarding any Context_Window content.
6. IF a Token_Optimizer mechanism raises an error during a turn, THEN THE Token_Optimizer SHALL continue to apply the remaining enabled mechanisms during that turn.

### Requirement 2: Tool-output trimming

**User Story:** As a CLI user, I want large tool outputs trimmed before they enter context, so that verbose file reads, searches, and command output do not waste tokens.

#### Acceptance Criteria

1. WHEN a Tool_Output exceeds the configured maximum output size measured in characters (default 10,000 characters), THE Tool_Output_Trimmer SHALL truncate the Tool_Output so that its length including the inserted Truncation_Marker does not exceed the configured maximum output size before it enters the Context_Window.
2. WHEN the Tool_Output_Trimmer truncates a Tool_Output, THE Tool_Output_Trimmer SHALL insert a Truncation_Marker recording the exact number of omitted characters and, for line-oriented output, the exact number of omitted lines.
3. WHEN the Tool_Output_Trimmer truncates a Tool_Output, THE Tool_Output_Trimmer SHALL retain the leading and trailing portions around the omitted middle by splitting the retained character budget (the maximum output size minus the Truncation_Marker length) evenly between the leading and trailing portions, assigning any odd remaining character to the leading portion.
4. WHEN a file-read Tool_Output is requested for a specified line range, THE Tool_Output_Trimmer SHALL include only the requested line range in the Context_Window.
5. IF a file-read Tool_Output is requested for a line range that falls outside the file's available lines, THEN THE Tool_Output_Trimmer SHALL include only the available lines within the requested range and SHALL record the out-of-range portion with a Truncation_Marker.
6. WHEN a new Tool_Output is byte-for-byte identical to a Duplicate_Output already present in the Context_Window, THE Tool_Output_Trimmer SHALL replace the new Tool_Output with a reference to the earlier identical Tool_Output and SHALL keep that earlier occurrence in the Context_Window.
7. WHEN a Tool_Output's size is at or below the configured maximum output size, THE Tool_Output_Trimmer SHALL pass the Tool_Output into the Context_Window unchanged.

### Requirement 3: Context offloading to memory

**User Story:** As a CLI user, I want older conversation context offloaded to memory as the window grows, so that long sessions stay within budget while detail remains recoverable.

#### Acceptance Criteria

1. WHEN the Token_Estimate of the Context_Window exceeds the configured offload threshold, THE Context_Offloader SHALL summarize conversation turns older than the configured retention window into the Memory_Store, offloading oldest-turn-first until the Token_Estimate is at or below the configured offload threshold or no eligible turns remain.
2. WHEN the Memory_Store confirms a successful write of Offloaded_Content, THE Context_Offloader SHALL remove the corresponding raw text from the Context_Window.
3. WHEN the Context_Offloader removes raw text from the Context_Window, THE Context_Offloader SHALL retain a summary reference in the Context_Window identifying the Offloaded_Content for later recall.
4. THE Context_Offloader SHALL retain the system message and the most recent conversation turns within the configured retention window, measured as a count of most recent conversation turns, in the Context_Window.
5. WHEN the Omni_Dev_Agent processes a summary reference for previously Offloaded_Content, THE Omni_Dev_Agent SHALL retrieve the Offloaded_Content from the Memory_Store via recall.
6. IF the Memory_Store does not confirm a successful write during offloading, THEN THE Context_Offloader SHALL retain the raw text in the Context_Window unchanged.

### Requirement 4: Automatic conversation compaction

**User Story:** As a CLI user, I want the conversation compacted automatically at a token budget, so that I never have to run /compact and never hit the model context limit.

#### Acceptance Criteria

1. WHEN the cumulative session token count reported by the Cost_Tracker reaches or exceeds the Token_Budget, evaluated before the Context_Window is sent to the model for a user turn, THE Auto_Compactor SHALL invoke the existing compaction summarization flow without requesting user confirmation.
2. WHEN the compaction summarization flow returns a compacted message list, THE Auto_Compactor SHALL replace the Context_Window with that compacted message list.
3. WHEN the Auto_Compactor replaces the Context_Window, THE Auto_Compactor SHALL retain the system message as the first message in the Context_Window.
4. WHEN the Auto_Compactor performs compaction, THE Auto_Compactor SHALL store the generated summary into the Memory_Store so that the pre-compaction detail is retrievable by a subsequent recall.
5. IF the compaction summarization flow returns an error, THEN THE Auto_Compactor SHALL leave the Context_Window unchanged and SHALL complete the turn without surfacing a blocking error.
6. IF the Memory_Store does not confirm a successful write of the generated summary, THEN THE Auto_Compactor SHALL leave the Context_Window unchanged.
7. WHILE a single user turn is being processed, THE Auto_Compactor SHALL trigger compaction at most one time.

### Requirement 5: Automatic model routing of cheap subtasks

**User Story:** As a CLI user, I want mechanical internal subtasks routed to a cheaper model, so that summarization and classification work does not cost premium-model rates.

#### Acceptance Criteria

1. WHEN the Token_Optimizer issues a Cheap_Subtask, THE Token_Optimizer SHALL request a Small_Model from the Model_Router for that Cheap_Subtask.
2. THE Token_Optimizer SHALL classify a subtask as a Cheap_Subtask if and only if it is a summarization subtask, a classification subtask, or a ranking subtask.
3. WHEN the Token_Optimizer routes a Cheap_Subtask to a Small_Model, THE Omni_Dev_Agent SHALL continue to use the user-selected model for the primary response.
4. IF the Model_Router cannot provide a Small_Model for a Cheap_Subtask, THEN THE Token_Optimizer SHALL execute that Cheap_Subtask using the user-selected model and SHALL return the resulting Cheap_Subtask output to the requesting mechanism.
5. WHEN the Token_Optimizer completes a Cheap_Subtask on a Small_Model, THE Token_Optimizer SHALL record the input token count and output token count of that Cheap_Subtask in the Cost_Tracker attributed to the Small_Model's cost rates.
6. IF a Cheap_Subtask executed on a Small_Model returns an error or returns no result, THEN THE Token_Optimizer SHALL re-execute that Cheap_Subtask using the user-selected model and SHALL return the resulting Cheap_Subtask output to the requesting mechanism.

### Requirement 6: Prompt-cache awareness

**User Story:** As a CLI user, I want stable context structured as a cacheable prefix, so that providers that bill cached reads cheaply reduce my repeat-context cost.

#### Acceptance Criteria

1. THE Token_Optimizer SHALL place the system instruction and injected static context at the start of the Context_Window as a Cacheable_Prefix preceding all dynamic content, including prior turns, tool results, and the current user prompt.
2. WHILE the injected static context is unchanged from the immediately preceding turn, THE Token_Optimizer SHALL keep the Cacheable_Prefix byte-for-byte identical to the immediately preceding turn.
3. WHEN the injected static context changes between turns, THE Token_Optimizer SHALL rebuild the Cacheable_Prefix from the current static context.
4. WHERE the active model provider bills cached-read tokens, THE Token_Optimizer SHALL mark the Cacheable_Prefix for prompt caching using the provider's caching mechanism.
5. WHERE the active model provider does not support prompt caching, THE Token_Optimizer SHALL send the Context_Window without cache markers.
6. IF marking the Cacheable_Prefix for prompt caching fails, THEN THE Token_Optimizer SHALL send the Context_Window without cache markers and complete the turn.
7. WHEN a model response reports cached-read token counts, THE Omni_Dev_Agent SHALL record those counts in the Cost_Tracker at the model's cached-read rate.
8. WHEN a model response reports cache-write token counts, THE Omni_Dev_Agent SHALL record those counts in the Cost_Tracker at the model's cache-write rate.

### Requirement 7: Transparency and observable savings

**User Story:** As a CLI user, I want to see that optimization happened and how much it saved, so that I trust the agent without it interrupting my workflow.

#### Acceptance Criteria

1. WHEN the Token_Optimizer applies an optimization during a turn, THE Omni_Dev_Agent SHALL display the Optimization_Indicator within that turn's output naming the mechanism that applied and the number of tokens saved, without requiring user action.
2. WHEN a session begins, THE Token_Optimizer SHALL initialize the Savings_Metrics for each mechanism to zero.
3. WHEN the Token_Optimizer saves tokens, THE Token_Optimizer SHALL compute the tokens saved as the Token_Estimate of the removed or avoided content.
4. WHEN the Token_Optimizer saves tokens, THE Token_Optimizer SHALL add the saved token count to the Savings_Metrics attributed to the specific mechanism that produced the saving.
5. THE Optimization_Indicator SHALL NOT block, pause, or require a response from the user.
6. WHEN the Token_Optimizer applies no optimization during a turn, THE Omni_Dev_Agent SHALL leave the Savings_Metrics unchanged for that turn.
7. WHEN the user requests the session cost summary, THE Omni_Dev_Agent SHALL include the cumulative Savings_Metrics total and the per-mechanism breakdown in the summary.

### Requirement 8: Configurability

**User Story:** As a CLI user, I want the optimization thresholds configurable, so that I can tune token-saving to my workflow and budget.

#### Acceptance Criteria

1. THE Optimizer_Config SHALL provide independently configurable values for the Token_Budget (in tokens, 1,000–1,000,000), the offload threshold (in tokens, 1,000–1,000,000), the retention window (in conversation turns, 1–100), and the maximum tool-output size (in characters, 100–1,000,000).
2. WHEN an Optimizer_Config value is absent, THE Token_Optimizer SHALL apply the documented default value for that setting.
3. WHEN the Token_Optimizer reads an Optimizer_Config value, THE Token_Optimizer SHALL read it from the Global_Config through the existing configuration store.
4. WHERE an individual optimization mechanism (Tool_Output_Trimmer, Context_Offloader, Auto_Compactor, Cheap_Subtask routing, or Cacheable_Prefix marking) is disabled in the Optimizer_Config, THE Token_Optimizer SHALL skip only that mechanism while applying the remaining enabled mechanisms.
5. IF an Optimizer_Config value is present but invalid, where invalid means non-numeric or outside its documented range, THEN THE Token_Optimizer SHALL apply the documented default value for that setting.
6. IF the configured offload threshold is greater than or equal to the Token_Budget, THEN THE Token_Optimizer SHALL apply the documented default values for the offload threshold and the Token_Budget.

### Requirement 9: Correctness preservation

**User Story:** As a CLI user, I want the optimizer to never lose information the agent needs to be correct, so that token-saving never degrades the agent's answers.

#### Acceptance Criteria

1. WHEN the Token_Optimizer removes content from the Context_Window, THE Token_Optimizer SHALL ensure the removed content is either recoverable via a subsequent recall from the Memory_Store or recorded as omitted by a Truncation_Marker.
2. THE Token_Optimizer SHALL retain the system message in the Context_Window on every model call.
3. THE Token_Optimizer SHALL retain the current user prompt byte-for-byte identical in the Context_Window on every model call.
4. WHEN the Token_Optimizer replaces a Duplicate_Output with a reference, THE Token_Optimizer SHALL keep the first occurrence of that output content in the Context_Window.
5. WHEN the Token_Optimizer replaces a Duplicate_Output with a reference, THE reference SHALL identify and resolve to the retained first occurrence of that output content.
6. WHEN the Token_Optimizer offloads or compacts content into the Memory_Store, a subsequent recall of that content within the same session SHALL return the stored representation of that content.
7. IF removing content from the Context_Window would leave that content neither recoverable from the Memory_Store nor recorded by a Truncation_Marker, THEN THE Token_Optimizer SHALL retain that content in the Context_Window.

### Requirement 10: Graceful degradation when memory is unavailable

**User Story:** As a CLI user, I want the agent to keep working when Cognee is unavailable, so that an offline memory backend never breaks my session.

#### Acceptance Criteria

1. IF the Cognee memory backend is unavailable when the Context_Offloader attempts to store Offloaded_Content, THEN THE Context_Offloader SHALL store the content in the SimpleMemory fallback store and SHALL retain a summary reference in the Context_Window identifying the Offloaded_Content.
2. IF both the Cognee backend and the SimpleMemory fallback store are unavailable when offloading is attempted, THEN THE Context_Offloader SHALL skip offloading, retain the raw text in the Context_Window, and complete the turn without blocking.
3. WHILE the Cognee memory backend is unavailable, THE Auto_Compactor SHALL continue to compact the Context_Window and store the generated summary in the SimpleMemory fallback store.
4. IF a recall of Offloaded_Content returns no result, THEN THE Omni_Dev_Agent SHALL continue the turn using the summary reference retained in the Context_Window.
5. WHEN a Memory_Store operation returns an error or does not complete within the configured memory-operation timeout (default 5 seconds), THE Token_Optimizer SHALL treat the Memory_Store as unavailable.
