"""Recover durable run headers after their capture process exits."""
import os
import time

import psutil


def process_owner():
    return {'pid': os.getpid(), 'started_at': psutil.Process().create_time()}


def owner_alive(owner):
    try:
        process = psutil.Process(int(owner['pid']))
        return (process.create_time() == float(owner['started_at'])
                and process.status() != psutil.STATUS_ZOMBIE)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except (psutil.AccessDenied, KeyError, TypeError, ValueError):
        # Uncertain ownership is not evidence that work has stopped.
        return True


def reconcile(store, owner):
    """Close dead owners and pre-ownership web runs from before this boot.

    PID plus creation time protects against PID reuse. No age timeout: a long
    productive turn remains live for as long as its capture process does.
    """
    now = time.time()
    messages = []
    for run in store.recent_running_runs():
        metadata = run['metadata']
        capture_owner = metadata.get('capture_owner')
        abandoned = (not owner_alive(capture_owner) if capture_owner else
                     bool((metadata.get('user_context') or {}).get('web_ui'))
                     and run['started_at'] < owner['started_at'])
        if abandoned:
            messages.append(('run', run['query_id'], {
                **metadata, 'finished_at': now, 'success': False,
                'capture_interrupted': True,
                'error_message': 'Capture process exited before the turn was finalized; outcome unknown.',
            }))
    if messages:
        store.write_batch(messages)
    return [qid for _, qid, _ in messages]
