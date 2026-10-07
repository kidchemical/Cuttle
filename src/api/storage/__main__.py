import argparse
import json
import sys
from .service import list_stores,set_policy
from api.agent_events.service import operate


def main():
    parser=argparse.ArgumentParser(description='Cuttle database settings and maintenance')
    parser.add_argument('verb',choices=['list','stats','set','prune','vacuum','reset'])
    parser.add_argument('store',nargs='?',default='agent_events')
    parser.add_argument('--policy',help='JSON policy fields to update')
    parser.add_argument('--confirm')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args()
    try:
        if args.verb in ('list','stats'):
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
