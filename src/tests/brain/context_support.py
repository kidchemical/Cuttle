"""Context fixtures using the same snapshot/preparation interfaces as the kernel."""
from api.cuttle_brain import context_delta as cd


def ack_snapshot(chat_session_id, agent_id, project_path):
    cd.record_snapshot(chat_session_id, agent_id, project_path, cd.compute_snapshot(project_path))


def delta_text(chat_session_id, agent_id, project_path):
    plan = cd.prepare_resume_delta(chat_session_id, agent_id, project_path)
    return plan.text if plan else None
