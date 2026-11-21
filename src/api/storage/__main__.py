import argparse
import json
import sys
from .service import list_stores,set_policy,operate


def main():
    parser=argparse.ArgumentParser(description='Cuttle database settings and maintenance')
    parser.add_argument('verb',choices=['list','stats','set','prune','vacuum','reset','location'])
    parser.add_argument('store',nargs='?',default='agent_events')
    change = parser.add_mutually_exclusive_group()
    change.add_argument('--destination', help='Schedule a whole-home move on the next cold start')
    change.add_argument('--cancel', action='store_true', help='Cancel a scheduled move')
    parser.add_argument('--policy',help='JSON policy fields to update')
    parser.add_argument('--confirm')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args()
    try:
        if args.verb != 'location' and (args.cancel or args.destination is not None):
            parser.error('--destination and --cancel apply to location')
        if args.verb == 'location':
            from core.home_relocation import configure, location
            if args.cancel:
                out = configure({'cancel': True})
            elif args.destination is not None:
                out = configure({'destination': args.destination})
            else:
                out = location()
        elif args.verb in ('list','stats'):
            out=list_stores()
        elif args.verb=='set':
            out=set_policy(args.store,json.loads(args.policy or '{}'))
        else:
            out=operate(args.store,args.verb,args.confirm)
        print(json.dumps(out));return 0
    except Exception as exc:
        print(json.dumps({'error':str(exc)}));return 1


if __name__=='__main__':
    sys.exit(main())
