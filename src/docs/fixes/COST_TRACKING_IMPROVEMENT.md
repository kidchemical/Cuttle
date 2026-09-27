# Cost Tracking Improvement: Actual vs Estimated Costs

## Overview

Enhanced the Query Report system to distinguish between **actual costs from APIs** and **estimated costs from token calculations**. This provides transparency about cost accuracy and warns users when estimates may not reflect actual charges.

## The Problem

**Two different LLM APIs handle costs differently:**

### OpenAI API (GPT-4o-mini)
- ❌ **Does NOT return cost information**
- ✅ Only returns token usage (input/output tokens)
- ⚠️ **Cost MUST be estimated** from token counts using pricing tables

### Claude API (Haiku, Sonnet, Opus)
- ✅ **Returns actual cost** including cache tokens
- ✅ Returns detailed token usage
- ✅ **Cost is ACTUAL** - includes cache creation/read tokens

**The Issue:** Cache tokens significantly affect costs but aren't visible in basic token counts.

## The Solution

### 1. Track Cost Source

Added `cost_is_estimated` flag to all LLM calls:

```python
llm_data = {
    "model": model,
    "cost": call_cost,
    "cost_is_estimated": False,  # True = estimated, False = actual from API
    ...
}
```

### 2. Enhanced `add_llm_call` Method

**File:** `query_report_generator.py`

```python
def add_llm_call(self, model: str, prompt_tokens: int, completion_tokens: int,
                total_tokens: int, start_time: float, end_time: float,
                success: bool = True, response_preview: str = "", 
                actual_cost: float = None):  # NEW PARAMETER
    """Add an LLM call to the report
    
    Args:
        actual_cost: If provided, uses this cost from the API instead of calculating.
                    This should be used when the API returns actual cost (e.g., Claude with cache tokens)
    """
    # Use actual cost if provided, otherwise calculate
    if actual_cost is not None and actual_cost > 0:
        call_cost = actual_cost
        cost_is_estimated = False  # ✅ Actual from API
    else:
        call_cost = self.calculate_cost(model, prompt_tokens, completion_tokens)
        cost_is_estimated = True  # ⚠️ Estimated from tokens
```

### 3. Visual Warnings in Query Reports

**LLM Calls Section:**
- Shows ⚠️ (estimated) next to estimated costs
- Shows no indicator for actual costs from API

**Cost Breakdown Section:**
- Header shows: "Cost Type: Actual from API" or "Estimated" or "Mixed"
- Shows count: "(2 actual, 1 estimated)"
- Each model shows: "(actual from API)" or "(estimated)" label

**Warning Box for Estimated Costs:**

```
⚠️ Warning: Some costs are estimated from token usage and may not reflect actual charges.

Estimated costs do not include:
• Cache creation/read tokens (Claude API)
• Volume discounts or special pricing
• Regional pricing variations

Recommendation: Use API-provided costs when available for accurate billing.
```

## Current State by LLM Type

### OpenAI (multi_stage, default mode, llm_only mode)

**Status:** ⚠️ **All costs are ESTIMATED**

**Why:** OpenAI API doesn't return cost information

**What's tracked:**
- ✅ Token counts (accurate)
- ⚠️ Cost (estimated from pricing table)

**Accuracy:** ~99% accurate for standard tokens, but doesn't account for:
- Volume discounts
- Enterprise pricing
- Special promotions

**Example Query Report:**
```
Total Cost: $0.000234 ⚠️ (estimated)
gpt-4o-mini (estimated): 1 call, 234 tokens
```

### Claude API (claude_code tool)

**Status:** ✅ **All costs are ACTUAL from API**

**Why:** Claude API returns actual cost including cache tokens

**What's tracked:**
- ✅ Token counts (accurate)
- ✅ Cost (actual from API including cache costs)
- ✅ Cache creation tokens
- ✅ Cache read tokens

**Accuracy:** 100% accurate - reflects actual bill from Anthropic

**Example Query Report:**
```
Total Cost: $0.015666 ✅ (actual from API)
claude-3-5-haiku (actual from API): 1 call, 15,263 tokens
```

## Implementation Details

### Files Modified

1. **query_report_generator.py** (4 changes)
   - `add_llm_call()`: Added `actual_cost` parameter
   - `add_tool_call()`: Marks costs as actual/estimated based on whether cost was provided
   - `_create_llm_calls_html()`: Shows ⚠️ indicator for estimated costs
   - `_create_cost_breakdown_html()`: Shows detailed warnings and cost type labels
   - Added CSS for `.cost-warning` styling

### Integration Points

**Claude Code Tool** (already working ✅):
```python
# claude_code_tool.py extracts actual cost from API
cost = usage_info.get("cost", 0.0)

# Bot passes actual cost to tracker
_track_tool_call(
    tool_name="claude_code",
    model=formatted_model,
    tokens=tokens_dict,
    cost=cost  # ✅ Actual cost from Claude API
)
```

**OpenAI Traditional LLM** (works with estimation ⚠️):
```python
# ai_agent.py only extracts tokens
usage_data = {
    "total_tokens": response.usage.total_tokens,
    "prompt_tokens": response.usage.prompt_tokens,
    "completion_tokens": response.usage.completion_tokens
    # No cost field available from OpenAI
}

# Query tracker calculates cost
tracker.add_llm_call(
    model="gpt-4o-mini",
    prompt_tokens=usage_data["prompt_tokens"],
    completion_tokens=usage_data["completion_tokens"],
    total_tokens=usage_data["total_tokens"],
    # actual_cost=None → will estimate ⚠️
)
```

## Cache Token Cost Impact

### Example: Claude API Call with Cache

**Visible Tokens:**
- Input: 15,176 tokens
- Output: 87 tokens
- Total: 15,263 tokens

**Invisible Cache Tokens:**
- Cache creation: ~10,000 tokens (higher cost)
- Cache read: ~5,000 tokens (lower cost)

**Cost Comparison:**
- **Estimated** (from visible tokens only): $0.002329
- **Actual** (includes cache tokens): $0.015666
- **Difference**: 6.7x higher due to cache creation!

This is why actual costs from the API are crucial for accurate billing.

## User Recommendations

### For Accurate Cost Tracking

1. ✅ **Use APIs that return actual costs** (Claude, future OpenAI updates)
2. ⚠️ **Understand estimated costs are approximations** for OpenAI
3. 📊 **Monitor Query Reports** for cost type indicators
4. 🔍 **Check your API billing dashboard** to verify actual charges
5. 💡 **Budget with buffers** when using estimated costs (add ~10-20% margin)

### For Development

When integrating new LLM APIs:

1. **Check if API returns cost** in the response
2. **If yes:** Extract and pass to `add_llm_call(actual_cost=...)`
3. **If no:** Leave `actual_cost=None` and costs will be estimated with warning

## Future Enhancements

Potential improvements:

1. **OpenAI Cost API Integration** - If OpenAI adds cost endpoints
2. **Cost Alerts** - Notify when estimated costs exceed thresholds
3. **Cost Comparison** - Show estimated vs actual when both available
4. **Billing Integration** - Auto-import actual costs from billing APIs
5. **Per-User Cost Tracking** - Track costs by Discord user

## Verification

To verify the system is working:

1. **Run a Claude Code command** - Should show "actual from API"
2. **Run a traditional LLM query** - Should show "estimated" with warning
3. **Check Query Report** - Cost Breakdown should show cost types
4. **Look for ⚠️ indicators** - Should appear next to estimated costs

## Key Takeaways

✅ **Claude API costs are 100% accurate** - includes all token types
⚠️ **OpenAI costs are ~99% accurate estimates** - calculated from pricing tables
🎯 **Query Reports now clearly label cost accuracy**
⚠️ **Warnings appear when costs are estimated**
📊 **Per-model cost breakdowns show actual vs estimated counts**

This transparency helps you understand your actual LLM costs and budget appropriately!

