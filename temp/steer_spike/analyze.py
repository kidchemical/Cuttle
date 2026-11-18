"""Summarize steer echoes and the next completed agent message for manual judgment."""
import json, sys
from pathlib import Path
rows = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
for index, row in enumerate(rows):
    if row['kind'] != 'send':
        continue
    text = row['data']
    echo = None
    for n in range(index + 1, len(rows)):
        event = rows[n].get('data', {})
        if rows[n]['kind'] != 'event' or not isinstance(event, dict): continue
        params = event.get('params', {})
        item = params.get('item', {})
        if item.get('type') == 'userMessage' and text in json.dumps(item):
            echo = (n, rows[n], params, item); break
    if not echo:
        print(f'{text!r}: no echo'); continue
    n, observed, params, item = echo
    print(f'{text!r}: echo {observed["monotonic"] - row["monotonic"]:.3f}s, turn={params.get("turnId")}, user_item={item.get("id")}')
    for later in rows[n+1:]:
        event = later.get('data', {})
        if later['kind'] != 'event' or not isinstance(event, dict): continue
        item = event.get('params', {}).get('item', {})
        if event.get('method') == 'item/completed' and item.get('type') == 'agentMessage':
            print(f'  next agent item={item.get("id")}: {item.get("text")!r}'); break
    else: print('  no subsequent completed agent message')
