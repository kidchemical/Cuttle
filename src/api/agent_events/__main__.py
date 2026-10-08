"""Agent operations for durable activity; no model execution."""
import argparse
import json
import sys
from .store import EventStore,state_dir


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('verb',choices=['stats','get','search','diff','changes','tail'])
    parser.add_argument('value',nargs='?')
    parser.add_argument('--after',type=int,default=0)
    parser.add_argument('--limit',type=int,default=100)
    parser.add_argument('--json',action='store_true')
    parser.add_argument('--once',action='store_true',help='Read one tail batch and exit')
    parser.add_argument('--agent')
    parser.add_argument('--kind')
    args=parser.parse_args()
    try:
        store=EventStore(state_dir())
        if args.verb=='tail':
            import time
            cursor=args.after
            while True:
                payload=store.changes(cursor,args.limit)
                cursor=payload['cursor']
                for row in payload['events']:
                    if args.agent and row.get('agent_id')!=args.agent:continue
                    if args.kind and row.get('kind') not in args.kind.split(','):continue
                    print(json.dumps(store.event(row['id']),ensure_ascii=False,default=str),flush=True)
                if args.once:return 0
                time.sleep(1)
        if args.verb=='stats':
            out=store.stats()
        elif args.verb=='get':
            out={'run':store.run(args.value),'events':store.events(args.value,after=args.after,limit=args.limit,full=True)}
        elif args.verb=='changes':
            from .reconcile import reconcile
            out=reconcile(store.run_edits(args.value),(store.run(args.value) or {}).get('agent_id'))
        elif args.verb=='diff':
            out=store.events(args.value,kind='edit',after=args.after,limit=args.limit,full=True)
        else:
            out=store.events(search=args.value,after=args.after,limit=args.limit,full=True)
        print(json.dumps(out,ensure_ascii=False,default=str));return 0
    except Exception as exc:
        print(json.dumps({'error':str(exc)}));return 1


if __name__=='__main__':
    sys.exit(main())
