"""Local trusted-agent CLI; JSON output, nonzero failures."""
import argparse
import json
import sqlite3
from api import chat_vfx

def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m api.chat_vfx')
    subs = parser.add_subparsers(dest='verb', required=True)
    for verb in ('confetti', 'toast', 'pending'):
        p = subs.add_parser(verb)
        p.add_argument('--session', required=True)
        if verb == 'confetti':
            p.add_argument('--count', type=int, default=120)
        elif verb == 'toast':
            p.add_argument('message')
            p.add_argument('--variant', default='info')
        else:
            p.add_argument('--after', type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.verb == 'pending':
            result = {'events': chat_vfx.pending(args.session, args.after)}
        else:
            eid = (chat_vfx.spawn_confetti(args.session, args.count) if args.verb == 'confetti'
                   else chat_vfx.toast(args.session, args.message, args.variant))
            result = {'event_id': eid, 'status': 'queued'}
        print(json.dumps({'success': True, **result}))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(json.dumps({'success': False, 'error': str(error)}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
