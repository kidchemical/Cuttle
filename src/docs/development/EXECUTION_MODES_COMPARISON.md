# Execution Modes & Graph Visualization Comparison

## Agent Execution Modes

Your project now has **three execution strategies** that are consistently represented across the node editor and query reports.

### 1. Single Stage Mode
**Direct LLM Processing** - No evaluation, just execute

```
📥 User Input
     ↓
🤖 Direct LLM Processing
   (gpt-4o-mini)
     ↓
📤 Response
```

**Use case**: Simple, fast responses where you always want LLM processing

**Query Report Badge**: `Single Stage (Direct LLM)`

---

### 2. Multi-Stage Mode (Experimental)
**Full Pipeline** - Regex → Evaluation → Execution

```
📥 User Input
     ↓
🔍 Regex Pattern Matching
     ↓
🤖 LLM Command Evaluation
   "Is this a command?"
     ↓
   ┌─────┴─────┐
   │           │
🤖 Command    🤖 Conversation
Execution    Response
   │           │
   └─────┬─────┘
         ↓
    📤 Response
```

**Use case**: When you need smart routing between commands and conversation

**Query Report Badge**: `Multi-Stage (Regex → Evaluation → Execution)`

---

### 3. Multi-Lite Mode (Default) ⭐
**Efficient Hybrid** - Regex → Combined LLM

```
📥 User Input
     ↓
🔍 Regex Pattern Matching
   (Fast pattern check)
     ↓
🤖 Combined LLM Processing
   (Evaluation + Response in one call)
     ↓
📤 Response
```

**Use case**: Best balance of speed and intelligence (recommended)

**Query Report Badge**: `Multi-Lite (Regex → Combined LLM)`

---

## Graph Visualization: Before vs After

### BEFORE: Linear Graph Only
Query reports showed a simple linear flow regardless of actual execution:

```
Stage 1
  ↓
Stage 2
  ↓
Stage 3
  ↓
Stage 4
```

**Problems:**
- ❌ Didn't show parallel execution
- ❌ Couldn't see branching logic
- ❌ No indication of agent pools
- ❌ Didn't match node editor structure

---

### AFTER: True Graph Representation

#### For Node Editor Pipelines
Shows the **actual graph structure** from the node editor:

```
    🎯 Manual Trigger
          ↓
    🤖 LLM Coordinator
          ↓
    ┌─────┴─────┐
    │           │
🤖 Expert A  🤖 Expert B
    │           │
    └─────┬─────┘
  ⚡ Parallel Execution
          ↓
    🤖 Aggregator
          ↓
    📤 Discord Output
```

**Features:**
- ✅ Shows parallel execution paths
- ✅ Indicates branching/merging
- ✅ Color-coded by node type
- ✅ Execution order badges (#1, #2, #3...)
- ✅ Success/error status indicators
- ✅ Timing information

#### For Discord/Web UI
Shows the **execution mode strategy** with stages:

```
Multi-Lite (Regex → Combined LLM)
─────────────────────────────────

📥 Input: Discord Channel
         ↓
🔍 Regex Pattern Matching
   0.001s | 0 patterns matched
         ↓
🤖 Combined LLM Processing
   gpt-4o-mini | 1.234s
   147 tokens
         ↓
📤 Output: Discord Response
```

---

## Configuration Locations

### 1. Agent Stage Mode (System-wide)
**File**: `src/bot_config.json`
```json
{
  "agent_stage_mode": "multi-lite"
}
```

**Web UI**: Landing Page → Settings → Agent Configuration
- Single Stage
- Multi Stage
- Multi-Lite (Default) ⭐

### 2. Node Editor (Per-pipeline)
**Visual Flow**: Shown in node_editor.html graph
- Drag and drop nodes
- Connect with wires
- Run to see execution graph in query report

---

## Query Report Sections

### 1. Summary Card
Shows high-level execution info:
- ✅/❌ Success status
- Execution stages count
- LLM calls count
- Tool calls count
- Total tokens
- Estimated cost
- Total execution time

### 2. Agent Execution Graph
**NEW**: Now accurately represents:
- Node editor pipeline flow
- Parallel execution indicators
- Execution mode for Discord/Web UI
- Branching and merging logic

### 3. Agent Configuration
Shows execution settings:
- **Node Editor**: Pipeline name, node count, models used
- **Discord/Web UI**: Agent stage mode, LLM model, system prompt

### 4. Cost Breakdown
Detailed cost analysis by model

### 5. Execution Stages
Chronological list of all stages with timing

### 6. LLM Calls
Detailed token usage per call

### 7. Tool Calls
Parameters and results for each tool

---

## Visual Indicators in Graph

### Node Colors (Border)
- 🟣 **Purple**: Trigger nodes
- 🔵 **Blue**: LLM nodes
- 🟠 **Orange**: Tool nodes
- 🟢 **Green**: Output nodes
- 🩷 **Pink**: Group nodes
- 🟣 **Indigo**: Utility nodes

### Node Status (Border Style)
- **Solid Green**: Executed successfully
- **Solid Red**: Execution failed
- **Dashed Gray**: Not executed

### Special Indicators
- **#1, #2, #3...**: Execution order badges
- **⚡ Parallel Execution**: Multiple nodes ran simultaneously
- **⏱️ 1.23s**: Execution duration

---

## Examples

### Example 1: Simple Linear Pipeline
```
🎯 Manual Trigger (#1)
        ↓
🤖 OpenAI Node (#2)
   gpt-4o-mini
   ⏱️ 1.45s
        ↓
📤 Log Output (#3)
   ⏱️ 0.02s
```

### Example 2: Agent Pool (Parallel)
```
    🎯 Webhook (#1)
          ↓
    🤖 Coordinator (#2)
          ↓
    ┌─────┴─────┐
    │           │
🤖 Code      🤖 Design
Expert      Expert
(#3)        (#4)
    │           │
    └─────┬─────┘
  ⚡ Parallel Execution
          ↓
    🤖 Final Review (#5)
          ↓
    📤 Webhook (#6)
```

### Example 3: Multi-Lite Discord
```
Multi-Lite (Regex → Combined LLM)

📥 Input: Discord Channel
         ↓
🔍 Regex Pattern Matching
   ⏱️ 0.001s
         ↓
🤖 Combined LLM Processing
   gpt-4o-mini
   ⏱️ 1.234s
   📊 147 tokens
         ↓
📤 Output: Discord Response
```

---

## Key Improvements

1. **Consistency**: Query report graph matches node editor structure
2. **Transparency**: Execution mode clearly shown
3. **Debugging**: Easy to spot parallel execution and bottlenecks
4. **Performance**: See which nodes take longest
5. **Error Tracking**: Failed nodes highlighted immediately

---

## Choosing the Right Mode

### Use **Single Stage** when:
- ❓ You always want LLM processing
- ❓ Speed is not critical
- ❓ Commands should be handled like conversation

### Use **Multi Stage** when:
- ❓ You need explicit command vs conversation detection
- ❓ Different handling for each type
- ❓ You want fine-grained control (experimental)

### Use **Multi-Lite** when: ⭐
- ✅ You want good performance
- ✅ You need pattern matching + LLM fallback
- ✅ You want one LLM call instead of multiple
- ✅ **This is the recommended default**

---

## Testing Your Setup

1. **Check current mode**:
   ```python
   # In bot_config.json
   "agent_stage_mode": "multi-lite"
   ```

2. **Test in Discord/Web UI**:
   - Send a message
   - Check query report link
   - Verify execution mode badge
   - See linear stage flow

3. **Test in Node Editor**:
   - Create pipeline with parallel paths
   - Run pipeline
   - Open query report
   - Verify graph matches node editor

4. **Verify parallel execution**:
   - Create pipeline: Trigger → LLM → (Expert1, Expert2) → Aggregator
   - Run and check report shows "⚡ Parallel Execution"

---

## Conclusion

Your project now has:
✅ Three execution modes (single, multi, multi-lite)
✅ Consistent visualization across node editor and query reports
✅ LangGraph-style execution graphs
✅ Parallel execution indicators
✅ Detailed execution tracking

The system automatically chooses the right visualization:
- **Node Editor**: Graph structure from pipeline
- **Discord/Web UI**: Execution mode stages

All working together to give you complete visibility into how your agent processes requests! 🎉

