"""Database commands serialize through the event writer."""
from .writer import get_writer


def operate(ident,operation,confirm=None):
    if ident!='agent_events':
        raise ValueError('This store does not support that operation')
    if operation=='prune':
        from .maintenance import prune
        return get_writer().call(prune)
    if operation=='vacuum':
        return get_writer().call(lambda store:store.vacuum())
    if operation=='reset':
        if confirm!='agent_events':
            raise ValueError('Type agent_events to confirm the reset')
        def reset(store):
            from api.active_executions import get_executing_jobs
            if get_executing_jobs():
                raise ValueError("Wait for active turns to finish before resetting")
            with store.connection() as conn:
                if conn.execute("SELECT 1 FROM runs WHERE status='running' LIMIT 1").fetchone():
                    raise ValueError('Wait for active turns to finish before resetting')
            from .maintenance import release_refs
            release_refs(store,all_completed=True)
            return store.reset()
        return get_writer().call(reset)
    raise ValueError('Unknown maintenance operation')
