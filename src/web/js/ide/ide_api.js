/* IDE App — HTTP transport over /api/ide (owner api.ide.routes) plus the
 * existing Git pending-changes and experimental-flag reads it composes.
 * Every call resolves to the parsed JSON body or throws an Error carrying
 * `status`, `conflict` and `current` from the server. */
(function (root) {
    'use strict';

    function create(deps) {
        const fetchImpl = (deps && deps.fetch) || ((url, opts) => root.fetch(url, opts));

        async function call(method, url, body, opts) {
            const init = { method: method, credentials: 'include', cache: 'no-store', signal: opts && opts.signal };
            if (body !== undefined) {
                init.headers = { 'Content-Type': 'application/json' };
                init.body = JSON.stringify(body);
            }
            const response = await fetchImpl(url, init);
            const data = await response.json().catch(() => null);
            if (!response.ok || !data || data.success === false) {
                const err = new Error((data && data.error) || ('HTTP ' + response.status));
                err.status = response.status;
                err.conflict = !!(data && data.conflict);
                err.current = data && data.current;
                err.disabled = !!(data && data.disabled);
                throw err;
            }
            return data;
        }
        const qs = (params) => new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== '')).toString();
        const get = (path, params, opts) => call('GET', '/api/ide/' + path + '?' + qs(params), undefined, opts);
        const post = (path, body, opts) => call('POST', '/api/ide/' + path, body, opts);

        return {
            workspaces: () => get('workspaces', {}),
            tree: (rootPath, dir) => get('tree', { root: rootPath, dir: dir || '' }),
            files: (rootPath) => get('files', { root: rootPath }),
            read: (rootPath, file) => get('file', { root: rootPath, file: file }).then((d) => d.file),
            write: (rootPath, file, content, o) => call('PUT', '/api/ide/file', Object.assign({ root: rootPath, file: file, content: content }, o || {})).then((d) => d.file),
            stat: (rootPath, files) => post('stat', { root: rootPath, files: files }).then((d) => d.files),
            createEntry: (rootPath, path, kind) => post('entry', { root: rootPath, path: path, kind: kind }),
            rename: (rootPath, from, to) => post('rename', { root: rootPath, from: from, to: to }),
            remove: (rootPath, path) => post('delete', { root: rootPath, path: path }),
            search: (rootPath, o, opts) => post('search', Object.assign({ root: rootPath }, o), opts),
            replace: (rootPath, o) => post('replace', Object.assign({ root: rootPath }, o)),
            original: (rootPath, file) => get('original', { root: rootPath, file: file }).then((d) => d.original),
            complete: (rootPath, o, opts) => post('complete', Object.assign({ root: rootPath }, o), opts),
            rawUrl: (rootPath, file) => '/api/ide/raw?' + qs({ root: rootPath, file: file }),
            pending: (rootPath, opts) => call('GET', '/api/git/pending-changes?' + qs({ path: rootPath }), undefined, opts),
            reveal: (absPath) => call('POST', '/api/fs/reveal', { path: absPath }),
            openDefault: (absPath) => call('POST', '/api/fs/open', { path: absPath }),
            flagEnabled: async (id) => {
                const data = await call('GET', '/api/experimental/flags');
                return !data.kill_switch && (data.flags || []).some((f) => f.id === id && f.enabled);
            },
        };
    }

    root.CuttleIdeApi = { create: create };
    if (typeof module !== 'undefined' && module.exports) module.exports = root.CuttleIdeApi;
})(globalThis);
