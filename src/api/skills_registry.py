"""
Skills registry for Cuttle.
A skill is a pipeline template or MCP server config. Registry is a JSON list; install loads pipeline or config.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional

DATA_DIR = Path(__file__).parent.parent / "data"
REGISTRY_FILE = DATA_DIR / "skills_registry.json"
PIPELINES_DIR = Path(__file__).parent.parent / "pipelines"

DEFAULT_REGISTRY = [
    {
        "id": "oobe_welcome",
        "name": "OOBE Welcome",
        "description": "Simple welcome pipeline with Web Chat trigger and LLM.",
        "type": "pipeline_template",
        "pipeline": {
            "name": "OOBE_Welcome",
            "nodes": [
                {"id": 1, "type": "trigger-webchat", "name": "Web Chat UI", "x": 100, "y": 100, "config": {"enabled": True}},
                {"id": 2, "type": "llm", "name": "LLM", "x": 300, "y": 100, "config": {"provider": "openai", "model": "gpt-4o-mini", "systemPrompt": "You are Cuttle, a helpful AI assistant."}},
                {"id": 3, "type": "output-webchat", "name": "Web Chat Output", "x": 500, "y": 100, "config": {}}
            ],
            "connections": [{"from": 1, "to": 2, "fromPort": 0, "toPort": 0}, {"from": 2, "to": 3, "fromPort": 0, "toPort": 0}],
            "triggers": [{"type": "trigger-webchat", "nodeId": 1}]
        },
    },
]


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_registry() -> List[Dict[str, Any]]:
    if REGISTRY_FILE.exists():
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_REGISTRY.copy()


def _save_registry(registry: List[Dict[str, Any]]) -> bool:
    _ensure_data_dir()
    try:
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)
        return True
    except Exception:
        return False


def list_skills() -> List[Dict[str, Any]]:
    """Return list of skills (id, name, description, type)."""
    registry = _load_registry()
    return [{"id": s.get("id"), "name": s.get("name"), "description": s.get("description"), "type": s.get("type", "pipeline_template")} for s in registry]


def get_skill(skill_id: str) -> Optional[Dict[str, Any]]:
    """Return full skill by id, or None."""
    registry = _load_registry()
    for s in registry:
        if s.get("id") == skill_id:
            return s
    return None


def install_skill(skill_id: str, pipeline_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Install a skill. For pipeline_template: save pipeline to pipelines/<name>.json.
    Returns { success, path?, error? }.
    """
    skill = get_skill(skill_id)
    if not skill:
        return {"success": False, "error": "Skill not found"}
    stype = skill.get("type", "pipeline_template")
    if stype == "pipeline_template":
        return {
            "success": False,
            "error": "graph_pipelines_removed",
            "message": "Skill templates that wrote pipeline graphs were removed.",
        }
    if stype == "mcp_config":
        return {"success": False, "error": "MCP config install not implemented; add server to config manually."}
    return {"success": False, "error": f"Unknown skill type: {stype}"}


def add_skill(skill: Dict[str, Any]) -> bool:
    """Add a skill to the registry. skill must have id, name, description, type, and type-specific content."""
    registry = _load_registry()
    for i, s in enumerate(registry):
        if s.get("id") == skill.get("id"):
            registry[i] = skill
            return _save_registry(registry)
    registry.append(skill)
    return _save_registry(registry)
