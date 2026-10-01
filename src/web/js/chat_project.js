/* ================================================================
   Cuttle Chat — project context domain (chat_project.js)
   Owner: project-context subsystem. Pure project resolution, mapping,
   and outbound decisions: no document, no window, no localStorage, no
   fetch here (all inputs are explicit arguments). Loaded before
   chat_page.js; the chat page owns the singletons (`projects`,
   `currentProject`, `currentSessionId`), session/prefs/server IO, DOM,
   persistence writes, and orchestration, and calls into
   `CuttleChatProject.*`.

   State shape threaded through (never stored here):
     project = { id, name, path }
     stored fields = { projectId, projectName, projectPath }
     starred = { id, path, name } | null
   ================================================================ */
(function (root) {
    'use strict';

    /** Normalize for path comparison (backslashes, trailing slash, case). */
    function normalizeProjectPath(path) {
        return String(path || '')
            .trim()
            .replace(/\\/g, '/')
            .replace(/\/+$/, '')
            .toLowerCase();
    }

    function findProjectById(projects, id) {
        if (id === null || id === undefined) return null;
        return ((projects || []).find((p) => String(p && p.id) === String(id)) || null);
    }

    function findProjectByName(projects, name) {
        if (!name) return null;
        const want = String(name).toLowerCase();
        return ((projects || []).find(
            (p) => String((p && p.name) || '').toLowerCase() === want
        ) || null);
    }

    function findProjectByPath(projects, path) {
        const want = normalizeProjectPath(path);
        if (!want) return null;
        return ((projects || []).find(
            (p) => normalizeProjectPath(p && p.path) === want
        ) || null);
    }

    /**
     * Resolve an {id, path, name} triple against the registry (the
     * new-chat-from-history fallback chain). Returns a project object,
     * inventing a lightweight one when nothing matches.
     */
    function findProject(projects, ref) {
        const r = ref || {};
        let proj = (r.id != null) ? findProjectById(projects, r.id) : null;
        if (!proj && r.path) {
            proj = (projects || []).find((p) => String((p && p.path) || '') === String(r.path)) || null;
        }
        if (!proj && r.name) {
            proj = findProjectByName(projects, r.name);
        }
        if (!proj) {
            proj = { id: r.id != null ? r.id : null, name: r.name || 'Project', path: r.path || '' };
        }
        return proj;
    }

    /**
     * Default project for new chats: starred pick wins (by id, then path,
     * then name), else a Cuttle-named project, else the first registry entry.
     */
    function findDefaultProject(projects, starred) {
        const list = projects || [];
        if (starred) {
            if (starred.id != null) {
                const byId = findProjectById(list, starred.id);
                if (byId) return byId;
            }
            const want = normalizeProjectPath(starred.path);
            if (want) {
                const byPath = list.find((p) => normalizeProjectPath(p && p.path) === want);
                if (byPath) return byPath;
            }
            if (starred.name) {
                const byName = findProjectByName(list, starred.name);
                if (byName) return byName;
            }
        }
        const byName = list.find(
            (p) => /cuttle/i.test(String((p && p.name) || '')) || /cuttle/i.test(String((p && p.path) || ''))
        );
        return byName || list[0] || null;
    }

    /** Normalize snake_case / camelCase server session rows to stored fields. */
    function projectFieldsFromServerSession(serverSession) {
        if (!serverSession || typeof serverSession !== 'object') return null;
        const id = serverSession.project_id != null
            ? serverSession.project_id
            : (serverSession.projectId != null ? serverSession.projectId : null);
        const name = serverSession.project_name || serverSession.projectName || '';
        const path = serverSession.project_path || serverSession.projectPath || '';
        if (id == null && !name && !path) return null;
        return { projectId: id, projectName: name, projectPath: path };
    }

    /**
     * Resolve stored fields to a project: registry by id, then by name,
     * else a lightweight object preserving id/name/path.
     */
    function projectObjectFromStoredFields(fields, projects) {
        if (!fields) return null;
        if (fields.projectId != null) {
            const byId = findProjectById(projects, fields.projectId);
            if (byId) {
                return { id: byId.id, name: byId.name, path: byId.path || '' };
            }
        }
        if (fields.projectName) {
            const byName = findProjectByName(projects, fields.projectName);
            if (byName) {
                return { id: byName.id, name: byName.name, path: byName.path || '' };
            }
        }
        if (fields.projectPath || fields.projectName || fields.projectId != null) {
            return {
                id: fields.projectId != null ? fields.projectId : null,
                name: fields.projectName || 'Project',
                path: fields.projectPath || '',
            };
        }
        return null;
    }

    /**
     * Match an arbitrary filesystem path to the registry (longest prefix
     * wins, either direction); falls back to a lightweight object named
     * for the last path segment.
     */
    function projectFromPath(path, projects) {
        const raw = String(path || '').trim();
        if (!raw) return null;
        const norm = normalizeProjectPath(raw);
        let best = null;
        let bestLen = -1;
        (projects || []).forEach((p) => {
            const pp = String((p && p.path) || '').trim();
            if (!pp) return;
            const have = normalizeProjectPath(pp);
            if (norm === have || norm.startsWith(have + '/') || have.startsWith(norm + '/')) {
                if (have.length > bestLen) {
                    best = p;
                    bestLen = have.length;
                }
            }
        });
        if (best) {
            return { id: best.id, name: best.name || 'Project', path: best.path || raw };
        }
        const parts = raw.split(/[\\/]/).filter(Boolean);
        return { id: null, name: parts[parts.length - 1] || raw, path: raw };
    }

    /**
     * Project carried by a message's metadata/options: explicit id/name/
     * path (registry-enriched), else harness cwd, else slash-chip cwd
     * annotations, else the live project for live turns.
     */
    function projectFromMessageOpts(opts, projects, currentProject) {
        const o = opts || {};
        const id = o.project_id != null ? o.project_id : o.projectId;
        const name = o.project_name || o.projectName || '';
        const path = o.project_path || o.projectPath || '';
        if (id != null || name || path) {
            const byId = id != null ? findProjectById(projects, id) : null;
            return {
                id: id != null ? id : (byId && byId.id),
                name: name || (byId && byId.name) || (path ? projectFromPath(path, projects).name : 'Project'),
                path: path || (byId && byId.path) || '',
            };
        }
        const cwd = o.cursor_run && o.cursor_run.cwd;
        if (cwd) return projectFromPath(cwd, projects);
        const chips = o.slash_command && o.slash_command.chips;
        if (Array.isArray(chips)) {
            for (let i = 0; i < chips.length; i++) {
                const meta = String((chips[i] && chips[i].meta) || '');
                const m = meta.match(/\bcwd\s+(\S+)/i);
                if (m && m[1]) return projectFromPath(m[1], projects);
            }
        }
        if (o.live && currentProject) return currentProject;
        return null;
    }

    /**
     * Pure core of reconcileChatProject: priority chain over explicit
     * inputs (server fields → merged prefs → stored entry → keep an
     * unsaved pick → registry backfills → default). Returns
     * { proj, hadStored }. Persistence/rendering decisions stay in the
     * chat-page orchestrator.
     */
    function resolveChatProject(inputs) {
        const in_ = inputs || {};
        const projects = in_.projects || [];
        const currentSessionId = in_.currentSessionId;
        const currentProject = in_.currentProject || null;
        let proj = null;
        let hadStored = false;

        if (currentSessionId) {
            proj = projectObjectFromStoredFields(in_.serverFields || null, projects);
            if (proj) hadStored = true;

            const prefs = in_.prefs || null;
            if (!proj && prefs && prefs.projectId !== undefined && prefs.projectId !== null) {
                proj = findProjectById(projects, prefs.projectId);
                hadStored = true;
            }
            if (!proj && prefs && prefs.projectPath) {
                proj = {
                    id: prefs.projectId,
                    name: prefs.projectName || 'Project',
                    path: prefs.projectPath,
                };
                hadStored = true;
            }
            if (!proj && prefs && prefs.projectName) {
                const byName = findProjectByName(projects, prefs.projectName);
                if (byName) {
                    proj = byName;
                    hadStored = true;
                } else {
                    proj = {
                        id: prefs.projectId,
                        name: prefs.projectName,
                        path: prefs.projectPath || '',
                    };
                    hadStored = true;
                }
            }
            const stored = in_.stored || null;
            if (!proj && stored) {
                if (stored.projectId !== undefined && stored.projectId !== null) {
                    proj = findProjectById(projects, stored.projectId);
                    hadStored = true;
                }
                if (!proj && stored.projectPath) {
                    proj = {
                        id: stored.projectId,
                        name: stored.projectName || 'Project',
                        path: stored.projectPath,
                    };
                    hadStored = true;
                }
                if (!proj && stored.projectName) {
                    const byName = findProjectByName(projects, stored.projectName);
                    proj = byName || {
                        id: stored.projectId,
                        name: stored.projectName,
                        path: stored.projectPath || '',
                    };
                    hadStored = true;
                }
            }
        }
        // Unsaved new chat: keep the already-picked project (no session
        // row exists yet, so falling through would snap back to default).
        if (!proj && !currentSessionId && currentProject) {
            proj = currentProject;
        }
        // Backfill path from the live registry when holding only id/name.
        if (proj && !proj.path && proj.id != null) {
            const byId = findProjectById(projects, proj.id);
            if (byId && byId.path) proj = { ...proj, path: byId.path, name: proj.name || byId.name };
        }
        if (proj && !proj.path && proj.name) {
            const byName = findProjectByName(projects, proj.name);
            if (byName && byName.path) {
                proj = { ...proj, id: proj.id != null ? proj.id : byName.id, path: byName.path };
            }
        }
        if (!proj) proj = findDefaultProject(projects, in_.starred || null);
        return { proj: proj || null, hadStored };
    }

    /**
     * Rebind a project to the live registry (repairs stale name/path
     * pairs kept in storage). Returns { proj, changed }.
     */
    function rebindProjectToRegistry(proj, projects) {
        if (!proj) return { proj, changed: false };
        if (proj.id != null) {
            const byId = findProjectById(projects, proj.id);
            if (byId && byId.path) {
                const fixed = {
                    id: byId.id,
                    name: byId.name || proj.name,
                    path: byId.path,
                };
                const changed = !proj.path || String(proj.path) !== String(byId.path)
                    || proj.name !== fixed.name;
                return { proj: fixed, changed };
            }
        }
        if (proj.name) {
            const byName = findProjectByName(projects, proj.name);
            if (byName && byName.path) {
                return {
                    proj: {
                        id: proj.id != null ? proj.id : byName.id,
                        name: byName.name || proj.name,
                        path: byName.path,
                    },
                    changed: true,
                };
            }
        }
        return { proj, changed: false };
    }

    /** Stamp the outbound project onto a chat request body. */
    function applyOutboundProjectToRequest(requestBody, proj) {
        if (!requestBody || !proj) return;
        if (proj.id != null) requestBody.project_id = proj.id;
        if (proj.name) requestBody.project_name = proj.name;
        if (proj.path) requestBody.project_path = proj.path;
    }

    function currentProjectPathKey(proj) {
        return proj && proj.path ? String(proj.path) : '';
    }

    const api = {
        normalizeProjectPath,
        findProjectById,
        findProjectByName,
        findProjectByPath,
        findProject,
        findDefaultProject,
        projectFieldsFromServerSession,
        projectObjectFromStoredFields,
        projectFromPath,
        projectFromMessageOpts,
        resolveChatProject,
        rebindProjectToRegistry,
        applyOutboundProjectToRequest,
        currentProjectPathKey,
    };

    const ns = (root.CuttleChatProject = root.CuttleChatProject || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
