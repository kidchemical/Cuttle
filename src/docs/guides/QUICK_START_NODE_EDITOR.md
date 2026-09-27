# Quick Start: Cuttle Node Graph Editor

A visual pipeline builder for creating AI agent workflows. Build powerful automation pipelines by connecting nodes together!

## 🚀 Launch the Editor

```bash
python start_node_editor.py
```

The editor will open automatically in your browser at `http://localhost:8080/node_editor.html`

## 📋 5-Minute Tutorial

### Step 1: Open an Example Pipeline

1. Click the **Open** button (📂) in the toolbar
2. Select "Screenshot Analysis Pipeline" 
3. Click to load it

You'll see a simple 4-node pipeline:
- Manual Trigger → Screenshot → OpenAI Analysis → Log Output

### Step 2: Configure the OpenAI Node

1. Click the **OpenAI** node (blue brain icon 🧠)
2. Properties panel opens on the right
3. Review the settings:
   - Model: gpt-4o-mini
   - System Prompt: "Analyze this screenshot..."
   - Temperature: 0.7

### Step 3: Run the Pipeline

1. Click the **Run** button (▶️) in the toolbar
2. Watch the execution:
   - Nodes turn blue (running) → green (success)
   - Execution log shows progress
   - Results appear in the log

### Step 4: Build Your Own Pipeline

1. Click **New** (📄) to create a fresh canvas
2. Drag nodes from the left palette:
   - Start with a **Manual Trigger** (under Triggers)
   - Add a **Process Manager** (under Tools)
   - Add a **Log Output** (under Outputs)
3. Connect them:
   - Click output port (right side) of Trigger
   - Drag to input port (left side) of Process Manager
   - Repeat for Process Manager → Log Output
4. Configure the Process Manager:
   - Click the node
   - Set "program" to "unity" or "cursor"
   - Add a project hint if needed
5. Save your pipeline:
   - Click **Save** (💾)
   - Enter a name
   - Click Save
   - **Tip**: Your viewport zoom and position are automatically saved, so when you reload the pipeline, you'll see it exactly as you left it!

## 🎯 Node Categories

### Triggers (Start Here)
- 👆 Manual Trigger
- 🌐 Webhook
- ⏰ Schedule
- 📁 File Watch

### AI/LLM
- 🧠 OpenAI (GPT models)
- 🔮 Anthropic (Claude models)
- 📝 Prompt Builder

### Tools (Cuttle Actions)
- 📸 Screenshot
- ⌨️ Input Control
- ⚙️ Process Manager
- 🪟 Window Control
- 👁️ OCR
- 🔧 Custom Tool

### Outputs (Results)
- 📋 Log
- 💾 File
- 💬 Discord
- 🔗 Webhook

### Groups (Advanced)
- 🧰 Toolbox (multiple tools)
- 🤖 AI Agent (LLM + Tools combo)
- ⚡ Parallel Execution
- 🔀 Conditional Branch

### Utilities
- 🔄 Transform Data
- 🔍 Filter
- 🔗 Merge
- ⏱️ Delay

## 💡 Common Patterns

### Pattern 1: Simple Automation
```
Trigger → Tool → Output
```
Example: Schedule → Screenshot → File Output

### Pattern 2: AI Analysis
```
Trigger → Tool → LLM → Output
```
Example: Manual → Screenshot → OpenAI → Discord

### Pattern 3: AI Agent
```
Trigger → AI Agent (LLM + Toolbox) → Output
```
The AI Agent node combines LLM reasoning with tool execution.

### Pattern 4: Conditional Flow
```
Trigger → Data → Conditional → [Path A | Path B]
```
Branch based on conditions.

### Pattern 5: Parallel Tasks
```
Trigger → Parallel → [Task 1, Task 2, Task 3] → Merge → Output
```
Run multiple operations concurrently.

## ⌨️ Essential Shortcuts

- `Ctrl+S` - Save pipeline
- `Ctrl+C/V` - Copy/Paste nodes
- `Delete` - Remove selected nodes
- `Shift+Click` - Pan canvas
- `Scroll` - Zoom in/out

## 🎨 Canvas Controls

- **Drag nodes** from palette to canvas
- **Click output → input** to connect nodes
- **Click nodes** to edit properties
- **Right-click** for context menu
- **Middle-click** or **Shift+Click** to pan
- **Scroll wheel** to zoom

## 📁 Where Things Are Saved

- **Pipelines**: `pipelines/*.json`
- **Screenshots**: `screenshots/*.png`
- **Output files**: Location specified in File Output nodes
- **Logs**: Visible in execution modal

## ⚙️ Configuration

### API Keys (Required for LLM nodes)

Add to your `.env` file:
```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

### Tool Paths

Tools automatically detect installed programs:
- Unity (via Unity Hub)
- Cursor IDE
- Other programs via PATH

## 🔧 Troubleshooting

### "Pipeline won't execute"
✅ Check all nodes are connected properly
✅ Configure all required fields
✅ API keys are set (for LLM nodes)

### "Can't connect nodes"
✅ Connect output (right) to input (left)
✅ Data types must be compatible

### "LLM error"
✅ Check API key in `.env`
✅ Verify API key has credits
✅ Check internet connection

## 📚 Learn More

- **Full Guide**: [Node Editor Guide](NODE_EDITOR_GUIDE.md)
- **Example Pipelines**: `pipelines/example_*.json`
- **Tool Documentation**: [Tools API](../development/TOOLS_API.md)

## 🎯 Next Steps

1. ✅ Load and run the example pipelines
2. ✅ Modify an example to understand how it works
3. ✅ Build a simple pipeline from scratch
4. ✅ Try the AI Agent group node
5. ✅ Create your own automation workflows

## 💬 Example Use Cases

### Game Development
- Automate Unity builds
- Screenshot game states
- AI-powered QA testing

### Development Workflow
- Launch IDE with correct project
- Run automated tests
- Generate reports

### AI Assistance
- Multi-step AI reasoning
- Tool-augmented AI agents
- Automated content generation

### System Automation
- Scheduled tasks
- File monitoring
- Webhook integrations

---

**Ready to build? Launch with `python start_node_editor.py`! 🚀**

