#!/usr/bin/env python3
"""Preserve client work while updating a clean tracking checkout (no lifecycle).

Two update channels:

* ``--source official`` (default) — fetch the configured upstream from the git
  remote and fast-forward. Unpushed Host commits never appear here.
* ``--source host`` — fast-forward from a Host-served ``git bundle`` (already
  downloaded by the caller). The Client must be on the Host's branch; a
  mismatch is refused instead of silently merging across branches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request


class UpdateRefused(RuntimeError):
    pass


# The Host substitutes its pinned TLS SPKI here when it delivers this helper, so
# the Client can reach the Host over HTTPS even when its own config has no pin
# (the pin is public — the Host's cert fingerprint — and travels over the
# already-trusted job channel). Empty on the checkout copy; set by submit.
EMBEDDED_HOST_SPKI = ""


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, timeout=180)
    if result.returncode:
        raise UpdateRefused(f'git {args[0]} failed: {result.stderr.decode(errors="replace").strip()}')
    return result.stdout


def require_clean(repo: Path) -> None:
    if git(repo, 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=none'):
        raise UpdateRefused('checkout has local changes or untracked files; preserve/commit them before updating')


def _ignored_collision_guard(repo: Path, target: bytes) -> None:
    """Refuse when incoming tracked paths would overwrite ignored local files."""
    incoming = set(git(repo, 'ls-tree', '-r', '-z', '--name-only', target).split(b'\0')) - {b''}
    ignored = set(git(repo, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').split(b'\0')) - {b''}
    # Byte paths support spaces/newlines/non-UTF8 names. Check file/directory
    # conflicts too (new tracked file "personal" vs ignored "personal/x").
    incoming_ancestors = set(incoming)
    for path in incoming:
        parts = path.split(b'/')
        incoming_ancestors.update(b'/'.join(parts[:n]) for n in range(1, len(parts)))
    for local in ignored:
        ancestors = {local}
        parts = local.split(b'/')
        ancestors.update(b'/'.join(parts[:n]) for n in range(1, len(parts)))
        if ancestors & incoming or local in incoming_ancestors:
            raise UpdateRefused('incoming tracked paths would overwrite ignored local files; preserve them before updating')


def _fast_forward(repo: Path, target: bytes) -> None:
    try:
        git(repo, 'merge-base', '--is-ancestor', 'HEAD', target)
    except UpdateRefused:
        raise UpdateRefused('local commits or diverged upstream; reconcile before updating') from None
    require_clean(repo)  # Work created during fetch must be refused too.
    _ignored_collision_guard(repo, target)
    git(repo, 'merge', '--ff-only', '--no-overwrite-ignore', target)


def _read_desktop_config() -> dict:
    """The Client's Electron desktop-config (host + pinned TLS identity).

    Electron sets ``userData`` to ``~/.config/cuttle-desktop-{client,host}`` on
    every platform (electron/main.js), while the daemon/sidecar use
    ``%LOCALAPPDATA%\\cuttle-desktop``. Check both so the paired host, httpsPort
    and TLS pins are found wherever the app wrote them.
    """
    home = Path.home()
    xdg = os.environ.get('XDG_CONFIG_HOME') or str(home / '.config')
    cands = []
    for base in (os.environ.get('LOCALAPPDATA'), os.environ.get('APPDATA')):
        if base:
            cands.append(Path(base) / 'cuttle-desktop' / 'desktop-config.json')
    for name in ('cuttle-desktop-client', 'cuttle-desktop-host', 'cuttle-desktop'):
        cands.append(Path(xdg) / name / 'desktop-config.json')
    primary = {}
    merged_pins = {}
    for path in cands:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding='utf-8')) or {}
            if not isinstance(data, dict):
                continue
            pins = data.get('tlsPins')
            if isinstance(pins, dict):
                merged_pins.update(pins)
            if not str(primary.get('host') or '').strip() and str(data.get('host') or '').strip():
                primary = data
            elif not primary and data:
                primary = data
        except Exception:
            continue
    out = dict(primary)
    if merged_pins:
        out['tlsPins'] = {**merged_pins, **(out.get('tlsPins') or {})}
    return out


def _download_host_bundle(repo: Path, host_base: str, spki: str) -> 'tuple[str, str]':
    """Fetch the Host's self-contained git bundle over pinned HTTPS.

    Returns ``(bundle_path, branch)``. Uses the Cuttle client TLS identity
    (api.tls_cert) so the artifact comes only from the pinned Host.
    """
    sys.path.insert(0, str(Path(repo).resolve() / 'src'))
    from api.tls_cert import urlopen as tls_urlopen  # noqa: WPS433 (optional dep)

    def _get(path: str) -> bytes:
        req = urllib.request.Request(host_base.rstrip('/') + path)
        with tls_urlopen(req, timeout=300, spki_pin=(spki or None)) as resp:
            return resp.read() or b''

    manifest = json.loads(_get('/api/desktop/git').decode('utf-8', 'replace') or '{}')
    if not manifest.get('artifact'):
        raise UpdateRefused(
            'host has no git bundle to serve'
            + (f" ({manifest.get('reason')})" if manifest.get('reason') else '')
        )
    data = _get('/api/desktop/git/bundle')
    expected = str(manifest.get('artifactSha256') or '').lower()
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise UpdateRefused('host bundle failed checksum verification')
    dest = Path(tempfile.gettempdir()) / f'cuttle-host-{os.getpid()}.bundle'
    dest.write_bytes(data)
    return str(dest), str(manifest.get('branch') or '')


def update_checkout(
    repo: Path,
    *,
    check_only: bool = False,
    source: str = 'official',
    bundle: str = '',
    branch: str = '',
    host: str = '',
    spki: str = '',
) -> None:
    require_clean(repo)
    git(repo, 'symbolic-ref', '-q', 'HEAD')  # Detached checkouts are never updated.

    if source == 'host':
        if check_only:
            return  # Lifecycle recheck only; the bundle was already merged.
        want = (branch or '').strip()
        bundle_path = bundle
        if not bundle_path:
            cfg = _read_desktop_config()
            host_name = (host or str(cfg.get('host') or '')).strip()
            if not host_name:
                raise UpdateRefused('host source: no paired host in desktop-config.json')
            port = int(cfg.get('httpsPort') or 0) or 0
            base = f'https://{host_name}:{port}' if port else f'https://{host_name}'
            pin = spki or EMBEDDED_HOST_SPKI
            if not pin:
                try:
                    from api.tls_cert import desktop_pin
                    pin = desktop_pin(cfg.get('tlsPins') or {}, host_name, port) or ''
                except Exception:
                    pin = ''
            bundle_path, manifest_branch = _download_host_bundle(repo, base, pin)
            want = want or manifest_branch
        if not bundle_path or not Path(bundle_path).is_file():
            raise UpdateRefused('host source requires a bundle (host download failed)')
        cur = git(repo, 'symbolic-ref', '--short', '-q', 'HEAD').decode().strip()
        if want and cur != want:
            raise UpdateRefused(
                f'wrong branch: on {cur!r}, host serves {want!r}; '
                f'switch to {want} to follow this host'
            )
        print(f'git fetch {bundle_path}', flush=True)
        git(repo, 'fetch', str(bundle_path), want or 'HEAD')
        target = git(repo, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}').decode().strip().encode()
        _fast_forward(repo, target)
        return

    upstream = git(repo, 'rev-parse', '--abbrev-ref', '@{u}').decode().strip()
    if not check_only:
        print('git fetch --all --prune', flush=True)
        git(repo, 'fetch', '--all', '--prune')
    # Pin the fetched target so a concurrent fetch cannot change the merge.
    upstream = git(repo, 'rev-parse', '--verify', '@{u}^{commit}').decode().strip()
    try:
        git(repo, 'merge-base', '--is-ancestor', 'HEAD', upstream)
    except UpdateRefused:
        raise UpdateRefused('local commits or diverged upstream; reconcile before updating') from None
    require_clean(repo)  # Work created during fetch must be refused too.
    _ignored_collision_guard(repo, upstream)
    if check_only:
        return
    print(f'git merge --ff-only --no-overwrite-ignore {upstream}', flush=True)
    git(repo, 'merge', '--ff-only', '--no-overwrite-ignore', upstream)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true', help='Recheck preservation before lifecycle; do not fetch or merge')
    parser.add_argument('--source', default='official', choices=['official', 'host'])
    parser.add_argument('--bundle', default='', help='Host-served git bundle path (source=host)')
    parser.add_argument('--branch', default='', help='Host branch to fast-forward to (source=host)')
    parser.add_argument('--host', default='', help='Override paired Host name (source=host)')
    parser.add_argument('--spki', default='', help='Override pinned TLS SPKI (source=host)')
    args = parser.parse_args()
    try:
        update_checkout(
            args.repo,
            check_only=args.check_only,
            source=args.source,
            bundle=args.bundle,
            branch=args.branch,
            host=args.host,
            spki=args.spki,
        )
    except (UpdateRefused, OSError, subprocess.TimeoutExpired) as exc:
        print(f'ERROR update refused: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
