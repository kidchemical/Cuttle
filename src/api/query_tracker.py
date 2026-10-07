"""
In-memory query tracker. Persists JSON sidecars under web/logs (query_data_<id>.json).
"""

import copy
import json
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import uuid

class QueryTracker:
    """Tracks one query; writes JSON sidecars, not HTML."""
    
    def __init__(self, output_dir: Optional[str] = None):
        if output_dir is None:
            from api.query_events import logs_dir

            output_dir = str(logs_dir())
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
            "claude_usage": {},
            "graph_structure": {
                "nodes": [],
                "connections": [],
                "execution_order": []
            },
            "events": [],
            "harness": {},
            "brain": {},
            "sent": {},
        }

        try:
            self._add_input_stage(user_context)
        except Exception as e:
            print(f"[QUERY] Error adding input stage: {e}")

        return self.query_id
    
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
        
        if user_context.get("web_ui"):
            input_source = "Web UI"
            source_details = "Web interface"

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
            
            if input_type == "Web UI":
                destination = "Web UI Response"
                details = "Response in web interface"
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
        """Finish tracking the query and persist JSON."""
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

            print(f"[QUERY FINISH_QUERY] Saving execution data JSON...")
            try:
                json_path = self._save_execution_data()
                print(f"[QUERY FINISH_QUERY] Execution data saved: {json_path}")
            except Exception as e:
                print(f"[QUERY FINISH_QUERY] Error saving execution data: {e}")
                import traceback
                traceback.print_exc()
                json_path = None

            print(f"[QUERY FINISH_QUERY] Returning json_path={json_path}")
            return json_path, json_path
        finally:
            try:
                self._publish_live_snapshot()
            except Exception as _pe:
                print(f"[QUERY] live snapshot publish in finish_query: {_pe}")
            self._tracker_session_open = False
            try:
                self._session_lock.release()
            except RuntimeError:
                pass

    def _save_execution_data(self) -> str:
        """Persist execution data as JSON sidecars (timestamped + stable)."""
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


# Concurrent query tracking: one QueryTracker per in-flight query.
# Historically a single global tracker held _session_lock for the whole run, so a
# second chat blocked on start_query_tracking and the UI sat on "Connecting..."
# until the first run finished (often the 900s Cursor Agent timeout).
_tls = threading.local()
_registry_lock = threading.Lock()
_active_trackers: Dict[str, "QueryTracker"] = {}
_fallback_tracker: Optional["QueryTracker"] = None
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


def _register_active_tracker(tracker: "QueryTracker") -> None:
    qid = (getattr(tracker, "query_id", None) or "").strip()
    if not qid:
        return
    _tls.tracker = tracker
    with _registry_lock:
        _active_trackers[qid] = tracker


def _unregister_active_tracker(tracker: "QueryTracker") -> None:
    qid = (getattr(tracker, "query_id", None) or "").strip()
    if getattr(_tls, "tracker", None) is tracker:
        _tls.tracker = None
    if not qid:
        return
    with _registry_lock:
        _active_trackers.pop(qid, None)


def get_query_tracker(query_id: str = None) -> "QueryTracker":
    """Return the tracker for an in-flight query, or this thread's current tracker.

    Prefer ``query_id`` when known (e.g. a completion helper on another thread).
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
        _fallback_tracker = QueryTracker()
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
    tracker = QueryTracker()
    qid = tracker.start_query(user_input, user_context)
    _register_active_tracker(tracker)
    return qid


def start_query_tracking_with_id(query_id: str, user_input: str, user_context: Dict = None) -> str:
    """Start tracking with a fixed id (adhoc pipeline-run-now: stable report URL before the worker thread runs)."""
    tracker = QueryTracker()
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

