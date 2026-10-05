"""Agent project operations backed by the same registry as the Projects App."""
import argparse
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect and configure Cuttle projects on this host.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    get = sub.add_parser('get'); get.add_argument('id', type=int)
    register = sub.add_parser('register')
    register.add_argument('--name', required=True); register.add_argument('--path', required=True)
    register.add_argument('--description', default=''); register.add_argument('--repo-url', default='')
    update = sub.add_parser('update'); update.add_argument('id', type=int)
    update.add_argument('--json', required=True, help='Object with name, description, tags, paths, repo_url, or archived.')
    check = sub.add_parser('check'); check.add_argument('paths', nargs='+')
    remove = sub.add_parser('remove'); remove.add_argument('id', type=int)
    remove.add_argument('--confirm-name', required=True)
    args = parser.parse_args(argv)
    from managers.project_manager import project_manager as pm
    from managers.project_locations import check_paths, validate_paths
    try:
        if args.command == 'list': result = pm.get_projects(include_archived=True)
        elif args.command == 'get':
            result = pm.get_project(args.id)
            if result is None: raise ValueError('Project not found.')
        elif args.command == 'check': result = check_paths(validate_paths(args.paths))
        elif args.command == 'register': result = {'id':pm.register_project(args.name, args.path, args.description, repo_url=args.repo_url)}
        elif args.command == 'update':
            values = json.loads(args.json)
            if not isinstance(values, dict): raise ValueError('Expected a settings object.')
            if not pm.update_project(args.id, **values): raise ValueError('Project not found.')
            result = pm.get_project(args.id)
        else:
            project = pm.get_project(args.id)
            if not project or project['name'] != args.confirm_name: raise ValueError('Exact project name required.')
            result = {'removed':pm.delete_project(args.id)}
        print(json.dumps({'success':True, 'data':result}, indent=2))
        return 0
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({'success':False, 'error':str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
