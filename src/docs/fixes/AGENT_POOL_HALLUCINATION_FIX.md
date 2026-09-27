# Agent Pool Hallucination Fix - Expert Response Accuracy

## Issue Summary

When testing the agent pool with a 1→3→1 chain (coordinator → 3 experts → aggregator), the following issues were observed:

1. **Coordinator** - Recognizes 3 agents but decides to use only 1 (expected behavior), but still provides instructions for 3 agents
2. **Expert Agent** - The single selected expert outputs a response describing 3 fictional team members (Alex Johnson, Maria Rodriguez, etc.) despite being the only agent executed
3. **Aggregator** - Works correctly

## Example from Query Report `adf8f315_20251012_131903`

### Call #1: Coordinator Planning
**Input:**
```
Available Expert Agents:
1. OpenAI: You are a helpful AI assistant.
2. OpenAI: You are a helpful AI assistant.
3. OpenAI: You are a helpful AI assistant.

User Request:
Introduce your team to me
```

**Output:**
```json
{
  "selectedExperts": [
    {
      "expertName": "OpenAI",
      "task": "Provide an introduction of the team members.",
      "context": "The user wants to know about the available expert agents."
    }
  ]
}
```

**Issue:** Only 1 expert is selected, but the task says "team members" (plural), causing confusion.

### Call #2: Expert Execution
**Input:**
```
Task: Provide an introduction of the team members.
Context: The user wants to know about the available expert agents.
```

**Output:**
```
Here's an introduction to our team members:

1. **Alex Johnson - Data Analyst**
   - Alex specializes in data interpretation...

2. **Maria Rodriguez - Software Developer**
   - A skilled software engineer...

3. **David Chen - Marketing Specialist**
   - David is our marketing guru...
```

**Issue:** The expert invented 3 fictional people because it was asked to describe "team members" (plural), but it doesn't know it's the only expert selected!

## Root Cause

### Problem 1: Coordinator Prompt Ambiguity
The coordinator prompt didn't emphasize that:
- Only selected experts will execute
- Task descriptions should match the number of experts selected
- Experts work independently

### Problem 2: Expert Prompt Lacks Context
The expert prompt didn't clarify:
- Whether it's the only expert or one of many
- That it shouldn't invent team members not mentioned in the context
- Its specific role in the delegation

## The Fix

### Fix 1: Enhanced Coordinator Instructions

**File:** `src/web/js/pipeline_executor.js` (lines 785-787)

```javascript
Important: Only the experts you select will execute. Be precise in your task descriptions:
- If you select 1 expert to introduce themselves, don't ask them to describe multiple team members
- Each expert works independently - they only know what you tell them in the task/context
```

**Impact:**
- Coordinator now understands that selecting 1 expert means only 1 will execute
- More precise task descriptions based on the number of experts selected
- Better alignment between available experts and task requirements

### Fix 2: Expert Execution Context

**File:** `src/web/js/pipeline_executor.js` (lines 904-915)

```javascript
// Clarify to expert whether it's solo or part of a team
const isOnlyExpert = plan.selectedExperts.length === 1;
const expertContext = isOnlyExpert 
    ? `You are the only expert selected for this task.`
    : `You are one of ${plan.selectedExperts.length} experts working on this task. Focus on your specific assignment.`;

const expertPrompt = `${expertContext}

Task: ${delegation.task}

Context: ${delegation.context}

Important: Complete only your assigned task. Do not invent or describe other team members unless they are specifically mentioned in the context.`;
```

**Impact:**
- Expert knows if it's working alone or as part of a team
- Explicit instruction not to invent team members
- Focuses expert on its specific assignment

## Expected Behavior After Fix

### Scenario: "Introduce your team to me" with 1 Expert Selected

**Coordinator Response:**
```json
{
  "selectedExperts": [
    {
      "expertName": "OpenAI",
      "task": "Introduce yourself as the AI assistant.",
      "context": "The user wants to meet the assistant."
    }
  ]
}
```

**Expert Response:**
```
Hello! I'm an AI assistant powered by OpenAI. I'm here to help you with a wide range of 
tasks including answering questions, providing information, assisting with problem-solving, 
and much more. I work independently to provide you with accurate and helpful responses 
tailored to your needs.
```

**Aggregator Response:**
```
Hello! I'm an AI assistant from OpenAI, ready to assist you with various tasks like answering 
questions and problem-solving. I work independently to provide accurate, helpful responses 
tailored to your specific needs.
```

### Scenario: "Introduce your team to me" with 3 Experts Selected

**Coordinator Response:**
```json
{
  "selectedExperts": [
    {
      "expertName": "OpenAI",
      "task": "Introduce yourself as Expert 1 - General Assistant",
      "context": "You are the first of 3 AI experts."
    },
    {
      "expertName": "OpenAI",
      "task": "Introduce yourself as Expert 2 - Technical Specialist",
      "context": "You are the second of 3 AI experts."
    },
    {
      "expertName": "OpenAI",
      "task": "Introduce yourself as Expert 3 - Creative Assistant",
      "context": "You are the third of 3 AI experts."
    }
  ]
}
```

**Each Expert Response:**
- Expert 1: Introduces itself as a general assistant
- Expert 2: Introduces itself as a technical specialist
- Expert 3: Introduces itself as a creative assistant

**Aggregator Response:**
Synthesizes all 3 expert introductions into a cohesive team overview.

## Benefits

1. ✅ **Eliminates hallucination** - Experts don't invent fictional team members
2. ✅ **Accurate task delegation** - Coordinator gives precise tasks based on selection
3. ✅ **Clear expert context** - Experts know their role (solo vs. team)
4. ✅ **Better user experience** - Responses match the actual agent configuration
5. ✅ **Scalable pattern** - Works correctly with 1, 2, 3, or more experts

## Testing

### Test Case 1: Single Expert
```
Input: "Introduce your team to me"
Expected: Single expert introduces itself only
```

### Test Case 2: Multiple Experts
```
Input: "Introduce your team to me"
Expected: Each expert introduces itself, aggregator synthesizes
```

### Test Case 3: Task Without Team Context
```
Input: "What is 2+2?"
Expected: Single expert answers the question directly
```

## Related Issues

- Agent pool hallucination (inventing team members)
- Coordinator task descriptions not matching expert selection
- Experts lacking context about their role in delegation

## Files Modified

1. ✅ `src/web/js/pipeline_executor.js` - Coordinator and expert prompt enhancements
2. ✅ `src/docs/fixes/AGENT_POOL_HALLUCINATION_FIX.md` - This document

## Date

October 12, 2025

## Status

✅ FIXED - Coordinator and experts now have clear context about execution

