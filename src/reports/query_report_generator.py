"""
Query Report Generator for Cuttle
Generates HTML reports showing agent execution flow, LLM usage, and tool calls.
"""

import copy
import html
import json
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import uuid

class QueryReportGenerator:
    """Generates HTML reports for Cuttle query execution"""
    
    def __init__(self, output_dir: str = "web/logs"):
        # Handle both absolute and relative paths
        output_path = Path(output_dir)
        if not output_path.is_absolute():
            # Relative paths are resolved from the project root (src/)
            project_root = Path(__file__).parent.parent
            output_path = project_root / output_dir
        self.output_dir = output_path
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Serialize query sessions so adhoc runs are not interrupted by another start_query (stale tracker / stuck live report).
        self._session_lock = threading.RLock()
        # Per-query copies of stages/llm/tool lists so /api/query-live-status still works if another
        # session steals the global tracker between finish_query releasing the lock and unregister running.
        self._snapshots_lock = threading.Lock()
        self._live_snapshots: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._live_snapshots_cap = 64
        self._tracker_session_open = False
        self.query_id = None
        self.start_time = None
        self.execution_data = {
            "query_id": None,
            "timestamp": None,
            "user_input": "",
            "execution_stages": [],
            "llm_calls": [],
            "tool_calls": [],
            "total_execution_time": 0,
            "total_tokens": 0,
            "total_cost": 0.0,
            "success": True,
            "error_message": None,
            "claude_usage": {},
            "graph_structure": {
                "nodes": [],
                "connections": [],
                "execution_order": []
            },
            "execution_mode": None,  # single, multi, multi-lite
            "events": [],
            "harness": {},
            "brain": {},
            "sent": {},
        }
        
        # Model pricing (per 1M tokens) - updated as of 2024
        self.model_pricing = {
            # Google Gemini (AI Studio / API tier estimates; verify on cloud.google.com)
            "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
            "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
            "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
            "gemini-2.0-flash-001": {"input": 0.10, "output": 0.40},
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4-turbo": {"input": 10.00, "output": 30.00},
            "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
            "claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
            "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
            "claude-3-haiku": {"input": 0.25, "output": 1.25},
            "claude-3-5-haiku": {"input": 0.25, "output": 1.25},
            "claude-3-3-5-haiku": {"input": 0.25, "output": 1.25},  # Fix for malformed name
            "haiku": {"input": 0.25, "output": 1.25},
            "sonnet": {"input": 3.00, "output": 15.00},
            "3-5-haiku": {"input": 0.25, "output": 1.25}  # Support stripped name format
        }

    def _publish_live_snapshot(self) -> None:
        """Copy stages/llm/tool lists for this query_id so live polling survives tracker handoff."""
        qid = (self.query_id or "").strip()
        if not qid or not self.execution_data:
            return
        snap = _live_snap_from_execution(self.execution_data)
        with self._snapshots_lock:
            self._live_snapshots[qid] = snap
            self._live_snapshots.move_to_end(qid)
            cap = max(8, int(self._live_snapshots_cap))
            while len(self._live_snapshots) > cap:
                self._live_snapshots.popitem(last=False)
        # Also publish to the process-wide store so other tracker instances can serve live status.
        try:
            _publish_shared_live_snapshot(qid, self.execution_data)
        except Exception:
            pass

    def get_live_snapshot(self, qid: str) -> Optional[Dict[str, Any]]:
        """Return last published snapshot for a query id (or None). Thread-safe."""
        raw = (qid or "").strip()
        if not raw:
            return None
        with self._snapshots_lock:
            hit = self._live_snapshots.get(raw)
            if not hit and len(raw) > 8:
                hit = self._live_snapshots.get(raw[:8])
            if hit:
                return copy.deepcopy(hit)
        return get_shared_live_snapshot(raw)
    
    def start_query(self, user_input: str, user_context: Dict = None) -> str:
        """Start tracking on this instance.

        The instance lock is held for the life of this query so finish/add_* stay
        consistent. Concurrent chats each get their own instance via
        ``start_query_tracking`` — they do not block each other.
        """
        self._session_lock.acquire()
        self._tracker_session_open = True
        try:
            qid = str(uuid.uuid4())[:8]
            return self._init_query_session(qid, user_input, user_context)
        except Exception:
            self._tracker_session_open = False
            self._session_lock.release()
            raise

    def start_query_with_id(self, query_id: str, user_input: str, user_context: Dict = None) -> str:
        """Start tracking with a pre-assigned id (stable URL before background thread runs)."""
        raw = (query_id or "").strip()
        if not raw or len(raw) > 16 or not raw.isalnum():
            raise ValueError(f"Invalid query_id: {query_id!r}")
        qid = raw[:8]
        self._session_lock.acquire()
        self._tracker_session_open = True
        try:
            return self._init_query_session(qid, user_input, user_context)
        except Exception:
            self._tracker_session_open = False
            self._session_lock.release()
            raise

    def _init_query_session(self, query_id: str, user_input: str, user_context: Dict = None) -> str:
        """Populate tracker state; caller must hold lock and set _tracker_session_open True."""
        self.query_id = query_id
        self.start_time = time.time()
        print(f"[QUERY] Starting query {self.query_id} at time {self.start_time:.3f}")

        agent_config = self._get_agent_config()

        uc = user_context or {}
        self.execution_data = {
            "query_id": self.query_id,
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input,
            "user_context": uc,
            "pipeline_name": uc.get("pipeline_name", ""),
            "execution_stages": [],
            "llm_calls": [],
            "tool_calls": [],
            "total_execution_time": 0,
            "total_tokens": 0,
            "total_cost": 0.0,
            "success": True,
            "error_message": None,
            "input_source": None,
            "output_destination": None,
            "agent_config": agent_config,
            "claude_usage": {},
            "graph_structure": {
                "nodes": [],
                "connections": [],
                "execution_order": []
            },
            "execution_mode": agent_config.get("agent_stage_mode", "multi-lite"),
            "events": [],
            "harness": {},
            "brain": {},
            "sent": {},
        }

        try:
            self._add_input_stage(user_context)
        except Exception as e:
            print(f"[QUERY] Error adding input stage: {e}")

        try:
            self._create_placeholder_report()
        except Exception as e:
            print(f"[QUERY] Error creating placeholder report: {e}")

        return self.query_id
    
    def _create_placeholder_report(self) -> str:
        """Create a live-updating in-progress report (polls /api/query-live-status)."""
        if not self.query_id or not self.execution_data:
            return None
        filename = f"query_report_{self.query_id}.html"
        filepath = self.output_dir / filename
        user_input = html.escape((self.execution_data.get("user_input") or "")[:500])
        qid_json = json.dumps(self.query_id)
        qid_esc = html.escape(self.query_id)
        doc = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Query """ + qid_esc + """ — Live</title>
    <link rel="stylesheet" href="/css/shared_navigation.css">
    <style>
        :root { --flow1:#7c3aed; --flow2:#06b6d4; --bg:#0f0f12; --card:#1a1a22; }
        body.dark-mode { background:var(--bg); color:#e4e4e7; font-family:system-ui,sans-serif; margin:0; min-height:100vh; }
        .wrap { max-width:720px; margin:0 auto; padding:32px 20px; }
        .hero { display:flex; align-items:center; gap:16px; margin-bottom:28px; }
        .pulse { width:48px; height:48px; border-radius:50%;
          background:linear-gradient(135deg,var(--flow1),var(--flow2));
          animation:flow 2.2s ease-in-out infinite;
          box-shadow:0 0 32px rgba(124,58,237,0.35); }
        @keyframes flow { 0%,100%{ transform:scale(1); opacity:.85;} 50%{ transform:scale(1.08); opacity:1;} }
        h1 { font-size:1.35rem; font-weight:600; margin:0;
          background:linear-gradient(90deg,var(--flow1),var(--flow2));
          -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; }
        .sub { color:#71717a; font-size:.9rem; margin-top:6px; }
        .input-box { background:var(--card); border:1px solid #27272a; border-radius:12px; padding:14px; font-size:.88rem; color:#a1a1aa; margin-bottom:20px; }
        #stages { display:flex; flex-direction:column; gap:8px; }
        .row { background:var(--card); border-left:3px solid var(--flow1); border-radius:8px; padding:10px 12px; font-size:.85rem; animation:slide .45s ease-out; }
        @keyframes slide { from{ opacity:0; transform:translateX(-10px);} to{ opacity:1; transform:none;} }
        .row.tool { border-left-color:#fbbf24; }
        .row.llm { border-left-color:#c084fc; }
        .meta { font-size:.75rem; color:#71717a; margin-top:4px; }
        .err { color:#f87171; }
    </style>
</head>
<body class="dark-mode">
<div class="wrap">
  <div class="hero">
    <div class="pulse" aria-hidden="true"></div>
    <div>
      <h1>Query running</h1>
      <p class="sub">Live status · Query ID <code>""" + qid_esc + """</code></p>
    </div>
  </div>
  <div class="input-box"><strong>Input</strong><br>""" + user_input + """</div>
  <div id="stages"></div>
  <p class="sub" style="margin-top:24px;">Updates every 2s. When the run finishes, this page reloads to the full report.</p>
</div>
<script>
const QID = """ + qid_json + """;
var __cuttleLiveReloads = 0;
async function tick() {
  try {
    const r = await fetch('/api/query-live-status?query_id=' + encodeURIComponent(QID), { cache: 'no-store' });
    const j = await r.json();
    const el = document.getElementById('stages');
    if (!el) return;
    let h = '';
    (j.stages||[]).forEach(function(s) {
      var t = (s.type||'stage').toLowerCase();
      var cls = t.indexOf('tool')>=0 ? 'row tool' : (t.indexOf('llm')>=0 ? 'row llm' : 'row');
      var ok = s.success===false ? '<span class="err">✗</span> ' : '✓ ';
      h += '<div class="'+cls+'">'+ok+escapeHtml(s.name||'')+'<div class="meta">'+
        (s.duration!=null ? Number(s.duration).toFixed(2)+'s · ' : '')+escapeHtml(s.details||'')+'</div></div>';
    });
    (j.llm_calls||[]).forEach(function(c) {
      var cls = 'row llm';
      var ok = c.success===false ? '<span class="err">✗</span> ' : '✓ ';
      h += '<div class="'+cls+'">'+ok+'🤖 '+escapeHtml(c.model||'LLM')+
        '<div class="meta">'+(c.total_tokens||0)+' tokens · '+escapeHtml(c.notes||'')+'</div></div>';
    });
    (j.tool_calls||[]).forEach(function(t) {
      var cls = 'row tool';
      var ok = t.success===false ? '<span class="err">✗</span> ' : '✓ ';
      h += '<div class="'+cls+'">'+ok+'🔧 '+escapeHtml(t.tool_name||'Tool')+
        '<div class="meta">'+escapeHtml(t.input_preview||'')+'</div></div>';
    });
    if (!j.executing) {
      if (!h) el.innerHTML = '<div class="row"><em>Run finished</em><div class="meta">Loading full report…</div></div>';
      else el.innerHTML = h;
      if (__cuttleLiveReloads >= 6) {
        el.innerHTML += '<div class="row err"><strong>Still on live page.</strong> Open <a href="/jobs_page.html">Jobs</a> or ask the daemon console for errors. Try <a href="' +
          location.pathname.split('?')[0] + '?v=' + Date.now() + '">hard refresh</a>.</div>';
        return;
      }
      __cuttleLiveReloads++;
      setTimeout(function(){
        var base = location.pathname.split('?')[0];
        location.replace(base + '?v=' + Date.now());
      }, 700);
      return;
    }
    __cuttleLiveReloads = 0;
    if (!h) h = '<div class="row"><em>Starting…</em><div class="meta">Waiting for pipeline activity</div></div>';
    el.innerHTML = h;
  } catch(e) { console.warn(e); }
  setTimeout(tick, 2000);
}
function escapeHtml(x) { return String(x||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
tick();
</script>
</body>
</html>"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(doc)
        return str(filepath)
    
    def _add_input_stage(self, user_context: Dict = None):
        """Add input stage to show where the prompt came from"""
        # Check if execution_data exists
        if not self.execution_data:
            return
            
        if not user_context:
            user_context = {}
        
        # Determine input source from context
        input_source = "Unknown"
        source_details = ""
        
        if user_context.get("session_kind") == "adhoc" or user_context.get("routing_key") == "adhoc":
            input_source = "Run Now"
            pipeline_name = user_context.get("pipeline_name", "Pipeline")
            source_details = f"Manual run: {pipeline_name}"
        elif user_context.get("command_type") == "claude":
            input_source = "Discord Command"
            source_details = "/claude command"
        elif user_context.get("channel_id"):
            if user_context.get("channel_type") == "dm":
                input_source = "Discord DM"
                source_details = f"DM with {user_context.get('display_name', 'User')}"
            else:
                input_source = "Discord Channel"
                source_details = f"Channel: {user_context.get('channel_name', 'Unknown')}"
        elif user_context.get("node_editor"):
            input_source = "Node Editor"
            pipeline_name = user_context.get("pipeline_name", "Untitled Pipeline")
            node_count = user_context.get("node_count", 0)
            source_details = f"Pipeline: {pipeline_name} ({node_count} nodes)"
        elif user_context.get("web_ui"):
            input_source = "Web UI"
            source_details = "Web interface"
        elif user_context.get("console"):
            input_source = "Bot Console"
            source_details = "Direct console input"
        else:
            # Try to infer from other context
            if user_context.get("display_name"):
                input_source = "Discord"
                source_details = f"User: {user_context.get('display_name')}"
        
        # Store input source info
        self.execution_data["input_source"] = {
            "source": input_source,
            "details": source_details,
            "context": user_context
        }
        
        # Add as execution stage
        self.add_execution_stage(
            f"📥 Input: {input_source}",
            "input",
            self.start_time,
            self.start_time + 0.001,  # Very short duration
            success=True,
            details={"source": input_source, "details": source_details}
        )
    
    def set_output_destination(self, destination: str, details: str = ""):
        """Set the output destination for the query"""
        # Check if execution_data exists
        if not self.execution_data:
            print("[QUERY] Error: execution_data is None, cannot set output destination")
            return
            
        self.execution_data["output_destination"] = {
            "destination": destination,
            "details": details
        }
    
    def _add_output_stage(self):
        """Add output stage to show where the response is going"""
        # Check if execution_data exists
        if not self.execution_data:
            return
            
        # Determine output destination
        output_dest = self.execution_data.get("output_destination", {})
        if output_dest is None:
            output_dest = {}
        destination = output_dest.get("destination", "Unknown") if output_dest else "Unknown"
        details = output_dest.get("details", "") if output_dest else ""
        
        # If no explicit destination set, infer from input source
        if destination == "Unknown":
            input_source = self.execution_data.get("input_source", {})
            if input_source is None:
                input_source = {}
            input_type = input_source.get("source", "Unknown") if input_source else "Unknown"
            
            if "Discord" in input_type:
                destination = "Discord Response"
                details = f"Reply to {input_type}"
            elif input_type == "Node Editor":
                destination = "Node Editor Pipeline"
                details = "Pipeline execution completed"
            elif input_type == "Web UI":
                destination = "Web UI Response"
                details = "Response in web interface"
            elif input_type == "Bot Console":
                destination = "Console Output"
                details = "Console response"
            else:
                destination = "Response Generated"
                details = "Query processed"
        
        # Add as execution stage
        end_time = time.time()
        self.add_execution_stage(
            f"📤 Output: {destination}",
            "output",
            end_time - 0.001,  # Very short duration
            end_time,
            success=True,
            details={"destination": destination, "details": details}
        )
    
    def calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate the cost for an LLM call based on token usage"""
        if model not in self.model_pricing:
            # Default to GPT-4o-mini pricing if model not found
            model = "gpt-4o-mini"
        
        pricing = self.model_pricing[model]
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        
        return input_cost + output_cost
    
    def add_execution_stage(self, stage_name: str, stage_type: str, 
                          start_time: float, end_time: float, 
                          success: bool = True, details: Dict = None,
                          model: str = None, tokens: Dict = None):
        """Add an execution stage to the report"""
        # Check if execution_data exists
        if not self.execution_data:
            print("[QUERY] Error: execution_data is None, cannot add execution stage")
            return
            
        stage_data = {
            "name": stage_name,
            "type": stage_type,  # "regex", "llm", "tool", "multi_stage"
            "start_time": start_time,
            "end_time": end_time,
            "duration": end_time - start_time,
            "success": success,
            "details": details or {},
            "model": model,  # Model used for this stage (if applicable)
            "tokens": tokens or {}  # Token usage for this stage (if applicable)
        }
        self.execution_data["execution_stages"].append(stage_data)
        self._publish_live_snapshot()
    
    def add_graph_node(self, node_id: str, node_name: str, node_type: str, node_category: str = None):
        """Add a node to the graph structure"""
        if not self.execution_data:
            print("[QUERY] Error: execution_data is None, cannot add graph node")
            return
        
        node_data = {
            "id": node_id,
            "name": node_name,
            "type": node_type,
            "category": node_category,
            "executed": False,
            "success": None,
            "start_time": None,
            "end_time": None,
            "duration": None
        }
        self.execution_data["graph_structure"]["nodes"].append(node_data)
    
    def add_graph_connection(self, from_node_id: str, to_node_id: str, port_name: str = None):
        """Add a connection between nodes in the graph"""
        if not self.execution_data:
            print("[QUERY] Error: execution_data is None, cannot add graph connection")
            return
        
        connection_data = {
            "from": from_node_id,
            "to": to_node_id,
            "port": port_name
        }
        self.execution_data["graph_structure"]["connections"].append(connection_data)
    
    def record_node_execution(
        self,
        node_id: str,
        start_time: float,
        end_time: float,
        success: bool = True,
        node_name: str = None,
        node_type: str = None,
    ):
        """Record when a node was executed. Also appends an execution stage so live status shows pipeline progress."""
        if not self.execution_data:
            print("[QUERY] Error: execution_data is None, cannot record node execution")
            return

        resolved_name = node_name
        resolved_type = node_type or ""

        # Find the node in the graph structure (handle type mismatch between int and str)
        node_found = False
        for node in self.execution_data["graph_structure"]["nodes"]:
            if str(node["id"]) == str(node_id):
                node["executed"] = True
                node["success"] = success
                node["start_time"] = start_time
                node["end_time"] = end_time
                node["duration"] = end_time - start_time
                node_found = True
                if not resolved_name:
                    resolved_name = node.get("name") or f"Node {node_id}"
                if not resolved_type:
                    resolved_type = node.get("type") or ""
                print(f"[QUERY] Marked node {node.get('name', 'Unknown')} (ID: {node['id']}) as executed")
                break

        if not node_found:
            print(f"[QUERY] Warning: Could not find node with ID {node_id} (type: {type(node_id).__name__}) in graph structure")
            if not resolved_name:
                resolved_name = f"Node {node_id}"

        nt = (resolved_type or "").lower()
        if nt.startswith("tool-"):
            stage_type = "tool"
        elif nt.startswith("llm") or nt == "llm":
            stage_type = "llm"
        elif nt.startswith("output-"):
            stage_type = "output"
        elif nt.startswith("trigger-"):
            stage_type = "trigger"
        else:
            stage_type = "pipeline"

        detail_line = resolved_type or "node"
        try:
            self.add_execution_stage(
                resolved_name or f"Node {node_id}",
                stage_type,
                start_time,
                end_time,
                success=success,
                details={"details": detail_line, "node_id": str(node_id)},
            )
        except Exception as e:
            print(f"[QUERY] Error adding pipeline execution stage: {e}")

        self.execution_data["graph_structure"]["execution_order"].append({
            "node_id": node_id,
            "timestamp": start_time,
            "success": success
        })
    
    def set_graph_structure(self, nodes: List[Dict], connections: List[Dict]):
        """Set the complete graph structure at once (for pipeline executor)"""
        if not self.execution_data:
            print("[QUERY] Error: execution_data is None, cannot set graph structure")
            return
        
        self.execution_data["graph_structure"]["nodes"] = nodes
        self.execution_data["graph_structure"]["connections"] = connections
    
    def add_llm_call(self, model: str, prompt_tokens: int, completion_tokens: int,
                    total_tokens: int, start_time: float, end_time: float,
                    success: bool = True, response_preview: str = "",
                    actual_cost: float = None, node_id: str = None, prompt_preview: str = "",
                    notes: str = None, knowledge_inputs: List[Dict] = None):
        """Add an LLM call to the report

        Args:
            actual_cost: If provided, uses this cost from the API instead of calculating.
                        This should be used when the API returns actual cost (e.g., Claude with cache tokens)
            node_id: Optional node ID for tracking which node made this call
            prompt_preview: Preview of the prompt/input sent to the LLM
            notes: Optional note (e.g. "Ollama queues requests; this call waited for a prior request.")
            knowledge_inputs: Optional list of knowledge files injected into context
                ({name, path?, kind?, content}) — workspace, skills, agent memory, etc.
        """
        # Check if execution_data exists
        if not self.execution_data:
            print("[QUERY] Error: execution_data is None, cannot add LLM call")
            return
        
        # Use actual cost if provided, otherwise calculate
        if actual_cost is not None and actual_cost > 0:
            call_cost = actual_cost
            cost_is_estimated = False
        else:
            # Calculate cost for this LLM call
            call_cost = self.calculate_cost(model, prompt_tokens, completion_tokens)
            cost_is_estimated = True
        
        # Normalize knowledge inputs (cap content so reports stay readable)
        knowledge_list = []
        if knowledge_inputs and isinstance(knowledge_inputs, list):
            for item in knowledge_inputs:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("path") or "knowledge").strip()
                if not name:
                    continue
                content = item.get("content")
                if content is None:
                    content = ""
                content = str(content)
                if len(content) > 80000:
                    content = content[:80000] + "\n\n… *(truncated for query log)*"
                knowledge_list.append({
                    "name": name,
                    "path": str(item.get("path") or ""),
                    "kind": str(item.get("kind") or ""),
                    "content": content,
                })

        llm_data = {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost": call_cost,
            "cost_is_estimated": cost_is_estimated,  # Flag to indicate if cost is estimated or actual
            "start_time": start_time,
            "end_time": end_time,
            "duration": end_time - start_time,
            "success": success,
            "prompt_preview": prompt_preview,  # Full prompt (no truncation)
            "response_preview": response_preview,  # Full response (no truncation)
            "node_id": node_id,  # Store node ID if provided
            "notes": notes,  # e.g. Ollama queue note
            "knowledge_inputs": knowledge_list,
        }
        self.execution_data["llm_calls"].append(llm_data)
        self.execution_data["total_tokens"] += total_tokens
        self.execution_data["total_cost"] += call_cost
        self._publish_live_snapshot()
    
    def add_tool_call(self, tool_name: str, parameters: Dict, 
                     start_time: float, end_time: float,
                     success: bool = True, result_preview: str = "",
                     model: str = None, tokens: Dict = None, cost: float = 0.0):
        """Add a tool call to the report"""
        # Check if execution_data exists
        if not self.execution_data:
            print("[QUERY] Error: execution_data is None, cannot add tool call")
            return
            
        tool_data = {
            "tool_name": tool_name,
            "parameters": parameters,
            "start_time": start_time,
            "end_time": end_time,
            "duration": end_time - start_time,
            "success": success,
            "result_preview": result_preview or "",  # Full result (no truncation)
            "model": model,  # Model used for this tool call (if applicable)
            "tokens": tokens or {},  # Token usage for this tool call (if applicable)
            "cost": cost  # Cost for this tool call (if applicable)
        }

        # Ensure tool_calls is a list
        if "tool_calls" not in self.execution_data or self.execution_data["tool_calls"] is None:
            self.execution_data["tool_calls"] = []

        self.execution_data["tool_calls"].append(tool_data)
        
        # If this tool call has model/token information, also add it as an LLM call
        # This is especially important for tools like claude_code that are essentially LLM calls
        if model and tokens and tokens.get("total_tokens", 0) > 0:
            # If cost was provided (e.g., from Claude API including cache costs), use it
            # Otherwise, calculate it based on token usage
            if cost > 0:
                # Use the provided cost directly (includes cache token costs)
                self.execution_data["total_cost"] += cost
                llm_call_cost = cost
            else:
                # Calculate cost from tokens
                llm_call_cost = self.calculate_cost(
                    model,
                    tokens.get("prompt_tokens", 0),
                    tokens.get("completion_tokens", 0)
                )
            
            llm_data = {
                "model": model,
                "prompt_tokens": tokens.get("prompt_tokens", 0),
                "completion_tokens": tokens.get("completion_tokens", 0),
                "total_tokens": tokens.get("total_tokens", 0),
                "cost": llm_call_cost,
                "cost_is_estimated": False if cost > 0 else True,  # False if actual cost was provided
                "start_time": start_time,
                "end_time": end_time,
                "duration": end_time - start_time,
                "success": success,
                "response_preview": result_preview,  # Full result (no truncation)
                "node_id": None  # Tool calls don't have node_id for now
            }
            self.execution_data["llm_calls"].append(llm_data)
            self.execution_data["total_tokens"] += tokens.get("total_tokens", 0)
        self._publish_live_snapshot()

    def add_event(self, kind: str, payload: Dict[str, Any] = None) -> None:
        if not self.execution_data:
            return
        events = self.execution_data.setdefault("events", [])
        if not isinstance(events, list):
            events = []
            self.execution_data["events"] = events
        from api.query_events import MAX_EVENTS, MAX_TEXT, _cap

        body = dict(payload or {})
        for key in ("text", "summary", "error"):
            if key in body:
                body[key] = _cap(body[key], MAX_TEXT if key == "text" else 2000)
        ev = {"kind": str(kind or "event"), "t": time.time(), **body}
        last = events[-1] if events else None
        if (
            isinstance(last, dict)
            and last.get("kind") in ("thinking", "writing", "status")
            and last.get("kind") == ev["kind"]
            and last.get("text") == ev.get("text")
        ):
            last["t"] = ev["t"]
            self._publish_live_snapshot()
            return
        events.append(ev)
        overflow = len(events) - MAX_EVENTS
        if overflow > 0:
            del events[:overflow]
        self._publish_live_snapshot()

    def set_harness(self, harness: Dict[str, Any]) -> None:
        if not self.execution_data:
            return
        self.execution_data["harness"] = dict(harness or {})
        self._publish_live_snapshot()

    def set_brain(self, brain: Dict[str, Any]) -> None:
        if not self.execution_data:
            return
        self.execution_data["brain"] = dict(brain or {})
        self._publish_live_snapshot()

    def set_sent(self, prompt: str, *, resume: bool = False) -> None:
        if not self.execution_data:
            return
        from api.query_events import MAX_SENT, _cap

        text = prompt or ""
        self.execution_data["sent"] = {
            "chars": len(text),
            "resume": bool(resume),
            "text": _cap(text, MAX_SENT),
        }
        self.add_event(
            "sent",
            {
                "chars": len(text),
                "resume": bool(resume),
                "preview": _cap(text, 1200),
            },
        )

    def finish_query(self, success: bool = True, error_message: str = None):
        """Finish tracking the query and generate the report"""
        if not self._tracker_session_open:
            print("[QUERY FINISH_QUERY] Skipped: no active session (already finished or duplicate finish)")
            return None, None

        report_path = None
        json_path = None
        try:
            print(f"[QUERY FINISH_QUERY] Starting finish_query - success: {success}, error: {error_message}")

            if not self.execution_data:
                print("[QUERY FINISH_QUERY] Error: execution_data is None, cannot finish query")
                return None, None

            print(f"[QUERY FINISH_QUERY] execution_data exists, query_id: {self.query_id}")

            # Calculate execution time with debug logging
            current_time = time.time()
            if self.start_time:
                execution_time = current_time - self.start_time
                self.execution_data["total_execution_time"] = execution_time
                print(f"[QUERY FINISH_QUERY] Total execution time: {execution_time:.2f}s (start: {self.start_time:.3f}, end: {current_time:.3f})")
            else:
                print(f"[QUERY FINISH_QUERY] Warning: start_time is None! Cannot calculate execution time.")
                self.execution_data["total_execution_time"] = 0

            self.execution_data["success"] = success
            self.execution_data["error_message"] = error_message
            try:
                self.add_event("finish", {"success": success, "error": error_message or ""})
            except Exception:
                pass

            print(f"[QUERY FINISH_QUERY] Adding output stage...")
            try:
                self._add_output_stage()
                print(f"[QUERY FINISH_QUERY] Output stage added successfully")
            except Exception as e:
                print(f"[QUERY FINISH_QUERY] Error adding output stage: {e}")
                import traceback
                traceback.print_exc()

            print(f"[QUERY FINISH_QUERY] Generating HTML report...")
            report_path = None
            report_gen_error = None
            try:
                report_path = self._generate_html_report()
                print(f"[QUERY FINISH_QUERY] HTML report generated: {report_path}")
            except Exception as e:
                report_gen_error = str(e)
                print(f"[QUERY FINISH_QUERY] Error generating HTML report: {e}")
                import traceback
                traceback.print_exc()
            if not report_path:
                fb = report_gen_error or (
                    (self.execution_data.get("error_message") or "").strip()
                    if self.execution_data
                    else ""
                ) or "Full HTML report was not generated"
                report_path = self._write_fallback_query_report_html(fb)
            if report_path and self.execution_data:
                self.execution_data["report_filename"] = os.path.basename(report_path)

            print(f"[QUERY FINISH_QUERY] Saving execution data JSON...")
            try:
                json_path = self._save_execution_data()
                print(f"[QUERY FINISH_QUERY] Execution data saved: {json_path}")
            except Exception as e:
                print(f"[QUERY FINISH_QUERY] Error saving execution data: {e}")
                import traceback
                traceback.print_exc()
                json_path = None

            print(f"[QUERY FINISH_QUERY] Returning: report_path={report_path}, json_path={json_path}")
            return report_path, json_path
        finally:
            try:
                self._publish_live_snapshot()
            except Exception as _pe:
                print(f"[QUERY] live snapshot publish in finish_query: {_pe}")
            try:
                self.ensure_stable_report_not_placeholder()
            except Exception as _se:
                print(f"[QUERY] ensure stable report in finish_query finally: {_se}")
            self._tracker_session_open = False
            try:
                self._session_lock.release()
            except RuntimeError:
                pass

    def _write_fallback_query_report_html(self, reason: str) -> Optional[str]:
        """Write a minimal report to the stable path so the live placeholder is not left on disk."""
        if not self.query_id:
            return None
        qid_esc = html.escape(str(self.query_id))
        reason_esc = html.escape((reason or "")[:1200])
        err_msg = ""
        stages_lines: List[str] = []
        tool_lines: List[str] = []
        user_in = ""
        if self.execution_data:
            err_msg = html.escape(str(self.execution_data.get("error_message") or "")[:4000])
            user_in = html.escape(str(self.execution_data.get("user_input") or "")[:800])
            for s in (self.execution_data.get("execution_stages") or [])[-40:]:
                nm = s.get("name") or s.get("type") or "stage"
                ok = "✓ " if s.get("success") is not False else "✗ "
                stages_lines.append(f"<li>{html.escape(ok + str(nm))}</li>")
            for t in (self.execution_data.get("tool_calls") or [])[-20:]:
                tn = t.get("tool_name") or t.get("name") or "tool"
                tool_lines.append(f"<li>{html.escape(str(tn))}</li>")
        stages_html = "".join(stages_lines) if stages_lines else "<li>(none recorded)</li>"
        tools_html = "".join(tool_lines) if tool_lines else "<li>(none recorded)</li>"
        doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Query {qid_esc} — summary</title>
<link rel="stylesheet" href="/css/shared_navigation.css">
<style>
body.dark-mode {{ background:#0f0f12; color:#e4e4e7; font-family:system-ui,sans-serif; margin:0; padding:24px; max-width:800px; margin-left:auto; margin-right:auto; }}
h1 {{ font-size:1.2rem; }}
.box {{ background:#1a1a22; border:1px solid #27272a; border-radius:10px; padding:14px; margin:14px 0; font-size:.88rem; }}
.note {{ color:#a1a1aa; white-space:pre-wrap; }}
.err {{ color:#f87171; white-space:pre-wrap; }}
ul {{ margin:8px 0; padding-left:20px; }}
</style>
</head>
<body class="dark-mode">
<p><a href="/">Home</a> · <a href="/jobs_page.html">Jobs</a></p>
<h1>Query report (summary)</h1>
<p style="color:#71717a;font-size:.9rem">ID <code>{qid_esc}</code> — full HTML could not be built; pipeline details below.</p>
<div class="box"><strong>Generation note</strong><p class="note">{reason_esc}</p></div>
<div class="box"><strong>Input</strong><p class="note">{user_in or "(empty)"}</p></div>
<div class="box"><strong>Run error (if any)</strong><p class="err">{err_msg or "(none)"}</p></div>
<div class="box"><strong>Stages</strong><ul>{stages_html}</ul></div>
<div class="box"><strong>Tool calls</strong><ul>{tools_html}</ul></div>
</body>
</html>"""
        try:
            stable_path = self.output_dir / f"query_report_{self.query_id}.html"
            with open(stable_path, "w", encoding="utf-8") as f:
                f.write(doc)
            print(f"[QUERY] Wrote fallback summary report: {stable_path}")
            return str(stable_path)
        except Exception as werr:
            print(f"[QUERY] Fallback report write failed: {werr}")
            return None

    def ensure_stable_report_not_placeholder(self) -> None:
        """If the stable query_report_<id>.html is missing or still the live polling page, write fallback summary."""
        if not self.query_id:
            return
        stable_path = self.output_dir / f"query_report_{self.query_id}.html"
        try:
            is_placeholder = True
            if stable_path.is_file():
                head = stable_path.read_text(encoding="utf-8", errors="replace")[:16000]
                is_placeholder = "async function tick()" in head
            if is_placeholder:
                if self.execution_data:
                    msg = (self.execution_data.get("error_message") or "").strip() or "Report did not finalize to disk as expected"
                    self._write_fallback_query_report_html(msg[:1200])
                else:
                    self._write_fallback_query_report_html("No execution data — could not build summary")
        except Exception as e:
            print(f"[QUERY] ensure_stable_report_not_placeholder: {e}")
    
    def _generate_html_report(self) -> str:
        """Generate the HTML report file"""
        print(f"[QUERY _generate_html_report] Starting HTML report generation...")
        
        # Check if execution_data exists
        if not self.execution_data:
            print("[QUERY _generate_html_report] Error: execution_data is None, cannot generate HTML report")
            return None
        
        print(f"[QUERY _generate_html_report] execution_data exists, creating filename...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"query_report_{self.query_id}_{timestamp}.html"
        filepath = self.output_dir / filename
        
        print(f"[QUERY _generate_html_report] Filepath: {filepath}")
        print(f"[QUERY _generate_html_report] Creating HTML content...")
        
        try:
            html_content = self._create_html_content()
            print(f"[QUERY _generate_html_report] HTML content created, length: {len(html_content)} chars")
        except Exception as e:
            print(f"[QUERY _generate_html_report] ERROR creating HTML content: {e}")
            import traceback
            traceback.print_exc()
            raise
        
        print(f"[QUERY _generate_html_report] Writing to file...")
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f"[QUERY _generate_html_report] File written successfully")
            # Also write to stable path so the in-progress link shows the final report
            stable_path = self.output_dir / f"query_report_{self.query_id}.html"
            with open(stable_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f"[QUERY _generate_html_report] Stable path written: {stable_path}")
        except Exception as e:
            print(f"[QUERY _generate_html_report] ERROR writing file: {e}")
            import traceback
            traceback.print_exc()
            raise
        
        print(f"[QUERY _generate_html_report] Returning filepath: {str(filepath)}")
        return str(filepath)
    
    def _save_execution_data(self) -> str:
        """Save execution data as JSON for debugging"""
        # Check if execution_data exists
        if not self.execution_data:
            print("[QUERY] Error: execution_data is None, cannot save execution data")
            return None
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"query_data_{self.query_id}_{timestamp}.json"
        filepath = self.output_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.execution_data, f, indent=2)

        try:
            stable = self.output_dir / f"query_data_{self.query_id}.json"
            with open(stable, 'w', encoding='utf-8') as f:
                json.dump(self.execution_data, f, indent=2)
        except Exception:
            pass
        
        return str(filepath)
    
    def _get_agent_config(self) -> Dict[str, Any]:
        """Get current agent configuration"""
        try:
            from core.config import get_config
            config = get_config()
            return {
                "agent_stage_mode": config.get_agent_stage_mode(),
                "preferred_llm_model": config.get_preferred_llm_model(),
                "llm_fallback_enabled": config.is_llm_fallback_enabled(),
                "mode": config.get_mode(),
                "thinking_response": config.should_show_thinking(),
                "debug_mode": config.is_debug_mode(),
                "cursor_agent_method": config.get_cursor_agent_method()
            }
        except Exception as e:
            print(f"[QUERY] Error getting agent config: {e}")
            return {
                "agent_stage_mode": "unknown",
                "preferred_llm_model": "unknown",
                "llm_fallback_enabled": False,
                "mode": "unknown",
                "thinking_response": True,
                "debug_mode": False,
                "cursor_agent_method": "unknown"
            }
    
    def _create_html_content(self) -> str:
        """Create the HTML content for the report"""
        print(f"[QUERY _create_html_content] Starting HTML content creation...")
        
        # Check if execution_data exists
        if not self.execution_data:
            print("[QUERY _create_html_content] Error: execution_data is None, cannot create HTML content")
            return "<html><body><h1>Error: No execution data available</h1></body></html>"
        
        print(f"[QUERY _create_html_content] Calculating status...")
        status_class = "success" if self.execution_data["success"] else "error"
        status_pill_class = "ok" if self.execution_data["success"] else "fail"
        status_pill_text = "Success" if self.execution_data["success"] else "Failed"
        
        # Calculate summary stats
        print(f"[QUERY _create_html_content] Calculating summary stats...")
        total_stages = len(self.execution_data.get("execution_stages", []))
        total_llm_calls = len(self.execution_data.get("llm_calls", []))
        total_tool_calls = len(self.execution_data.get("tool_calls", []))
        total_tokens = self.execution_data.get("total_tokens", 0)
        execution_time = self.execution_data.get("total_execution_time", 0)
        
        print(f"[QUERY _create_html_content] Stats: stages={total_stages}, llm_calls={total_llm_calls}, tool_calls={total_tool_calls}, tokens={total_tokens}, time={execution_time:.2f}s")
        
        # Create agent graph HTML
        print(f"[QUERY _create_html_content] Creating agent graph HTML...")
        try:
            agent_graph_html = self._create_agent_graph_html()
            print(f"[QUERY _create_html_content] Agent graph HTML created, length: {len(agent_graph_html)}")
        except Exception as e:
            print(f"[QUERY _create_html_content] ERROR creating agent graph: {e}")
            import traceback
            traceback.print_exc()
            agent_graph_html = "<div>Error creating agent graph</div>"
        
        # Create stages HTML
        print(f"[QUERY _create_html_content] Creating stages HTML...")
        try:
            stages_html = self._create_stages_html()
            print(f"[QUERY _create_html_content] Stages HTML created")
        except Exception as e:
            print(f"[QUERY _create_html_content] ERROR creating stages: {e}")
            import traceback
            traceback.print_exc()
            stages_html = "<div>Error creating stages</div>"
        
        # Create LLM calls HTML
        print(f"[QUERY _create_html_content] Creating LLM calls HTML...")
        try:
            llm_calls_html = self._create_llm_calls_html()
            print(f"[QUERY _create_html_content] LLM calls HTML created")
        except Exception as e:
            print(f"[QUERY _create_html_content] ERROR creating LLM calls: {e}")
            import traceback
            traceback.print_exc()
            llm_calls_html = "<div>Error creating LLM calls</div>"
        
        # Create tool calls HTML
        print(f"[QUERY _create_html_content] Creating tool calls HTML...")
        try:
            tool_calls_html = self._create_tool_calls_html()
            print(f"[QUERY _create_html_content] Tool calls HTML created")
        except Exception as e:
            print(f"[QUERY _create_html_content] ERROR creating tool calls: {e}")
            import traceback
            traceback.print_exc()
            tool_calls_html = "<div>Error creating tool calls</div>"
        
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cuttle Query Report - {self.query_id}</title>
    <link rel="stylesheet" href="/css/shared_navigation.css">
    <style>
        /* Query report specific styles */
        .report-container {{
            max-width: 1180px;
            margin: 0 auto;
            padding: 10px 12px 16px;
        }}
        
        .page-header {{
            display: flex;
            flex-wrap: wrap;
            align-items: flex-start;
            justify-content: space-between;
            gap: 10px 16px;
            text-align: left;
            padding: 12px 16px;
            background: var(--header-bg);
            color: var(--text-inverse);
            margin-bottom: 12px;
            border-radius: 10px;
        }}
        
        .page-header-main {{
            min-width: 0;
            flex: 1;
        }}
        
        .page-header h1 {{
            font-size: 1.15rem;
            font-weight: 600;
            margin: 0 0 4px 0;
            letter-spacing: -0.02em;
            /* Default (light mode) - dark gradient */
            background: linear-gradient(135deg, #1a202c 0%, #2d3748 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .dark-mode .page-header h1,
        .midnight-mode .page-header h1 {{
            /* Dark/Midnight mode - light gradient */
            background: linear-gradient(135deg, #ffffff 0%, #e2e8f0 100%);
        }}
        
        .page-header h1 .emoji {{
            -webkit-text-fill-color: initial;
            background: none;
            -webkit-background-clip: initial;
            background-clip: initial;
        }}
        
        .page-header .subtitle {{
            font-size: 0.78rem;
            opacity: 0.88;
            font-weight: 400;
            line-height: 1.35;
        }}
        
        .page-header-meta {{
            font-size: 0.72rem;
            opacity: 0.9;
            text-align: right;
            max-width: 280px;
        }}
        
        .page-header-meta a {{
            color: inherit;
            text-decoration: underline;
            text-underline-offset: 2px;
        }}
        
        /* Theme tokens (dark / midnight / light) come from shared_navigation.css.
           Only add report-local extras here so midnight stays consistent with Cuttle. */
        :root {{
            --bg-hover: rgba(0, 0, 0, 0.06);
        }}
        .dark-mode {{
            --bg-hover: rgba(255, 255, 255, 0.06);
        }}
        .midnight-mode {{
            --bg-hover: rgba(255, 255, 255, 0.08);
        }}
        .light-mode {{
            --bg-hover: rgba(0, 0, 0, 0.06);
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 12px;
            transition: background 0.3s ease, color 0.3s ease;
        }}
        
        .page-header .query-id-code {{
            font-size: 0.85em;
            padding: 2px 7px;
            border-radius: 4px;
            background: rgba(255, 255, 255, 0.18);
            font-family: ui-monospace, Consolas, monospace;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: var(--bg-secondary);
            border-radius: 12px;
            box-shadow: 0 20px 40px var(--card-shadow);
            overflow: hidden;
            transition: background 0.3s ease, box-shadow 0.3s ease;
        }}
        
        .header {{
            background: var(--header-bg);
            color: var(--text-inverse);
            padding: 10px 16px;
            text-align: center;
            position: relative;
            transition: background 0.3s ease;
        }}
        
        .header .header-logo {{
            max-height: 32px;
            width: auto;
            vertical-align: middle;
        }}
        
        .header h1 {{
            font-size: 1.25rem;
            margin-bottom: 0;
            font-weight: 500;
        }}
        
        .header-controls {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 15px;
        }}
        
        .timestamp {{
            opacity: 0.8;
            font-size: 1.1em;
        }}
        
        .theme-toggle {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: var(--text-inverse);
            padding: 8px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 1.2em;
            transition: all 0.3s ease;
            backdrop-filter: blur(10px);
        }}
        
        .theme-toggle:hover {{
            background: rgba(255, 255, 255, 0.2);
            border-color: rgba(255, 255, 255, 0.3);
            transform: scale(1.05);
        }}
        
        .theme-icon {{
            display: inline-block;
            transition: transform 0.3s ease;
        }}
        
        .light-mode .theme-icon {{
            transform: rotate(180deg);
        }}
        
        .summary-card {{
            margin: 0 0 8px 0;
            padding: 8px 10px;
            border-radius: 8px;
            border: 1px solid;
            transition: background 0.3s ease, border-color 0.3s ease;
        }}
        
        .summary-card.success {{
            background: var(--success-bg);
            border-color: var(--success-border);
        }}
        
        .summary-card.error {{
            background: var(--error-bg);
            border-color: var(--error-border);
        }}
        
        .summary-header {{
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px 12px;
            margin-bottom: 8px;
        }}
        
        .summary-header .summary-title {{
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-secondary);
            margin: 0;
        }}
        
        .status-icon {{
            font-size: 1.1em;
            line-height: 1;
        }}
        
        .status-pill {{
            display: inline-flex;
            align-items: center;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 0.65rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}
        
        .status-pill.ok {{
            background: rgba(72, 187, 120, 0.25);
            color: var(--success-border);
            border: 1px solid var(--success-border);
        }}
        
        .status-pill.fail {{
            background: rgba(245, 101, 101, 0.2);
            color: var(--error-border);
            border: 1px solid var(--error-border);
        }}
        
        .summary-chips {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 6px;
        }}
        
        .summary-chip {{
            display: inline-flex;
            align-items: baseline;
            gap: 5px;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 500;
            color: var(--text-primary);
            background: var(--stat-bg);
            border: 1px solid var(--border-color);
            box-shadow: 0 1px 2px var(--card-shadow);
        }}

        a.summary-chip {{
            color: inherit;
            text-decoration: none;
        }}

        a.summary-chip:hover {{
            border-color: var(--accent-color);
        }}
        
        .summary-chip .chip-value {{
            font-weight: 700;
            font-variant-numeric: tabular-nums;
            font-size: 0.8rem;
        }}
        
        .summary-chip .chip-label {{
            color: var(--text-secondary);
            font-weight: 500;
            font-size: 0.72rem;
        }}
        
        /* Legacy stat grid (unused in new layout; kept for any older embedded fragments) */
        .summary-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 10px;
        }}
        
        .stat {{
            text-align: center;
            padding: 12px;
            background: var(--stat-bg);
            border-radius: 8px;
            box-shadow: 0 1px 6px var(--card-shadow);
        }}
        
        .stat-number {{
            display: block;
            font-size: 1.4em;
            font-weight: bold;
            margin-bottom: 4px;
        }}
        
        .stat-number.success {{
            color: var(--success-border);
        }}
        
        .stat-number.error {{
            color: var(--error-border);
        }}
        
        .stat-label {{
            color: var(--text-secondary);
            font-size: 0.75em;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}
        
        .section {{
            margin: 7px 0;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
            transition: background 0.3s ease;
        }}
        
        .section-header {{
            background: transparent;
            color: var(--text-primary);
            padding: 8px 10px;
            font-size: 0.84rem;
            font-weight: 600;
            transition: background 0.3s ease;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            user-select: none;
        }}
        
        .section-header:hover {{
            background: var(--bg-hover);
        }}
        
        .section-toggle {{
            font-size: 1.2em;
            transition: transform 0.3s ease;
            margin-left: 10px;
        }}
        
        .section.collapsed .section-toggle {{
            transform: rotate(-90deg);
        }}
        
        .section-content {{
            padding: 8px 10px 10px;
            border-top: 1px solid var(--border-color);
            transition: max-height 0.3s ease, opacity 0.3s ease;
            overflow: hidden;
        }}
        
        .section.collapsed .section-content {{
            max-height: 0;
            padding: 0 14px;
            opacity: 0;
        }}
        
        .agent-graph {{
            background: var(--stat-bg);
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
            box-shadow: 0 2px 10px var(--card-shadow);
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        
        .flow-node {{
            display: block;
            background: var(--success-border);
            color: var(--text-inverse);
            padding: 15px 20px;
            border-radius: 12px;
            margin: 5px 0;
            font-size: 0.9em;
            position: relative;
            text-align: center;
            min-width: 300px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
        }}
        
        .flow-node.llm {{
            background: #3498db;
            color: var(--text-inverse);
        }}
        
        .flow-node.tool {{
            background: #e67e22;
            color: var(--text-inverse);
        }}
        
        .flow-node.tool.error {{
            background: #e74c3c;
            color: var(--text-inverse);
        }}
        
        .flow-node.regex {{
            background: #9b59b6;
            color: var(--text-inverse);
        }}
        
        .flow-node.input {{
            background: #27ae60;
            color: var(--text-inverse);
        }}
        
        .flow-node.output {{
            background: #8e44ad;
            color: var(--text-inverse);
        }}
        
        .flow-arrow {{
            display: block;
            margin: 5px 0;
            color: var(--text-secondary);
            font-size: 1.5em;
            text-align: center;
        }}
        
        .stage-item {{
            background: var(--stat-bg);
            border: 1px solid var(--border-color);
            border-radius: 7px;
            padding: 9px 10px;
            margin: 7px 0;
            transition: background 0.3s ease, box-shadow 0.3s ease;
        }}
        
        .stage-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }}
        
        .stage-name {{
            font-weight: bold;
            font-size: 1.1em;
        }}
        
        .call-number-badge {{
            display: inline-block;
            padding: 4px 10px;
            background: linear-gradient(135deg, #8B5CF6 0%, #7C3AED 100%);
            color: var(--text-inverse);
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 600;
            margin-right: 8px;
            box-shadow: 0 2px 4px rgba(139, 92, 246, 0.3);
        }}
        
        .response-preview {{
            background: var(--bg-tertiary);
            padding: 10px;
            border-radius: 4px;
            margin-top: 8px;
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
            max-height: 150px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-word;
        }}

        .knowledge-section details.knowledge-root > summary {{
            cursor: pointer;
            list-style: none;
            user-select: none;
        }}
        .knowledge-section details.knowledge-root > summary::-webkit-details-marker {{
            display: none;
        }}
        .knowledge-section details.knowledge-root > summary::before {{
            content: '▸ ';
            color: var(--text-secondary);
        }}
        .knowledge-section details.knowledge-root[open] > summary::before {{
            content: '▾ ';
        }}
        .knowledge-files {{
            margin-top: 8px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .knowledge-file {{
            border: 1px solid var(--border-color, rgba(255,255,255,0.12));
            border-radius: 6px;
            background: rgba(245, 158, 11, 0.06);
            border-left: 3px solid rgba(245, 158, 11, 0.65);
        }}
        .knowledge-file-summary {{
            cursor: pointer;
            padding: 8px 10px;
            font-family: 'Courier New', monospace;
            font-size: 0.88em;
            color: var(--text-primary);
            list-style: none;
            user-select: none;
        }}
        .knowledge-file-summary::-webkit-details-marker {{
            display: none;
        }}
        .knowledge-file-summary::before {{
            content: '▸ ';
            color: var(--text-secondary);
        }}
        .knowledge-file[open] > .knowledge-file-summary::before {{
            content: '▾ ';
        }}
        .knowledge-kind {{
            display: inline-block;
            margin-left: 8px;
            padding: 1px 7px;
            border-radius: 999px;
            font-size: 0.72em;
            font-family: system-ui, sans-serif;
            background: rgba(245, 158, 11, 0.2);
            color: var(--text-secondary);
            vertical-align: middle;
        }}
        .knowledge-file-content {{
            margin: 0 8px 8px;
            max-height: 320px;
            background-color: rgba(245, 158, 11, 0.05);
            border-left: 3px solid rgba(245, 158, 11, 0.45);
        }}
        
        .stage-duration {{
            color: var(--text-secondary);
            font-size: 0.9em;
        }}
        
        .stage-details {{
            color: var(--text-secondary);
            font-size: 0.9em;
        }}
        
        .stage-model-info {{
            background: var(--bg-tertiary);
            border-radius: 6px;
            padding: 8px 12px;
            margin: 8px 0;
            border-left: 3px solid var(--accent-color);
            font-size: 0.9em;
        }}
        
        .stage-model-info .model-name {{
            font-weight: 600;
            color: var(--accent-color);
            margin-right: 10px;
        }}
        
        .stage-model-info .token-count {{
            color: var(--text-primary);
            font-family: monospace;
            background: var(--bg-secondary);
            padding: 2px 6px;
            border-radius: 4px;
            margin: 0 5px;
        }}
        
        .stage-model-info .token-breakdown {{
            color: var(--text-secondary);
            font-size: 0.85em;
            margin-top: 4px;
        }}
        
        .user-input {{
            background: var(--stat-bg);
            border-radius: 6px;
            padding: 9px 10px;
            margin: 0;
            font-family: monospace;
            border-left: 3px solid var(--success-border);
            white-space: pre-wrap;
            word-break: break-word;
        }}
        
        /* Cost breakdown styles */
        .cost-summary {{
            text-align: left;
            margin-bottom: 12px;
            padding: 10px 12px;
            background: var(--bg-tertiary);
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }}
        
        .cost-summary h3 {{
            color: var(--accent-color);
            margin-bottom: 6px;
            font-size: 0.95rem;
            font-weight: 600;
        }}
        
        .cost-note {{
            color: var(--text-secondary);
            font-size: 0.78rem;
            font-style: italic;
        }}
        
        .cost-warning {{
            background: var(--warning-bg);
            border: 2px solid var(--warning-border);
            border-radius: 8px;
            padding: 15px;
            margin: 20px 0;
        }}
        
        .cost-warning p {{
            margin: 10px 0;
        }}
        
        .cost-warning ul {{
            margin: 10px 0;
            padding-left: 30px;
        }}
        
        .cost-warning li {{
            margin: 5px 0;
        }}
        
        .model-breakdown {{
            display: grid;
            gap: 10px;
        }}
        
        .model-cost-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 10px 12px;
            transition: all 0.3s ease;
        }}
        
        .model-cost-card:hover {{
            transform: translateY(-1px);
            box-shadow: 0 2px 8px var(--card-shadow);
        }}
        
        .model-cost-card h4 {{
            margin: 0 0 8px 0;
            color: var(--text-primary);
            font-size: 0.9rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 6px;
        }}
        
        .cost-details {{
            display: grid;
            gap: 8px;
        }}
        
        .cost-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 5px 0;
        }}
        
        .cost-label {{
            color: var(--text-secondary);
            font-weight: 500;
        }}
        
        .cost-value {{
            color: var(--text-primary);
            font-weight: 600;
            font-family: monospace;
        }}
        
        .footer {{
            background: var(--footer-bg);
            color: var(--text-inverse);
            padding: 12px 14px;
            text-align: center;
            font-size: 0.75rem;
            transition: background 0.3s ease;
            margin-top: 8px;
            border-radius: 0 0 8px 8px;
        }}
        
        @media (max-width: 768px) {{
            .container {{
                margin: 10px;
                border-radius: 8px;
            }}
            
            .header {{
                padding: 10px 12px;
            }}
            
            .page-header {{
                flex-direction: column;
            }}
            
            .page-header-meta {{
                text-align: left;
                max-width: none;
            }}
            
            .summary-stats {{
                grid-template-columns: repeat(2, 1fr);
            }}
            
            .section {{
                margin: 10px 0;
            }}
        }}
    </style>
    <script>
        (function() {{
            var t = localStorage.getItem('theme') || 'dark';
            var cls = t === 'light' ? 'light-mode' : (t === 'midnight' ? 'midnight-mode' : 'dark-mode');
            document.documentElement.classList.add(cls);
        }})();
    </script>
</head>
<body data-page="query">
    <script>
        (function() {{
            var t = localStorage.getItem('theme') || 'dark';
            document.body.classList.remove('dark-mode', 'midnight-mode', 'light-mode');
            document.body.classList.add(t === 'light' ? 'light-mode' : (t === 'midnight' ? 'midnight-mode' : 'dark-mode'));
        }})();
    </script>
    <div class="report-container">
        <div class="summary-card {status_class}">
            {f'<div class="error-detail" style="margin:0 0 8px 0;padding:8px 10px;font-size:0.8rem;background:var(--error-bg);border-radius:6px;border-left:3px solid var(--error-border);"><strong>Error:</strong> {html.escape(str(self.execution_data.get("error_message", "")))}</div>' if not self.execution_data["success"] and self.execution_data.get("error_message") else ''}
            <div class="summary-chips" aria-label="Query metrics">
                <span class="status-pill {status_pill_class}">{status_pill_text}</span>
                <span class="summary-chip"><span class="chip-value">{html.escape(str(self.query_id))}</span><span class="chip-label">query</span></span>
                {f'<a class="summary-chip" href="/pipeline_chat.html?pipeline={html.escape(self.execution_data.get("pipeline_name", "").replace(" ", "_"))}"><span class="chip-value">{html.escape(self.execution_data.get("pipeline_name", ""))}</span><span class="chip-label">pipeline</span></a>' if self.execution_data.get("pipeline_name") else ''}
                <span class="summary-chip"><span class="chip-value">{total_stages}</span><span class="chip-label">stages</span></span>
                <span class="summary-chip"><span class="chip-value">{total_llm_calls}</span><span class="chip-label">LLM calls</span></span>
                <span class="summary-chip"><span class="chip-value">{total_tool_calls}</span><span class="chip-label">tool calls</span></span>
                <span class="summary-chip"><span class="chip-value">{total_tokens:,}</span><span class="chip-label">tokens</span></span>
                <span class="summary-chip"><span class="chip-value">${self.execution_data["total_cost"]:.4f}</span><span class="chip-label">est. cost</span></span>
                <span class="summary-chip"><span class="chip-value">{execution_time:.2f}s</span><span class="chip-label">runtime</span></span>
            </div>
        </div>
        
        <div class="section compact-section" id="input-section">
            <div class="section-header" onclick="toggleSection('input-section')">
                <span>💬 Prompt</span>
                <span class="section-toggle">▼</span>
            </div>
            <div class="section-content">
                <div class="user-input">
                    {html.escape(str(self.execution_data["user_input"]))}
                </div>
            </div>
        </div>
        
        <div class="section collapsed advanced-section" id="config-section">
            <div class="section-header" onclick="toggleSection('config-section')">
                <span>⚙️ {'Pipeline Configuration' if self.execution_data.get('agent_config', {}).get('execution_mode') == 'Node Editor Pipeline' else 'Agent Configuration'}</span>
                <span class="section-toggle">▼</span>
            </div>
            <div class="section-content">
                {self._create_agent_config_html()}
            </div>
        </div>
        
        {f'''<div class="section collapsed advanced-section" id="cost-section">
            <div class="section-header" onclick="toggleSection('cost-section')">
                <span>💰 Cost Breakdown</span>
                <span class="section-toggle">▼</span>
            </div>
            <div class="section-content">
                {self._create_cost_breakdown_html()}
            </div>
        </div>''' if total_llm_calls else ''}
        
        <div class="section collapsed advanced-section" id="graph-section">
            <div class="section-header" onclick="toggleSection('graph-section')">
                <span>🔄 Agent Execution Graph</span>
                <span class="section-toggle">▼</span>
            </div>
            <div class="section-content">
                {agent_graph_html}
            </div>
        </div>
        
        <div class="section collapsed advanced-section" id="stages-section">
            <div class="section-header" onclick="toggleSection('stages-section')">
                <span>⚡ Execution Stages</span>
                <span class="section-toggle">▼</span>
            </div>
            <div class="section-content">
                {stages_html}
            </div>
        </div>
        
        {f'''<div class="section" id="llm-section">
            <div class="section-header" onclick="toggleSection('llm-section')">
                <span>🧠 LLM Calls</span>
                <span class="section-toggle">▼</span>
            </div>
            <div class="section-content">
                {llm_calls_html}
            </div>
        </div>''' if total_llm_calls else ''}
        
        {f'''<div class="section" id="tools-section">
            <div class="section-header" onclick="toggleSection('tools-section')">
                <span>🔧 Tool Calls</span>
                <span class="section-toggle">▼</span>
            </div>
            <div class="section-content">
                {tool_calls_html}
            </div>
        </div>''' if total_tool_calls else ''}
    </div>

    <script>
        // Theme management — matches Cuttle (dark → midnight → light)
        function initializeTheme() {{
            const savedTheme = localStorage.getItem('theme') || 'dark';
            const body = document.body;
            body.classList.remove('dark-mode', 'midnight-mode', 'light-mode');
            document.documentElement.classList.remove('dark-mode', 'midnight-mode', 'light-mode');
            let cls = 'dark-mode';
            if (savedTheme === 'light') cls = 'light-mode';
            else if (savedTheme === 'midnight') cls = 'midnight-mode';
            body.classList.add(cls);
            document.documentElement.classList.add(cls);
        }}
        
        function toggleTheme() {{
            const body = document.body;
            let next = 'dark';
            if (body.classList.contains('dark-mode')) next = 'midnight';
            else if (body.classList.contains('midnight-mode')) next = 'light';
            else next = 'dark';
            localStorage.setItem('theme', next);
            initializeTheme();
            try {{
                if (window.parent && window.parent !== window) {{
                    window.parent.postMessage({{ type: 'cuttle-theme-change', theme: next }}, '*');
                }}
            }} catch (e) {{}}
        }}

        window.addEventListener('message', function(e) {{
            if (e && e.data && e.data.type === 'cuttle-theme-change' && e.data.theme) {{
                localStorage.setItem('theme', e.data.theme);
                initializeTheme();
            }}
        }});
        window.addEventListener('storage', function(e) {{
            if (e.key === 'theme') initializeTheme();
        }});
        
        // Section collapse/expand functionality
        function toggleSection(sectionId) {{
            const section = document.getElementById(sectionId);
            if (section) {{
                section.classList.toggle('collapsed');
                
                // Save section state to localStorage
                const collapsedSections = JSON.parse(localStorage.getItem('collapsedSections') || '[]');
                if (section.classList.contains('collapsed')) {{
                    if (!collapsedSections.includes(sectionId)) {{
                        collapsedSections.push(sectionId);
                    }}
                }} else {{
                    const index = collapsedSections.indexOf(sectionId);
                    if (index > -1) {{
                        collapsedSections.splice(index, 1);
                    }}
                }}
                localStorage.setItem('collapsedSections', JSON.stringify(collapsedSections));
            }}
        }}
        
        // Initialize section states on page load
        function initializeSections() {{
            const collapsedSections = JSON.parse(localStorage.getItem('collapsedSections') || '[]');
            collapsedSections.forEach(sectionId => {{
                const section = document.getElementById(sectionId);
                if (section) {{
                    section.classList.add('collapsed');
                }}
            }});
        }}
        
        // Initialize compact report on page load
        document.addEventListener('DOMContentLoaded', function() {{
            initializeTheme();
            initializeSections();
        }});
    </script>
</body>
</html>"""
    
    def _create_agent_graph_html(self) -> str:
        """Create the agent execution graph visualization"""
        try:
            # Check if we have graph structure from node editor
            graph_structure = self.execution_data.get("graph_structure", {})
            
            # Validate graph structure
            if not isinstance(graph_structure, dict):
                print(f"[GRAPH ERROR] graph_structure is not a dict: {type(graph_structure)}")
                return self._create_linear_graph_html()
            
            nodes = graph_structure.get("nodes", [])
            if not isinstance(nodes, list):
                print(f"[GRAPH ERROR] nodes is not a list: {type(nodes)}")
                return self._create_linear_graph_html()
            
            has_graph_structure = len(nodes) > 0
            
            if has_graph_structure:
                print(f"[GRAPH] Using node editor graph with {len(nodes)} nodes")
                return self._create_node_editor_graph_html()
            else:
                print(f"[GRAPH] Using linear graph (no node structure)")
                return self._create_linear_graph_html()
        except Exception as e:
            print(f"[GRAPH ERROR] Exception in _create_agent_graph_html: {e}")
            import traceback
            traceback.print_exc()
            # Fallback to linear graph
            try:
                return self._create_linear_graph_html()
            except Exception as fallback_error:
                print(f"[GRAPH ERROR] Even linear graph failed: {fallback_error}")
                return "<div class='agent-graph'>Error creating graph visualization</div>"
    
    def _create_linear_graph_html(self) -> str:
        """Create a linear execution graph for non-node-editor executions"""
        try:
            if not self.execution_data.get("execution_stages") and not self.execution_data.get("tool_calls"):
                return "<div class='agent-graph'>No execution stages or tool calls recorded</div>"
            
            # Get execution mode for display
            execution_mode = self.execution_data.get("execution_mode", "multi-lite")
            mode_display = {
                "single": "Single Stage (Direct LLM)",
                "multi": "Multi-Stage (Regex → Evaluation → Execution)",
                "multi-lite": "Multi-Lite (Regex → Combined LLM)"
            }.get(execution_mode, execution_mode)
            
            graph_html = f"<div class='agent-graph'><div class='execution-mode-badge'>{mode_display}</div>"
            
            # Combine execution stages and tool calls into a chronological flow
            all_events = []
            
            # Add execution stages with validation
            for stage in self.execution_data.get("execution_stages", []):
                if isinstance(stage, dict):
                    all_events.append({
                        "type": "stage",
                        "data": stage,
                        "start_time": stage.get("start_time", 0)
                    })
                else:
                    print(f"[GRAPH WARNING] Invalid stage data (not a dict): {type(stage)}")
            
            # Add tool calls with validation
            for tool_call in self.execution_data.get("tool_calls", []):
                if isinstance(tool_call, dict):
                    all_events.append({
                        "type": "tool",
                        "data": tool_call,
                        "start_time": tool_call.get("start_time", 0)
                    })
                else:
                    print(f"[GRAPH WARNING] Invalid tool_call data (not a dict): {type(tool_call)}")
            
            # Sort by start time to create chronological flow
            try:
                all_events.sort(key=lambda x: x.get("start_time", 0))
            except Exception as sort_error:
                print(f"[GRAPH WARNING] Error sorting events: {sort_error}")
        
        except Exception as e:
            print(f"[GRAPH ERROR] Error in _create_linear_graph_html initialization: {e}")
            import traceback
            traceback.print_exc()
            return "<div class='agent-graph'>Error creating linear graph visualization</div>"
        
        try:
            for i, event in enumerate(all_events):
                try:
                    if not isinstance(event, dict):
                        print(f"[GRAPH WARNING] Invalid event (not a dict): {type(event)}")
                        continue
                    
                    if event.get("type") == "stage":
                        stage = event.get("data", {})
                        if not isinstance(stage, dict):
                            print(f"[GRAPH WARNING] Invalid stage data: {type(stage)}")
                            continue
                        
                        stage_type = stage.get("type", "unknown")
                        stage_name = stage.get("name", "Unknown Stage")
                        duration = stage.get("duration", 0)
                        
                        # Create node with appropriate styling
                        node_class = "flow-node"
                        if stage_type == "llm":
                            node_class += " llm"
                        elif stage_type == "tool":
                            node_class += " tool"
                        elif stage_type == "regex":
                            node_class += " regex"
                        elif stage_type == "input":
                            node_class += " input"
                        elif stage_type == "output":
                            node_class += " output"
                        
                        # Build node content with model and token info
                        node_content = f"{stage_name}<br><small>{duration:.2f}s</small>"
                        
                        # Add model info if available
                        if stage.get("model"):
                            node_content += f"<br><small>🦑 {stage['model']}</small>"
                        
                        # Add token info if available
                        tokens_data = stage.get("tokens")
                        if isinstance(tokens_data, dict) and tokens_data.get("total_tokens"):
                            tokens = tokens_data["total_tokens"]
                            node_content += f"<br><small>📊 {tokens} tokens</small>"
                        
                        graph_html += f'<span class="{node_class}">{node_content}</span>'
                        
                    elif event.get("type") == "tool":
                        tool_call = event.get("data", {})
                        if not isinstance(tool_call, dict):
                            print(f"[GRAPH WARNING] Invalid tool_call data: {type(tool_call)}")
                            continue
                        
                        tool_name = tool_call.get("tool_name", "Unknown Tool")
                        duration = tool_call.get("duration", 0)
                        success = tool_call.get("success", True)
                        
                        # Create tool node with appropriate styling
                        node_class = "flow-node tool"
                        if not success:
                            node_class += " error"
                        
                        # Build tool node content
                        node_content = f"🔧 {tool_name}<br><small>{duration:.2f}s</small>"
                        
                        # Add model info if available (for LLM-based tools like claude_code)
                        if tool_call.get("model"):
                            node_content += f"<br><small>🦑 {tool_call['model']}</small>"
                        
                        # Add token info if available
                        tokens_data = tool_call.get("tokens")
                        if isinstance(tokens_data, dict) and tokens_data.get("total_tokens"):
                            tokens = tokens_data["total_tokens"]
                            node_content += f"<br><small>📊 {tokens} tokens</small>"
                        
                        # Add parameters preview if available
                        params = tool_call.get("parameters")
                        if isinstance(params, dict):
                            # Show key parameters
                            param_preview = []
                            try:
                                for key, value in list(params.items())[:2]:  # Show first 2 params
                                    if isinstance(value, str) and len(value) > 20:
                                        value = value[:20] + "..."
                                    param_preview.append(f"{key}: {value}")
                                if param_preview:
                                    node_content += f"<br><small>📋 {', '.join(param_preview)}</small>"
                            except Exception as param_error:
                                print(f"[GRAPH WARNING] Error processing tool parameters: {param_error}")
                        
                        graph_html += f'<span class="{node_class}">{node_content}</span>'
                
                    # Add arrow between events (except for the last one)
                    if i < len(all_events) - 1:
                        graph_html += '<span class="flow-arrow">↓</span>'
                
                except Exception as event_error:
                    print(f"[GRAPH ERROR] Error rendering event {i}: {event_error}")
                    graph_html += '<span class="flow-node error">Error rendering event</span>'
        
        except Exception as loop_error:
            print(f"[GRAPH ERROR] Error in event rendering loop: {loop_error}")
            import traceback
            traceback.print_exc()
            graph_html += "<div class='error-message'>Error rendering execution flow</div>"
        
        graph_html += "</div>"
        return graph_html
    
    def _create_node_editor_graph_html(self) -> str:
        """Create a graph visualization for node editor pipelines (like LangGraph)"""
        try:
            graph_structure = self.execution_data.get("graph_structure", {})
            all_nodes = graph_structure.get("nodes", [])
            all_connections = graph_structure.get("connections", [])
            execution_order = graph_structure.get("execution_order", [])
            
            # Validate data types
            if not isinstance(all_nodes, list):
                print(f"[GRAPH ERROR] all_nodes is not a list: {type(all_nodes)}")
                return "<div class='agent-graph'>Invalid graph data: nodes must be a list</div>"
            
            if not isinstance(all_connections, list):
                print(f"[GRAPH ERROR] all_connections is not a list: {type(all_connections)}")
                all_connections = []  # Use empty list as fallback
            
            if not isinstance(execution_order, list):
                print(f"[GRAPH ERROR] execution_order is not a list: {type(execution_order)}")
                execution_order = []  # Use empty list as fallback
            
            # Validate and collect all valid nodes (both executed and skipped)
            # Keep track of valid node IDs for connection filtering
            valid_node_ids = set()
            nodes = []
            for node in all_nodes:
                if not isinstance(node, dict):
                    print(f"[GRAPH WARNING] Skipping invalid node (not a dict): {type(node)}")
                    continue
                if not "id" in node:
                    print(f"[GRAPH WARNING] Skipping node without id: {node}")
                    continue
                valid_node_ids.add(node["id"])
                nodes.append(node)
            
            # Validate connections to ensure both endpoints exist
            connections = []
            for conn in all_connections:
                if not isinstance(conn, dict):
                    print(f"[GRAPH WARNING] Skipping invalid connection (not a dict): {type(conn)}")
                    continue
                if "from" not in conn or "to" not in conn:
                    print(f"[GRAPH WARNING] Skipping connection missing from/to: {conn}")
                    continue
                # Only include connections where both nodes exist in the graph
                if conn["from"] in valid_node_ids and conn["to"] in valid_node_ids:
                    connections.append(conn)
            
            if not nodes:
                return "<div class='agent-graph'>No pipeline nodes found</div>"
            
            # Count executed vs skipped nodes for display
            executed_count = sum(1 for node in nodes if node.get("executed", False))
            skipped_count = len(nodes) - executed_count
            
            print(f"[GRAPH] Creating graph with {len(nodes)} total nodes ({executed_count} executed, {skipped_count} skipped) and {len(connections)} connections")
            
        except Exception as e:
            print(f"[GRAPH ERROR] Error in initial graph data processing: {e}")
            import traceback
            traceback.print_exc()
            return f"<div class='agent-graph'>Error processing graph data: {str(e)}</div>"
        
        # Create a graph visualization with proper layout
        graph_html = "<div class='agent-graph graph-pipeline'>"
        graph_html += "<div class='graph-title'>Pipeline Execution Graph</div>"
        
        # Display executed and skipped counts in subtitle
        if skipped_count > 0:
            graph_html += f"<div class='graph-subtitle'>{len(nodes)} node(s) total: {executed_count} executed, {skipped_count} skipped</div>"
        else:
            graph_html += f"<div class='graph-subtitle'>{len(nodes)} node(s) executed</div>"
        
        # Build execution order lookup with layer-aware numbering
        # Group nodes by layer for parallel numbering
        exec_order_map = {}
        layer_counters = {}  # Track which nodes are in same layer
        
        try:
            for idx, item in enumerate(execution_order):
                if isinstance(item, dict) and "node_id" in item:
                    exec_order_map[item["node_id"]] = idx
                else:
                    print(f"[GRAPH WARNING] Invalid execution_order item: {item}")
        except Exception as e:
            print(f"[GRAPH ERROR] Error building execution order map: {e}")
        
        # Find root nodes (nodes with no incoming connections)
        try:
            incoming_connections = {conn["to"] for conn in connections}
            root_nodes = [node for node in nodes if node.get("id") not in incoming_connections]
            
            if not root_nodes and nodes:
                # If no root nodes found (circular?), use first node
                print(f"[GRAPH WARNING] No root nodes found, using first node as root")
                root_nodes = [nodes[0]]
            
            print(f"[GRAPH] Found {len(root_nodes)} root node(s)")
        except Exception as e:
            print(f"[GRAPH ERROR] Error finding root nodes: {e}")
            # Fallback: use all nodes without incoming connections or first node
            root_nodes = [nodes[0]] if nodes else []
        
        # Build adjacency list for graph traversal
        adjacency = {}
        try:
            for conn in connections:
                from_id = conn.get("from")
                to_id = conn.get("to")
                if from_id and to_id:
                    if from_id not in adjacency:
                        adjacency[from_id] = []
                    adjacency[from_id].append(to_id)
        except Exception as e:
            print(f"[GRAPH ERROR] Error building adjacency list: {e}")
            import traceback
            traceback.print_exc()
        
        # Track visited nodes and create layers (BFS for proper layout)
        visited = set()
        layers = []
        try:
            current_layer = [node["id"] for node in root_nodes if "id" in node]
            
            # Limit iterations to prevent infinite loops
            max_iterations = len(nodes) + 10
            iteration = 0
            
            while current_layer and iteration < max_iterations:
                iteration += 1
                layers.append(current_layer)
                next_layer = []
                for node_id in current_layer:
                    visited.add(node_id)
                    # Add children that haven't been visited
                    for child_id in adjacency.get(node_id, []):
                        if child_id not in visited and child_id not in next_layer:
                            # Check if all parents have been visited
                            parents = [conn["from"] for conn in connections if conn["to"] == child_id]
                            if all(p in visited for p in parents):
                                next_layer.append(child_id)
                current_layer = next_layer
            
            if iteration >= max_iterations:
                print(f"[GRAPH WARNING] Layer building exceeded max iterations, possible circular dependency")
            
            print(f"[GRAPH] Created {len(layers)} layer(s), visited {len(visited)}/{len(nodes)} nodes")
            
        except Exception as e:
            print(f"[GRAPH ERROR] Error creating layers: {e}")
            import traceback
            traceback.print_exc()
            # Fallback: create single layer with all nodes
            layers = [[node["id"] for node in nodes if "id" in node]]
        
        # Create node lookup
        try:
            node_map = {node["id"]: node for node in nodes if "id" in node}
        except Exception as e:
            print(f"[GRAPH ERROR] Error creating node map: {e}")
            node_map = {}
        
        # Render layers
        execution_counter = 1  # Track overall execution order
        
        try:
            for layer_idx, layer in enumerate(layers):
                try:
                    # Check if layer has parallel execution (multiple nodes)
                    is_parallel = len(layer) > 1
                    
                    if is_parallel:
                        graph_html += f"<div class='graph-layer parallel-layer'>"
                        graph_html += f"<div class='parallel-container'>"
                    else:
                        graph_html += f"<div class='graph-layer'>"
                    
                    # For parallel layers, use sub-numbering (e.g., #2:1, #2:2, #2:3)
                    layer_base_number = execution_counter
                    
                    for sub_idx, node_id in enumerate(layer):
                        try:
                            node = node_map.get(node_id)
                            if not node:
                                print(f"[GRAPH WARNING] Node {node_id} not found in node_map")
                                continue
                            
                            # Determine node styling
                            node_class = "flow-node"
                            icon = "●"
                            
                            # Category-based styling
                            if node.get("category") == "trigger":
                                node_class += " trigger"
                                icon = "🎯"
                            elif node.get("category") == "llm":
                                node_class += " llm"
                                icon = "🤖"
                            elif node.get("category") == "input":
                                node_class += " input"
                                icon = "📥"
                            elif node.get("category") == "tool":
                                node_class += " tool"
                                icon = "🛠️"
                            elif node.get("category") == "output":
                                node_class += " output"
                                icon = "📤"
                            elif node.get("category") == "group":
                                node_class += " group"
                                icon = "📦"
                            elif node.get("category") == "utility":
                                node_class += " utility"
                                icon = "🔧"
                            
                            # Execution status styling
                            if node.get("executed"):
                                if node.get("success"):
                                    node_class += " executed success"
                                else:
                                    node_class += " executed error"
                            else:
                                node_class += " not-executed"
                            
                            # Build node content
                            node_name = node.get("name", "Unknown")
                            node_type = node.get("type", "")
                            # Prefer friendly display names for known node types
                            display_name = {
                                "tool-router": "Agent Router",
                                "tool-remote-agent": "Coding agent (Claude Code / Cursor)",
                            }.get(node_type) or node_name
                            node_content = f"{icon} {display_name}"
                            
                            if node.get("executed"):
                                duration = node.get("duration", 0)
                                node_content += f"<br><small>⏱️ {duration:.2f}s</small>"
                                
                                # Use execution_order from the node if available (from executionOrderMap)
                                # Otherwise fall back to layer-based numbering
                                exec_order_value = node.get("execution_order")
                                display_id_value = node.get("display_id")
                                print(f"[GRAPH RENDER] Node '{display_name}' (ID: {str(node_id)[:8]}...): execution_order={exec_order_value}, display_id={display_id_value}")
                                
                                if exec_order_value:
                                    # Use the actual execution order from the node editor
                                    node_content += f"<br><small class='exec-order'>{exec_order_value}</small>"
                                    print(f"[GRAPH RENDER]   Using execution_order: {exec_order_value}")
                                elif display_id_value:
                                    # Use display_id if available
                                    node_content += f"<br><small class='exec-order'>{display_id_value}</small>"
                                    print(f"[GRAPH RENDER]   Using display_id: {display_id_value}")
                                else:
                                    # Fall back to layer-based numbering
                                    if is_parallel:
                                        # For parallel nodes, show as #N:1, #N:2, #N:3
                                        fallback_value = f"#{layer_base_number}:{sub_idx + 1}"
                                        node_content += f"<br><small class='exec-order'>{fallback_value}</small>"
                                        print(f"[GRAPH RENDER]   Using fallback (parallel): {fallback_value}")
                                    else:
                                        # For sequential nodes, show as #N
                                        fallback_value = f"#{layer_base_number}"
                                        node_content += f"<br><small class='exec-order'>{fallback_value}</small>"
                                        print(f"[GRAPH RENDER]   Using fallback (sequential): {fallback_value}")
                            else:
                                # Node was not executed - add skipped indicator
                                node_content += f"<br><small class='skipped-label'>⏭️ SKIPPED</small>"
                                # Optionally show execution order if available
                                exec_order_value = node.get("execution_order")
                                display_id_value = node.get("display_id")
                                if exec_order_value:
                                    node_content += f"<br><small class='exec-order-skipped'>{exec_order_value}</small>"
                                elif display_id_value:
                                    node_content += f"<br><small class='exec-order-skipped'>{display_id_value}</small>"
                
                            graph_html += f'<div class="{node_class}" data-node-id="{node_id}">{node_content}</div>'
                        
                        except Exception as node_error:
                            print(f"[GRAPH ERROR] Error rendering node {node_id}: {node_error}")
                            graph_html += f'<div class="flow-node error">Error rendering node</div>'
                    
                    # Increment counter for next layer
                    if is_parallel:
                        # All parallel nodes count as one execution step
                        execution_counter += 1
                    else:
                        # Sequential node increments normally
                        execution_counter += 1
                    
                    if is_parallel:
                        graph_html += "</div>"  # Close parallel-container
                        graph_html += "<div class='parallel-merge-indicator'>⚡ Parallel Execution</div>"
                    
                    graph_html += "</div>"  # Close graph-layer
                    
                    # Add arrow to next layer (except for last layer)
                    if layer_idx < len(layers) - 1:
                        next_layer = layers[layer_idx + 1]
                        if len(layer) > 1 or len(next_layer) > 1:
                            # Multiple connections
                            graph_html += '<div class="flow-connector">↓</div>'
                        else:
                            # Single connection
                            graph_html += '<div class="flow-arrow-simple">↓</div>'
                
                except Exception as layer_error:
                    print(f"[GRAPH ERROR] Error rendering layer {layer_idx}: {layer_error}")
                    graph_html += f'<div class="graph-layer"><div class="flow-node error">Error rendering layer</div></div>'
        
        except Exception as render_error:
            print(f"[GRAPH ERROR] Error in layer rendering loop: {render_error}")
            import traceback
            traceback.print_exc()
            graph_html += "<div class='error-message'>Error rendering graph layers</div>"
        
        graph_html += "</div>"
        
        # Add CSS for the graph visualization
        graph_html += """
<style>
.agent-graph.graph-pipeline {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 20px;
    min-height: 300px;
}

.graph-title {
    font-size: 1.1em;
    font-weight: 600;
    margin-bottom: 20px;
    color: var(--text-primary);
}

.graph-layer {
    display: flex;
    flex-direction: column;
    align-items: center;
    margin: 10px 0;
}

.parallel-layer {
    position: relative;
}

.parallel-container {
    display: flex;
    flex-direction: row;
    gap: 20px;
    justify-content: center;
    align-items: flex-start;
}

.parallel-merge-indicator {
    font-size: 0.85em;
    color: var(--accent-color);
    margin-top: 8px;
    font-weight: 600;
}

.flow-connector {
    font-size: 1.5em;
    color: var(--text-secondary);
    margin: 5px 0;
}

.flow-arrow-simple {
    font-size: 1.2em;
    color: var(--text-secondary);
    margin: 5px 0;
}

.flow-node {
    padding: 12px 20px;
    border-radius: 8px;
    border: 2px solid var(--border-color);
    background: var(--card-bg);
    font-size: 0.9em;
    text-align: center;
    min-width: 120px;
    transition: all 0.2s;
}

.flow-node.executed {
    border-width: 3px;
}

.flow-node.success {
    border-color: #10b981;
    background: rgba(16, 185, 129, 0.1);
}

.flow-node.error {
    border-color: #ef4444;
    background: rgba(239, 68, 68, 0.1);
}

.flow-node.not-executed {
    opacity: 0.6;
    border-style: dashed;
    border-width: 2px;
    background: repeating-linear-gradient(
        45deg,
        rgba(128, 128, 128, 0.05),
        rgba(128, 128, 128, 0.05) 10px,
        rgba(128, 128, 128, 0.1) 10px,
        rgba(128, 128, 128, 0.1) 20px
    );
}

.skipped-label {
    display: inline-block;
    padding: 2px 8px;
    background: rgba(156, 163, 175, 0.3);
    color: var(--text-secondary);
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.7em;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-top: 4px;
}

.exec-order-skipped {
    display: inline-block;
    padding: 2px 6px;
    background: rgba(156, 163, 175, 0.3);
    color: var(--text-secondary);
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.75em;
    opacity: 0.7;
}

.flow-node.trigger {
    border-color: #8b5cf6;
}

.flow-node.llm {
    border-color: #3b82f6;
}

.flow-node.input {
    border-color: #10b981;
}

.flow-node.tool {
    border-color: #f59e0b;
}

.flow-node.output {
    border-color: #10b981;
}

.flow-node.group {
    border-color: #ec4899;
}

.flow-node.utility {
    border-color: #6366f1;
}

.exec-order {
    display: inline-block;
    padding: 2px 6px;
    background: var(--accent-color);
    color: var(--text-inverse);
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.75em;
}

.execution-mode-badge {
    display: inline-block;
    padding: 6px 12px;
    background: var(--accent-color);
    color: var(--text-inverse);
    border-radius: 4px;
    font-size: 0.85em;
    font-weight: 600;
    margin-bottom: 15px;
}
</style>
"""
        
        return graph_html
    
    def _create_stages_html(self) -> str:
        """Create HTML for execution stages"""
        if not self.execution_data["execution_stages"]:
            return "<p>No execution stages recorded</p>"
        
        stages_html = ""
        for stage in self.execution_data["execution_stages"]:
            status_icon = "✅" if stage["success"] else "❌"
            
            # Build model and token information section
            model_info_html = ""
            if stage.get("model") or (stage.get("tokens") and stage["tokens"]):
                model_info_html = '<div class="stage-model-info">'
                
                if stage.get("model"):
                    model_info_html += f'<span class="model-name">🦑 {stage["model"]}</span>'
                
                if stage.get("tokens") and stage["tokens"]:
                    tokens = stage["tokens"]
                    if "total_tokens" in tokens:
                        model_info_html += f'<span class="token-count">{tokens["total_tokens"]} tokens</span>'
                        
                        if "prompt_tokens" in tokens and "completion_tokens" in tokens:
                            model_info_html += f'<div class="token-breakdown">Prompt: {tokens["prompt_tokens"]} | Completion: {tokens["completion_tokens"]}</div>'
                
                model_info_html += '</div>'
            
            stages_html += f"""
            <div class="stage-item">
                <div class="stage-header">
                    <div class="stage-name">{status_icon} {stage['name']}</div>
                    <div class="stage-duration">{stage['duration']:.2f}s</div>
                </div>
                <div class="stage-details">
                    Type: {stage['type']} | 
                    Start: {stage['start_time']:.3f}s | 
                    End: {stage['end_time']:.3f}s
                </div>
                {model_info_html}
            </div>
            """
        
        return stages_html
    
    def _create_llm_calls_html(self) -> str:
        """Create HTML for LLM calls"""
        if not self.execution_data["llm_calls"]:
            return (
                "<p>No LLM calls recorded.</p>"
                "<p class=\"subtitle\" style=\"margin-top:8px;color:#888;font-size:0.9em;\">"
                "Pipeline <strong>llm-*</strong> nodes and <strong>/api/llm-request</strong> calls appear here. "
                "If you only ran the <strong>Coding agent</strong> node, its work is tracked under "
                "<strong>Claude Code (…)</strong> here after the run completes (older reports may only show it under tool calls)."
                "</p>"
            )
        
        llm_html = ""
        original_prompt = str(self.execution_data.get("user_input") or "").strip()
        for idx, call in enumerate(self.execution_data["llm_calls"], start=1):
            status_icon = "✅" if call["success"] else "❌"
            
            # Add warning icon if cost is estimated
            cost_label = "est. cost" if call.get("cost_is_estimated", False) else "cost"
            
            # Create call number badge with node ID if available
            node_id = call.get("node_id", None)
            if node_id:
                call_badge = f'<span class="call-number-badge">#{idx} · node {html.escape(str(node_id))}</span>'
            else:
                call_badge = f'<span class="call-number-badge">#{idx}</span>'
            
            # Extract additional context
            call_time = datetime.fromtimestamp(call['start_time']).strftime('%H:%M:%S') if 'start_time' in call else ''
            
            # Get prompt preview if available
            prompt_preview = call.get('prompt_preview', '')
            prompt_html = ''
            # The top Prompt section already shows the original request. Only repeat
            # this here when an LLM actually received a transformed prompt.
            if prompt_preview and str(prompt_preview).strip() != original_prompt:
                prompt_html = f"""
                <div class="stage-details" style="margin-top: 10px;">
                    <strong>📥 Transformed prompt:</strong><br>
                    <div class="response-preview" style="background-color: rgba(100, 150, 255, 0.05); border-left: 3px solid rgba(100, 150, 255, 0.5);">{html.escape(str(prompt_preview))}</div>
                </div>
                """

            knowledge_inputs = call.get('knowledge_inputs') or []
            knowledge_html = ''
            if knowledge_inputs:
                file_blocks = []
                seen_knowledge = set()
                for ki in knowledge_inputs:
                    if not isinstance(ki, dict):
                        continue
                    knowledge_key = str(ki.get('path') or ki.get('name') or 'knowledge')
                    if knowledge_key in seen_knowledge:
                        continue
                    seen_knowledge.add(knowledge_key)
                    fname = html.escape(str(ki.get('name') or 'knowledge'))
                    kind = str(ki.get('kind') or '').strip()
                    kind_badge = f' <span class="knowledge-kind">{html.escape(kind)}</span>' if kind else ''
                    content = html.escape(str(ki.get('content') or ''))
                    file_blocks.append(f"""
                    <details class="knowledge-file">
                        <summary class="knowledge-file-summary">{fname}{kind_badge}</summary>
                        <div class="response-preview knowledge-file-content">{content}</div>
                    </details>
                    """)
                if file_blocks:
                    n = len(file_blocks)
                    label = f"{n} file{'s' if n != 1 else ''}"
                    knowledge_html = f"""
                <div class="stage-details knowledge-section" style="margin-top: 10px;">
                    <details class="knowledge-root" open>
                        <summary><strong>📚 Input (Knowledge):</strong> {label}</summary>
                        <div class="knowledge-files">
                            {''.join(file_blocks)}
                        </div>
                    </details>
                </div>
                """
            
            llm_html += f"""
            <div class="stage-item">
                <div class="stage-header">
                    <div class="stage-name">{call_badge} {status_icon} {html.escape(str(call['model']))}</div>
                </div>
                <div class="summary-chips call-metrics" aria-label="LLM call metrics">
                    <span class="summary-chip"><span class="chip-value">{call['total_tokens']:,}</span><span class="chip-label">tokens</span></span>
                    <span class="summary-chip"><span class="chip-value">{call['prompt_tokens']:,}</span><span class="chip-label">in</span></span>
                    <span class="summary-chip"><span class="chip-value">{call['completion_tokens']:,}</span><span class="chip-label">out</span></span>
                    <span class="summary-chip"><span class="chip-value">${call['cost']:.6f}</span><span class="chip-label">{cost_label}</span></span>
                    <span class="summary-chip"><span class="chip-value">{call['duration']:.2f}s</span><span class="chip-label">runtime</span></span>
                    {f'<span class="summary-chip"><span class="chip-value">{call_time}</span><span class="chip-label">started</span></span>' if call_time else ''}
                </div>
                {f'<div class="stage-details" style="margin-top: 6px; color: #888; font-size: 0.9em;">📌 {html.escape(str(call.get("notes") or ""))}</div>' if call.get("notes") else ""}
                {prompt_html}
                {knowledge_html}
                <div class="stage-details" style="margin-top: 10px;">
                    <strong>📤 Output (Response):</strong><br>
                    <div class="response-preview" style="background-color: rgba(100, 255, 150, 0.05); border-left: 3px solid rgba(100, 255, 150, 0.5);">{html.escape(str(call.get('response_preview') or ''))}</div>
                </div>
            </div>
            """
        
        return llm_html
    
    def _create_cost_breakdown_html(self) -> str:
        """Create HTML for cost breakdown"""
        total_cost = self.execution_data["total_cost"]
        llm_calls = self.execution_data["llm_calls"]
        
        if not llm_calls:
            return "<p>No LLM calls recorded.</p>"
        
        # Count estimated vs actual costs
        estimated_count = sum(1 for call in llm_calls if call.get("cost_is_estimated", False))
        actual_count = len(llm_calls) - estimated_count
        
        # Group costs by model
        model_costs = {}
        for call in llm_calls:
            model = call['model']
            if model not in model_costs:
                model_costs[model] = {
                    'total_cost': 0,
                    'total_tokens': 0,
                    'calls': 0,
                    'estimated_calls': 0,
                    'actual_calls': 0,
                    'pricing': self.model_pricing.get(model, self.model_pricing['gpt-4o-mini'])
                }
            model_costs[model]['total_cost'] += call['cost']
            model_costs[model]['total_tokens'] += call['total_tokens']
            model_costs[model]['calls'] += 1
            
            if call.get("cost_is_estimated", False):
                model_costs[model]['estimated_calls'] += 1
            else:
                model_costs[model]['actual_calls'] += 1
        
        # Determine overall cost type
        cost_type = "Mixed"
        if estimated_count == 0:
            cost_type = "Actual from API"
        elif actual_count == 0:
            cost_type = "Estimated"
        
        cost_html = f"""
        <div class="cost-summary">
            <h3>Total Cost: ${total_cost:.6f}</h3>
            <p class="cost-note"><strong>Cost Type:</strong> {cost_type} ({actual_count} actual, {estimated_count} estimated)</p>
        </div>
        """
        
        # Add warning if any costs are estimated
        if estimated_count > 0:
            cost_html += """
            <div class="cost-warning">
                <p><strong>⚠️ Estimated:</strong> Token-based estimates may exclude cache usage, discounts, and regional pricing.</p>
            </div>
            """
        
        cost_html += '<div class="model-breakdown">'
        
        for model, data in model_costs.items():
            pricing = data['pricing']
            
            # Show estimated vs actual breakdown for this model
            cost_type_label = ""
            if data['estimated_calls'] > 0 and data['actual_calls'] > 0:
                cost_type_label = f" ({data['actual_calls']} actual, {data['estimated_calls']} estimated)"
            elif data['estimated_calls'] > 0:
                cost_type_label = " (estimated)"
            elif data['actual_calls'] > 0:
                cost_type_label = " (actual from API)"
            
            cost_html += f"""
            <div class="model-cost-card">
                <h4>{model}{cost_type_label}</h4>
                <div class="cost-details">
                    <div class="cost-item">
                        <span class="cost-label">Total Cost:</span>
                        <span class="cost-value">${data['total_cost']:.6f}</span>
                    </div>
                    <div class="cost-item">
                        <span class="cost-label">Total Tokens:</span>
                        <span class="cost-value">{data['total_tokens']:,}</span>
                    </div>
                    <div class="cost-item">
                        <span class="cost-label">Calls:</span>
                        <span class="cost-value">{data['calls']}</span>
                    </div>
                    <div class="cost-item">
                        <span class="cost-label">Pricing:</span>
                        <span class="cost-value">${pricing['input']:.2f}/${pricing['output']:.2f} per 1M tokens</span>
                    </div>
                </div>
            </div>
            """
        
        cost_html += "</div>"
        return cost_html
    
    def _create_tool_calls_html(self) -> str:
        """Create HTML for tool calls"""
        if not self.execution_data["tool_calls"]:
            return "<p>No tool calls recorded</p>"
        
        tools_html = ""
        for call in self.execution_data["tool_calls"]:
            status_icon = "✅" if call["success"] else "❌"
            
            # Build model and token information section
            model_info_html = ""
            if call.get("model") or (call.get("tokens") and call["tokens"]):
                model_info_html = '<div class="stage-model-info">'
                
                if call.get("model"):
                    model_info_html += f'<span class="model-name">🦑 {call["model"]}</span>'
                
                if call.get("tokens") and call["tokens"]:
                    tokens = call["tokens"]
                    if "total_tokens" in tokens:
                        model_info_html += f'<span class="token-count">{tokens["total_tokens"]} tokens</span>'
                        
                        if "prompt_tokens" in tokens and "completion_tokens" in tokens:
                            model_info_html += f'<div class="token-breakdown">Prompt: {tokens["prompt_tokens"]} | Completion: {tokens["completion_tokens"]}</div>'
                
                if call.get("cost", 0) > 0:
                    model_info_html += f'<div class="token-breakdown">Cost: ${call["cost"]:.6f}</div>'
                
                model_info_html += '</div>'
            
            tools_html += f"""
            <div class="stage-item">
                <div class="stage-header">
                    <div class="stage-name">{status_icon} {call['tool_name']}</div>
                    <div class="stage-duration">{call['duration']:.2f}s</div>
                </div>
                <div class="stage-details">
                    Parameters: {json.dumps(call['parameters'], indent=2)} | 
                    Duration: {call['duration']:.2f}s
                </div>
                {model_info_html}
                <div class="stage-details">
                    Result: {call['result_preview']}
                </div>
            </div>
            """
        
        return tools_html
    
    def _create_agent_config_html(self) -> str:
        """Create HTML for agent configuration section"""
        config = self.execution_data.get("agent_config", {})
        
        if not config:
            return "<p>No agent configuration recorded</p>"
        
        config_html = '<div class="model-breakdown">'
        
        # Check if this is a node editor pipeline config
        if config.get("execution_mode") == "Node Editor Pipeline":
            # Node Editor Pipeline Configuration
            config_html += f"""
            <div class="model-cost-card">
                <h4>Pipeline Information</h4>
                <div class="cost-details">
                    <div class="cost-item">
                        <span class="cost-label">Pipeline Name:</span>
                        <span class="cost-value">{config.get('pipeline_name', 'Unknown')}</span>
                    </div>
                    <div class="cost-item">
                        <span class="cost-label">Total Nodes:</span>
                        <span class="cost-value">{config.get('node_count', 0)}</span>
                    </div>
                    <div class="cost-item">
                        <span class="cost-label">LLM Nodes:</span>
                        <span class="cost-value">{config.get('llm_nodes', 0)}</span>
                    </div>
                    <div class="cost-item">
                        <span class="cost-label">Tool Nodes:</span>
                        <span class="cost-value">{config.get('tool_nodes', 0)}</span>
                    </div>
                </div>
            </div>
            """
            
            # Models Used
            models_used = config.get('models_used', [])
            if models_used:
                models_list = ', '.join(models_used) if models_used else 'None'
                config_html += f"""
                <div class="model-cost-card">
                    <h4>Models Used</h4>
                    <div class="cost-details">
                        <div class="cost-item">
                            <span class="cost-label">LLM Models:</span>
                            <span class="cost-value">{models_list}</span>
                        </div>
                    </div>
                </div>
                """
            
            # Pipeline Description
            description = config.get('pipeline_description', '')
            if description:
                config_html += f"""
                <div class="model-cost-card">
                    <h4>Pipeline Description</h4>
                    <div class="cost-details">
                        <div class="cost-item" style="display: block;">
                            <p style="margin: 0; white-space: pre-wrap;">{description}</p>
                        </div>
                    </div>
                </div>
                """
        else:
            # Discord Bot / Agent Configuration
            # Agent Stage Mode
            stage_mode = config.get("agent_stage_mode", "unknown")
            stage_mode_display = {
                "single": "Single Stage (Direct Processing)",
                "multi": "Multi Stage (Full Pipeline)",
                "multi-lite": "Multi-Lite (Combined Evaluation & Response)"
            }.get(stage_mode, f"Unknown ({stage_mode})")
            
            config_html += f"""
            <div class="model-cost-card">
                <h4>Agent Stage Mode</h4>
                <div class="cost-details">
                    <div class="cost-item">
                        <span class="cost-label">Mode:</span>
                        <span class="cost-value">{stage_mode_display}</span>
                    </div>
                </div>
            </div>
            """
            
            # Preferred LLM Model
            preferred_model = config.get("preferred_llm_model", "unknown")
            config_html += f"""
            <div class="model-cost-card">
                <h4>Preferred LLM Model</h4>
                <div class="cost-details">
                    <div class="cost-item">
                        <span class="cost-label">Model:</span>
                        <span class="cost-value">{preferred_model}</span>
                    </div>
                </div>
            </div>
            """
            
            # LLM Fallback
            fallback_enabled = config.get("llm_fallback_enabled", False)
            config_html += f"""
            <div class="model-cost-card">
                <h4>LLM Model Fallback</h4>
                <div class="cost-details">
                    <div class="cost-item">
                        <span class="cost-label">Enabled:</span>
                        <span class="cost-value">{'Yes' if fallback_enabled else 'No'}</span>
                    </div>
                </div>
            </div>
            """
            
            # Additional settings
            mode = config.get("mode", "unknown")
            debug_mode = config.get("debug_mode", False)
            cursor_method = config.get("cursor_agent_method", "unknown")
            
            config_html += f"""
            <div class="model-cost-card">
                <h4>Additional Settings</h4>
                <div class="cost-details">
                    <div class="cost-item">
                        <span class="cost-label">Bot Mode:</span>
                        <span class="cost-value">{mode}</span>
                    </div>
                    <div class="cost-item">
                        <span class="cost-label">Debug Mode:</span>
                        <span class="cost-value">{'Yes' if debug_mode else 'No'}</span>
                    </div>
                    <div class="cost-item">
                        <span class="cost-label">Cursor Method:</span>
                        <span class="cost-value">{cursor_method}</span>
                    </div>
                </div>
            </div>
            """
        
        config_html += "</div>"
        return config_html
    


def _live_snap_from_execution(execution_data: Dict[str, Any]) -> Dict[str, Any]:
    events = execution_data.get("events") or []
    if isinstance(events, list) and len(events) > 120:
        events = events[-120:]
    return {
        "query_id": execution_data.get("query_id"),
        "timestamp": execution_data.get("timestamp"),
        "user_input": execution_data.get("user_input"),
        "success": execution_data.get("success"),
        "error_message": execution_data.get("error_message"),
        "total_execution_time": execution_data.get("total_execution_time"),
        "total_tokens": execution_data.get("total_tokens"),
        "total_cost": execution_data.get("total_cost"),
        "execution_stages": copy.deepcopy(execution_data.get("execution_stages") or []),
        "llm_calls": copy.deepcopy(execution_data.get("llm_calls") or []),
        "tool_calls": copy.deepcopy(execution_data.get("tool_calls") or []),
        "events": copy.deepcopy(events if isinstance(events, list) else []),
        "harness": copy.deepcopy(execution_data.get("harness") or {}),
        "brain": copy.deepcopy(execution_data.get("brain") or {}),
        "sent": copy.deepcopy(execution_data.get("sent") or {}),
    }


# Concurrent query tracking: one QueryReportGenerator per in-flight query.
# Historically a single global tracker held _session_lock for the whole run, so a
# second chat blocked on start_query_tracking and the UI sat on "Connecting..."
# until the first run finished (often the 900s Cursor Agent timeout).
_tls = threading.local()
_registry_lock = threading.Lock()
_active_trackers: Dict[str, "QueryReportGenerator"] = {}
_fallback_tracker: Optional["QueryReportGenerator"] = None
_shared_live_snapshots: OrderedDict[str, Dict[str, Any]] = OrderedDict()
_shared_live_snapshots_lock = threading.Lock()
_shared_live_snapshots_cap = 64


def _publish_shared_live_snapshot(query_id: str, execution_data: Dict[str, Any]) -> None:
    qid = (query_id or "").strip()
    if not qid or not execution_data:
        return
    snap = _live_snap_from_execution(execution_data)
    with _shared_live_snapshots_lock:
        _shared_live_snapshots[qid] = snap
        _shared_live_snapshots.move_to_end(qid)
        cap = max(8, int(_shared_live_snapshots_cap))
        while len(_shared_live_snapshots) > cap:
            _shared_live_snapshots.popitem(last=False)


def get_shared_live_snapshot(qid: str) -> Optional[Dict[str, Any]]:
    raw = (qid or "").strip()
    if not raw:
        return None
    with _shared_live_snapshots_lock:
        hit = _shared_live_snapshots.get(raw)
        if not hit and len(raw) > 8:
            hit = _shared_live_snapshots.get(raw[:8])
        if not hit:
            return None
        return copy.deepcopy(hit)


def _register_active_tracker(tracker: "QueryReportGenerator") -> None:
    qid = (getattr(tracker, "query_id", None) or "").strip()
    if not qid:
        return
    _tls.tracker = tracker
    with _registry_lock:
        _active_trackers[qid] = tracker


def _unregister_active_tracker(tracker: "QueryReportGenerator") -> None:
    qid = (getattr(tracker, "query_id", None) or "").strip()
    if getattr(_tls, "tracker", None) is tracker:
        _tls.tracker = None
    if not qid:
        return
    with _registry_lock:
        _active_trackers.pop(qid, None)


def get_query_tracker(query_id: str = None) -> "QueryReportGenerator":
    """Return the tracker for an in-flight query, or this thread's current tracker.

    Prefer ``query_id`` when known (e.g. ``/api/llm-request`` on another thread).
    """
    global _fallback_tracker
    if query_id is not None and str(query_id).strip():
        raw = str(query_id).strip()
        with _registry_lock:
            t = _active_trackers.get(raw)
            if t is None and len(raw) > 8:
                t = _active_trackers.get(raw[:8])
            if t is not None:
                return t
    t = getattr(_tls, "tracker", None)
    if t is not None:
        return t
    if _fallback_tracker is None:
        _fallback_tracker = QueryReportGenerator()
    return _fallback_tracker


def track_tool_call(
    tool_name: str,
    parameters: dict,
    start_time: float,
    result: str,
    success: bool = True,
    model: str = None,
    tokens: Dict = None,
    cost: float = 0.0,
):
    """Record a tool/CLI call on the in-flight query report, if any."""
    tool_end_time = time.time()
    try:
        tracker = get_query_tracker()
        if tracker.query_id:
            tracker.add_tool_call(
                tool_name=tool_name,
                parameters=parameters,
                start_time=start_time,
                end_time=tool_end_time,
                success=success,
                result_preview=result,
                model=model,
                tokens=tokens,
                cost=cost,
            )
    except Exception as e:
        print(f"[QUERY] Error tracking tool call: {e}")


def start_query_tracking(user_input: str, user_context: Dict = None) -> str:
    """Start tracking a new query on its own tracker instance (concurrent-safe)."""
    tracker = QueryReportGenerator()
    qid = tracker.start_query(user_input, user_context)
    _register_active_tracker(tracker)
    return qid


def start_query_tracking_with_id(query_id: str, user_input: str, user_context: Dict = None) -> str:
    """Start tracking with a fixed id (adhoc pipeline-run-now: stable report URL before the worker thread runs)."""
    tracker = QueryReportGenerator()
    qid = tracker.start_query_with_id(query_id, user_input, user_context)
    _register_active_tracker(tracker)
    return qid


def finish_query_tracking(success: bool = True, error_message: str = None) -> tuple:
    """Finish tracking the current thread's query and return report paths."""
    tracker = get_query_tracker()
    try:
        return tracker.finish_query(success, error_message)
    finally:
        _unregister_active_tracker(tracker)

