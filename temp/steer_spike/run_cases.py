"""Run one direct vendor turn, send at a detected stream boundary, log JSONL."""
import asyncio, json, sys, time
from pathlib import Path
from api.agent_harness import steer
from scripts.utilities.codex_app_server_turn import run_codex_turn_app_server
from scripts.utilities.muse_serve_turn import run_muse_turn_serve
from scripts.utilities.claude_cli_tool import ClaudeCliTool

agent, case = sys.argv[1:]
out = Path(__file__).with_name(f'{agent}-{case}.jsonl')
out.unlink(missing_ok=True)
session = 'isolated-spike-' + agent
loop = None
sent = False
def log(kind, data):
    with out.open('a') as f: f.write(json.dumps({'monotonic': time.monotonic(), 'kind': kind, 'data': data}, default=str) + '\n')
async def send():
    text = 'Please acknowledge this steer and include the word marigold in your response.'
    log('send', text)
    result = await asyncio.to_thread(steer.steer, session, text)
    log('send_result', result)
def observe(event):
    global sent
    log('event', event)
    method = event.get('method', '')
    item = event.get('params', {}).get('item', {})
    typ = event.get('type', '')
    tool = (method == 'item/started' and (item.get('type') or item.get('kind')) in ('commandExecution', 'toolCall')) or (typ == 'assistant' and 'tool_use' in json.dumps(event))
    text = ('agentMessage/delta' in method or (method == 'item/delta' and event.get('params', {}).get('field') == 'text') or typ == 'stream_event' and 'text_delta' in json.dumps(event))
    end = method == 'item/completed' and (item.get('type') or item.get('kind')) == 'agentMessage' and (item.get('phase') == 'final_answer' or item.get('messagePhase') == 'finalAnswer') or typ == 'result'
    if not sent and ((case == 'tool' and tool) or (case == 'text' and text) or (case == 'end' and end)):
        sent = True
        loop.create_task(send())
async def main():
    global loop
    loop = asyncio.get_running_loop()
    prompt = 'Run a shell command that waits 8 seconds and prints READY, then give a detailed explanation of three ways to organize a small Python project. Do not edit files.'
    kw = dict(cwd=str(Path.cwd()), chat_session_id=session, timeout=110, on_event=observe)
    if agent == 'codex': result = await run_codex_turn_app_server(prompt, resume=None, model='gpt-5.6-sol', reasoning_effort='low', **kw)
    elif agent == 'muse': result = await run_muse_turn_serve(prompt, resume=None, model='muse-spark-1.3-contributor', reasoning_effort='low', **kw)
    else: result = await ClaudeCliTool(model='haiku', reasoning_effort='low').execute_prompt(prompt, steerable=True, **kw)
    await asyncio.sleep(1)
    log('result', result)
    print(agent, case, 'triggered', sent, 'result', json.dumps(result, default=str)[:1200])
asyncio.run(main())
