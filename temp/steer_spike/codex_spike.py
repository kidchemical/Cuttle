"""Direct runner experiment; no Cuttle service or user chat involved."""
import asyncio, json, time
from pathlib import Path
from api.agent_harness import steer
from scripts.utilities.codex_app_server_turn import run_codex_turn_app_server

out = Path(__file__).with_name('codex.jsonl')
def record(kind, data):
    with out.open('a') as f:
        f.write(json.dumps({'monotonic': time.monotonic(), 'kind': kind, 'data': data}) + '\n')
async def main():
    out.unlink(missing_ok=True)
    task = asyncio.create_task(run_codex_turn_app_server(
        'Inspect the repository: list files, read three source files in separate shell commands, then summarize each briefly. Do not change files.',
        cwd=str(Path.cwd()), resume=None, model='gpt-5.6-sol', reasoning_effort='low',
        chat_session_id='steer-spike-isolated', timeout=120, on_event=lambda e: record('event', e)))
    for delay, msg in [(10, 'Briefly acknowledge this request: mention the color amber.'), (1, 'Also mention the word cobalt.'), (15, 'Please mention the word jade in your summary.')]:
        await asyncio.sleep(delay)
        record('send', msg)
        result = await asyncio.to_thread(steer.steer, 'steer-spike-isolated', msg)
        record('send_result', result)
    result = await task
    record('result', result)
    print(json.dumps(result, default=str))
asyncio.run(main())
