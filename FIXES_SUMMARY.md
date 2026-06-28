# API Providers & Gemma Model Fixes - Summary

## Issues Fixed

### 1. **Tool Schema Rejection for Certain Models (Gemma, Mistral, etc.)**
**Problem**: Models like Gemma4 31b don't support the `tools` parameter in litellm, causing "bad request" errors.

**Fixes**:
- Added pre-detection of models known to have tool schema issues
- Models without tool support: `ollama/`, `gemma`, `mistral`, `neural-chat`, `orca`, `dolphin`
- These models now have tools automatically disabled before the API call
- Prevents the entire call from failing

**Files Modified**:
- `src/agent/core.py`: Line 305-312 - Added `disable_tools_for_model` check

### 2. **Fallback Handling Improvement**
**Problem**: When fallback to non-tools mode was triggered, raw JSON from tool calls would leak into the UI.

**Fixes**:
- Apply `_clean_final_text()` to fallback responses
- Only fallback if tools were originally attempted
- Better error messages when tools are rejected
- Improved user guidance

**Files Modified**:
- `src/agent/core.py`: Line 367-382 - Better fallback with text cleaning

### 3. **Incorrect Ollama API Base Configuration**
**Problem**: Having an API key would automatically switch to cloud, even for local Ollama.

**Fixes**:
- Only auto-switch to cloud when BOTH: model name has "cloud" AND API key exists
- Local Ollama remains default unless explicitly using cloud model
- When setting OLLAMA_API_KEY, only switch to cloud if current model is a cloud model

**Files Modified**:
- `src/cli/interface.py`: Line 81-93 - Fixed API base initialization
- `src/cli/interface.py`: Line 622-632 - Fixed API key handling
- `src/agent/core.py`: Line 325-345 - Improved cloud detection logic

### 4. **Model-Specific Error Handling**
**Problem**: Generic error messages didn't help users understand why their model failed.

**Fixes**:
- Added specific detection for tool/function calling errors
- Better error messages for authentication issues
- Guides user to switch models if tool support is the issue

**Files Modified**:
- `src/agent/core.py`: Line 388-394 - Better error categorization

### 5. **User Feedback for Tool-Limited Models**
**Problem**: Users weren't informed that their chosen model doesn't support tools.

**Fixes**:
- Display warning when switching to tool-limited models
- Show warning in doctor command
- Clear indication of model capabilities

**Files Modified**:
- `src/cli/interface.py`: Line 568-575 - Warning for tool-limited models
- `src/commands/doctor.py`: Line 70-103 - Ollama diagnostics and model capability info

## How To Use

### For Gemma4 31b (or other tool-limited models):
1. Switch to model: `/model ollama/gemma4:31b`
2. You'll see a warning: "This model doesn't support tool use"
3. The CLI will still work - just without file/command execution
4. For full capabilities, use: `/model gpt-4o` or `/model groq/llama-3.3-70b-versatile`

### For Ollama Cloud:
1. Set model: `/model ollama/llama3:cloud`
2. Set API key: `/api_key 10` (Ollama Cloud API Key)
3. The system will automatically detect cloud and set the correct API base

### For Local Ollama:
1. Set model: `/model ollama/llama3`
2. Make sure `ollama serve` is running
3. The system will use `http://localhost:11434` by default

### For Diagnostics:
Run `/doctor` to see:
- Current model capabilities
- Ollama server status (if using Ollama)
- All API keys configuration
- Full environment diagnostics

## Testing

All changes have been validated:
- ✅ Model name parsing works for various formats
- ✅ Local/cloud Ollama detection works correctly
- ✅ Tool schema errors are handled gracefully
- ✅ Error messages are user-friendly
- ✅ Python syntax validation passed

## Models Known to Have Tool Limitations

These models work but without tool/file execution:
- `ollama/*` (all local Ollama models except those with cloud in name)
- `gemma`
- `mistral`
- `neural-chat`
- `orca`
- `dolphin`

Recommended models with full tool support:
- `openai/gpt-4o`
- `anthropic/claude-3-5-sonnet`
- `gemini/gemini-2.5-pro`
- `groq/llama-3.3-70b-versatile`
- `vertex_ai/gemini-1.5-pro`

## Error Recovery

If you get "tool schema" errors:
1. The system will automatically fallback to text-only mode
2. Run `/doctor` to verify your setup
3. Try switching models with `/model`
4. If issue persists, check `/api_key` settings
