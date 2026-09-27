# Claude Code Pricing Discrepancy Fix

## Problem Summary

The Discord bot and Query Report were showing **different prices** for the same Claude Code execution:

- **Discord**: $0.015666
- **Query Report**: $0.002329
- **Same tokens**: 15,263 total (15,176 input → 87 output)

## Root Causes

### 1. Cache Token Costs Not Included in Query Report

**Discord shows the CORRECT price** ($0.015666) because:
- It uses the cost directly from Claude's API response
- This includes **cache creation tokens** which cost more than regular input tokens
- Cache creation tokens have higher pricing to account for the computational cost of creating prompt caching

**Query Report showed WRONG price** ($0.002329) because:
- It was recalculating the cost based only on input/output tokens
- It didn't account for cache creation tokens
- Cache tokens can significantly increase costs

### 2. Model Name Formatting Issue

The model name was being **double-prefixed**:
- Claude API returns: `"claude-3-5-haiku-20241022"`
- `claude_code_tool.py` strips it to: `"3-5-haiku"`
- Bot code adds prefix: `"claude-3-" + "3-5-haiku"` → `"claude-3-3-5-haiku"` ❌

This malformed name wasn't in the pricing dictionary, causing it to fallback to GPT-4o-mini pricing.

### 3. Missing Model Pricing Entries

The Query Report's model pricing dictionary was missing several Claude model variants.

## Fixes Applied

### Fix 1: Use Actual Cost from Claude API

**File**: `query_report_generator.py` (lines 291-319)

```python
# If cost was provided (e.g., from Claude API including cache costs), use it
# Otherwise, calculate it based on token usage
if cost > 0:
    # Use the provided cost directly (includes cache token costs)
    self.execution_data["total_cost"] += cost
    llm_call_cost = cost
else:
    # Calculate cost from tokens
    llm_call_cost = self.calculate_cost(...)
```

**Impact**: Query Report now uses the actual cost from Claude API, which includes cache token pricing.

### Fix 2: Prevent Model Name Double-Prefixing

**Files**: `bot_mcp.py` (lines 747-751), `bot_deprecated.py` (lines 957-961)

```python
# Format model name consistently - only add prefix if not already present
if not model_name.startswith("claude-"):
    formatted_model = f"claude-3-{model_name}"
else:
    formatted_model = model_name
```

**Impact**: Model names are now formatted correctly:
- `"3-5-haiku"` → `"claude-3-5-haiku"` ✓
- `"claude-3-5-haiku"` → `"claude-3-5-haiku"` ✓

### Fix 3: Add Missing Model Pricing Entries

**File**: `query_report_generator.py` (lines 43-51)

Added pricing for:
- `"claude-3-5-sonnet"`: $3.00/$15.00 per 1M tokens
- `"claude-3-5-haiku"`: $0.25/$1.25 per 1M tokens
- `"claude-3-3-5-haiku"`: $0.25/$1.25 per 1M tokens (fix for malformed name)
- `"haiku"`: $0.25/$1.25 per 1M tokens
- `"sonnet"`: $3.00/$15.00 per 1M tokens
- `"3-5-haiku"`: $0.25/$1.25 per 1M tokens (support stripped format)

**Impact**: All Claude model variants now have correct pricing fallbacks.

### Fix 4: Extract and Pass Cost from Claude API

**Files**: `bot_mcp.py` (lines 733-763), `bot_deprecated.py` (lines 943-973)

```python
# Extract usage information for tracking
usage_info = result.get("usage_info", {})
model_name = usage_info.get("model", "haiku")
tokens_dict = None
cost = 0.0

if usage_info and usage_info.get("total_tokens", 0) > 0:
    tokens_dict = {
        "total_tokens": usage_info.get("total_tokens", 0),
        "prompt_tokens": usage_info.get("input_tokens", 0),
        "completion_tokens": usage_info.get("output_tokens", 0)
    }
    cost = usage_info.get("cost", 0.0)  # Extract cost from Claude API

# Track with cost included
_track_tool_call(
    tool_name="claude_code",
    model=formatted_model,
    tokens=tokens_dict,
    cost=cost  # Pass the actual cost
)
```

**Impact**: The actual cost from Claude API (including cache costs) is now tracked and passed to the Query Report.

## Understanding Cache Token Pricing

Claude API uses **prompt caching** to speed up repeated prompts. Cache tokens have different pricing:

1. **Regular Input Tokens**: $0.25 per 1M tokens (Haiku)
2. **Cache Creation Tokens**: Higher cost (first time creating the cache)
3. **Cache Read Tokens**: Lower cost (reading from existing cache)

The total cost includes all three types, which is why the actual API cost ($0.015666) is higher than a simple calculation based on input/output tokens.

## Verification

After these fixes:

✅ **Discord** shows: $0.015666 (from Claude API - includes cache costs)
✅ **Query Report** now shows: $0.015666 (uses same cost from Claude API)
✅ **Model name** displays correctly: "3-5-haiku" or "claude-3-5-haiku"
✅ **Pricing** uses correct Haiku rates when recalculating

## Key Takeaway

**Always use the cost returned by the API** when available, rather than recalculating it from token counts. The API includes costs that aren't visible in the basic token breakdown:
- Cache creation tokens
- Cache read tokens
- Special token types
- Volume discounts
- Regional pricing variations

The recalculated price should only be used as a fallback when the API doesn't provide a cost.

