/* ================================================================
   Cuttle Chat — slash command domain (chat_slash.js)
   Owner: slash-command subsystem. Command registry data, parsing,
   matching, sticky/starred decisions, project-command merging, native
   control detection, and chip classification: pure over explicit inputs
   (no document, no window, no localStorage, no fetch here). Loaded
   before chat_page.js; the chat page owns palette DOM/rendering, event
   wiring, settings/fetch IO, page state, and execution, and calls into
   `CuttleChatSlash.*`.

   Conventions:
   - `commands` params default to the module registries when omitted.
   - Resolvers the page owns (pipeline chips, model labels, category
     labels) are injected as callbacks, never read from chat globals.
   ================================================================ */
(function (root) {
    'use strict';

const SLASH_COMMANDS = [
    {
        prefix: '/claude ',
        label: 'Claude Code',
        hint: 'Run Claude Code on the project (Auto/Cloud)',
        category: 'command',
        requiresCloud: true,
        stickySession: true,
    },
    {
        prefix: '/hermes ',
        label: 'Hermes Agent',
        hint: 'Run Hermes Agent on the local llama.cpp model (Qwen3-Coder)',
        category: 'hermes',
        stickySession: true,
    },
    {
        prefix: '/cursor ',
        label: 'Cursor Agent',
        hint: 'Run Cursor `agent` CLI one-shot (Auto/Cloud)',
        category: 'cursor',
        requiresCloud: true,
        stickySession: true,
    },
    {
        prefix: '/codex ',
        label: 'Codex',
        hint: 'Run OpenAI Codex CLI (`codex exec`) with per-chat resume (Auto/Cloud)',
        category: 'codex',
        requiresCloud: true,
        stickySession: true,
    },
    {
        prefix: '/muse ',
        label: 'Muse Code',
        hint: 'Run Meta Muse Code CLI (`muse exec`) with per-chat resume (Auto/Cloud)',
        category: 'muse',
        requiresCloud: true,
        stickySession: true,
    },
    {
        prefix: '/opencode ',
        label: 'OpenCode',
        hint: 'Run OpenCode CLI (`opencode run`) with per-chat resume (Auto/Cloud)',
        category: 'opencode',
        requiresCloud: true,
        stickySession: true,
    },
    {
        prefix: '/antigravity ',
        label: 'Antigravity CLI',
        hint: 'Run Google Antigravity CLI (`agy`) with per-chat resume (Auto/Cloud)',
        category: 'command',
        requiresCloud: true,
        stickySession: true,
    },
    {
        prefix: '/deepseek ',
        label: 'DeepSeek Harness',
        hint: 'Run DeepSeek Harness CLI (`dsh --profile headless`; Flash by default)',
        category: 'command',
        requiresCloud: true,
        stickySession: true,
    },
    {
        prefix: '/coordinator ',
        label: 'Coordinator',
        hint: 'Supervised mode: status, mode, profile, worker, review-loops',
        category: 'command',
        requiresCloud: true,
        controlCommand: true,
        keywords: 'coordinator supervised diet-frontier mode profile worker',
    },
    {
        prefix: '/coordinate ',
        label: 'Coordinate task',
        hint: 'Run supervised task (Codex Sol low → Cursor Auto) or status/cancel',
        category: 'command',
        requiresCloud: true,
        controlCommand: true,
        keywords: 'coordinate supervised task followup cancel status',
    },
    {
        prefix: '/restart ',
        label: 'Restart Flask',
        hint: 'Cuttle control: status, graceful, when-idle, force --yes',
        category: 'command',
        controlCommand: true,
        keywords: 'restart flask daemon reload graceful when-idle force status',
    },
    { prefix: '/help', label: 'Help', hint: 'Show command help', category: 'command' },
];

const CURSOR_AGENT_SLASH_COMMANDS = [
    {
        prefix: '/model ',
        label: 'Model',
        hint: 'List, set, or refresh Cursor Agent models for this chat',
        category: 'cursor-cmd',
        keywords: 'cursor model llm opus sonnet composer refresh',
    },
    {
        prefix: '/plan ',
        label: 'Plan mode',
        hint: 'Switch Cursor Agent to Plan mode (read-only planning)',
        category: 'cursor-cmd',
        keywords: 'cursor plan mode',
    },
    {
        prefix: '/ask ',
        label: 'Ask mode',
        hint: 'Switch Cursor Agent to Ask mode (read-only Q&A)',
        category: 'cursor-cmd',
        keywords: 'cursor ask mode',
    },
    {
        prefix: '/agent',
        label: 'Agent mode',
        hint: 'Reset Cursor Agent to default agent mode',
        category: 'cursor-cmd',
        keywords: 'cursor agent mode default',
    },
    {
        prefix: '/clear',
        label: 'New Cursor chat',
        hint: 'Clear the Cursor Agent resume session for this chat',
        category: 'cursor-cmd',
        keywords: 'cursor clear new chat reset session',
    },
    {
        prefix: '/sandbox ',
        label: 'Sandbox',
        hint: 'Set sandbox enabled/disabled for Cursor Agent runs',
        category: 'cursor-cmd',
        keywords: 'cursor sandbox network',
    },
    {
        prefix: '/about',
        label: 'About',
        hint: 'Show Cursor Agent CLI version and account info',
        category: 'cursor-cmd',
        keywords: 'cursor about version whoami',
    },
    {
        prefix: '/usage-live',
        label: 'Live usage',
        hint: 'Usage refreshed every minute while visible (shared across panes)',
        category: 'cursor-cmd',
        keywords: 'cursor usage live quota billing',
    },
    {
        prefix: '/usage',
        label: 'Usage',
        hint: 'Show Cursor plan usage for the current billing cycle',
        category: 'cursor-cmd',
        keywords: 'cursor usage quota billing spend tokens plan',
    },
    {
        prefix: '/cost',
        label: 'Cost',
        hint: 'Per-model input/output token prices (active model first)',
        category: 'cursor-cmd',
        keywords: 'cursor cost pricing price rates tokens models dollars',
    },
    {
        prefix: '/compact',
        label: 'Compact',
        hint: 'Summarize the Cursor Agent session to free context window space',
        category: 'cursor-cmd',
        keywords: 'cursor compact summarize compress context window',
    },
];

    const HARNESS_USAGE_SLASH_BY_AGENT = {
        muse: {
            prefix: '/usage',
            label: 'Usage',
            hint: 'Show Muse Code account + local session usage',
            category: 'muse-cmd',
            keywords: 'muse usage quota billing spend tokens plan subscription',
        },
        codex: {
            prefix: '/usage',
            label: 'Usage',
            hint: 'Show ChatGPT Codex plan windows (5-hour / weekly)',
            category: 'codex-cmd',
            keywords: 'codex usage quota billing spend tokens plan chatgpt',
        },
        hermes: {
            prefix: '/usage',
            label: 'Usage',
            hint: 'Show Hermes local insights (tokens, tools, models)',
            category: 'hermes-cmd',
            keywords: 'hermes usage insights tokens tools models',
        },
        opencode: {
            prefix: '/usage',
            label: 'Usage',
            hint: 'Show OpenCode local stats (cost, tools, models)',
            category: 'opencode-cmd',
            keywords: 'opencode usage stats cost tokens tools models',
        },
    };

    /** Nested ``/cost`` (per-model token prices) for every non-Cursor harness. */
    const HARNESS_COST_AGENT_LABELS = {
        muse: 'Muse Code',
        codex: 'Codex',
        hermes: 'Hermes',
        opencode: 'OpenCode',
        claude: 'Claude Code',
        deepseek: 'DeepSeek',
        antigravity: 'Antigravity',
    };

    function harnessCostSlashCommand(agent) {
        const name = HARNESS_COST_AGENT_LABELS[agent] || agent;
        return {
            prefix: '/cost',
            label: 'Cost',
            hint: name + ' per-model input/output token prices (active model first)',
            category: agent + '-cmd',
            keywords: agent + ' cost pricing price rates tokens models dollars',
        };
    }

    /**
     * Overlay harness agents onto the static slash list: install hints when
     * the CLI is missing, and append drop-in harness agents not already
     * hardcoded. Pure over (base, agents).
     */
    function mergeHarnessAgentsIntoSlashCommands(base, agents) {
        const list = Array.isArray(agents) ? agents : [];
        if (!list.length) return base;
        const byPrefix = new Map();
        for (const a of list) {
            const prefix = String(a.prefix || ((a.slash || '') + ' ') || '').trimEnd();
            const norm = prefix.endsWith(' ') ? prefix : (prefix + ' ');
            if (norm.length > 1) byPrefix.set(norm.toLowerCase(), a);
        }
        const out = base.map((cmd) => {
            const key = String(cmd.prefix || '').toLowerCase();
            const a = byPrefix.get(key);
            if (!a) return cmd;
            const available = a.available !== false;
            const install = String(a.install_hint || '').trim();
            const hint = !available && install
                ? install
                : (String(a.hint || cmd.hint || '').trim() || cmd.hint);
            return {
                ...cmd,
                label: a.label || cmd.label,
                hint,
                requiresCloud: a.requires_cloud != null ? !!a.requires_cloud : cmd.requiresCloud,
                stickySession: a.stickySession != null ? !!a.stickySession : cmd.stickySession,
                harness: true,
                available,
                installHint: install,
            };
        });
        const existing = new Set(out.map((c) => String(c.prefix || '').toLowerCase()));
        for (const a of list) {
            const prefixRaw = String(a.prefix || ((a.slash || '') + ' ') || '').trim();
            if (!prefixRaw) continue;
            const prefix = prefixRaw.endsWith(' ') ? prefixRaw : (prefixRaw + ' ');
            if (existing.has(prefix.toLowerCase())) continue;
            const available = a.available !== false;
            const install = String(a.install_hint || '').trim();
            out.push({
                prefix,
                label: a.label || a.id || prefix.trim(),
                hint: (!available && install) ? install : (a.hint || install || 'Harness agent'),
                category: 'command',
                requiresCloud: a.requires_cloud !== false,
                stickySession: a.sticky !== false && a.stickySession !== false,
                harness: true,
                available,
                installHint: install,
            });
            existing.add(prefix.toLowerCase());
        }
        return out;
    }

    /** Registry for the current inference mode (local hides cloud-only). */
    function slashCommandsForCurrentMode(mode, harnessAgents) {
        const base = mode !== 'local'
            ? SLASH_COMMANDS.slice()
            : SLASH_COMMANDS.filter((c) => !c.requiresCloud);
        return mergeHarnessAgentsIntoSlashCommands(base, harnessAgents);
    }

    const TITLE_SLASH_SKIP = { help: 1, pipelines: 1, project: 1, cd: 1 };

    /**
     * Parse one stored slash head (`/cursor …`, `/model <id> …`,
     * `/pipeline <id> …`, `[Skill ref] …`, registry prefixes).
     * Pipeline/model resolvers are page-owned; inject them.
     */
    function parseStoredSlashCommandHead(s, deps) {
        const d = deps || {};
        const commands = d.commands || SLASH_COMMANDS;
        const cursorCommands = d.cursorCommands || CURSOR_AGENT_SLASH_COMMANDS;
        const str = String(s ?? '').trim();
        if (!str) return null;

        const pipeM = str.match(/^\/pipeline\s+(\S+)\s*(.*)$/s);
        if (pipeM) {
            const id = pipeM[1];
            const body = pipeM[2].trim();
            const chip = d.resolvePipelineChip
                ? d.resolvePipelineChip(id)
                : { label: id, meta: '/pipeline ' + id, category: 'pipeline' };
            return { chips: [chip], body };
        }

        // `/model <id> …` — consume the model id so it does not leak into the body
        // (and so we can collapse to a single Cursor badge).
        const modelSetM = str.match(/^\/model\s+(\S+)\s*(.*)$/s);
        if (modelSetM) {
            const mid = modelSetM[1];
            const body = (modelSetM[2] || '').trim();
            return {
                chips: [{
                    label: d.modelLabel ? d.modelLabel(mid) : mid,
                    meta: '/model ' + mid,
                    category: 'cursor-model',
                    modelId: mid,
                }],
                body,
            };
        }

        const cmds = commands.concat(cursorCommands)
            .slice()
            .sort((a, b) => b.prefix.length - a.prefix.length);
        for (const cmd of cmds) {
            const p = cmd.prefix;
            const cat = cmd.category || 'command';
            // `/model ` alone (list models) — handled here; `/model <id>` above.
            if (p === '/model ') {
                if (str === '/model') {
                    return { chips: [{ label: cmd.label, meta: '/model', category: cat }], body: '' };
                }
                continue;
            }
            if (p.endsWith(' ')) {
                const headTrim = p.replace(/\s+$/, '');
                if (str === headTrim) {
                    return { chips: [{ label: cmd.label, meta: headTrim, category: cat }], body: '' };
                }
                if (str.startsWith(p)) {
                    return {
                        chips: [{ label: cmd.label, meta: headTrim, category: cat }],
                        body: str.slice(p.length).trim(),
                    };
                }
            } else {
                if (str === p) {
                    return { chips: [{ label: cmd.label, meta: p, category: cat }], body: '' };
                }
                if (str.startsWith(p + ' ')) {
                    return {
                        chips: [{ label: cmd.label, meta: p, category: cat }],
                        body: str.slice(p.length + 1).trim(),
                    };
                }
            }
        }

        const skillM = str.match(/^\[Skill\s+([\w/-]+)\]\s*(.*)$/s);
        if (skillM) {
            const ref = skillM[1];
            const body = skillM[2].trim();
            const slug = ref.includes('/') ? ref.split('/').pop() : ref;
            const pretty = slug
                .split(/[-_]+/)
                .filter(Boolean)
                .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
                .join(' ');
            return {
                chips: [{ label: pretty || ref, meta: '[Skill ' + ref + ']', category: 'skill' }],
                body,
            };
        }
        return null;
    }

    /** Full message: first command(s), then optional freeform body. Supports multiple consecutive commands. */
    function parseStoredSlashCommandMessage(raw, deps) {
        const s = String(raw ?? '').trim();
        if (!s.startsWith('/') && !/^\[Skill\s/i.test(s)) return null;
        const chips = [];
        let rest = s;
        for (let i = 0; i < 24; i++) {
            const step = parseStoredSlashCommandHead(rest, deps);
            if (!step) {
                if (i === 0) return null;
                break;
            }
            chips.push(...step.chips);
            rest = step.body.trim();
            if (!rest) break;
            if (!rest.startsWith('/') && !/^\[Skill\s/i.test(rest)) break;
        }
        if (!chips.length) return null;
        return { chips, body: rest };
    }

    function parseProjectOrGenericSlashHead(rest, projectCmdItems) {
        const str = String(rest || '').trim();
        if (!str.startsWith('/')) return null;
        const items = Array.isArray(projectCmdItems) ? projectCmdItems : [];
        const sorted = items.slice().sort((a, b) => String(b.prefix || '').length - String(a.prefix || '').length);
        for (const item of sorted) {
            const p = String(item.prefix || '');
            if (!p) continue;
            const head = p.replace(/\s+$/, '');
            if (str === head || str.startsWith(p) || str.startsWith(head + ' ')) {
                const body = str === head
                    ? ''
                    : (str.startsWith(p) ? str.slice(p.length) : str.slice(head.length)).trim();
                return {
                    chips: [{
                        label: item.label || head.replace(/^\//, ''),
                        meta: item.meta || head,
                        category: 'project-cmd',
                    }],
                    body,
                };
            }
        }
        const cmdM = str.match(/^\/cmd\s+([A-Za-z][\w-]*)\s*(.*)$/s);
        if (cmdM) {
            const name = cmdM[1];
            if (TITLE_SLASH_SKIP[name.toLowerCase()]) {
                return { chips: [], body: (cmdM[2] || '').trim() };
            }
            return {
                chips: [{ label: name, meta: '/cmd ' + name, category: 'project-cmd' }],
                body: (cmdM[2] || '').trim(),
            };
        }
        const tokM = str.match(/^\/([A-Za-z][\w-]*)\s*(.*)$/s);
        if (!tokM) return null;
        const name = tokM[1];
        if (TITLE_SLASH_SKIP[name.toLowerCase()]) {
            return { chips: [], body: (tokM[2] || '').trim() };
        }
        return {
            chips: [{ label: name, meta: '/' + name, category: 'command' }],
            body: (tokM[2] || '').trim(),
        };
    }

    function parseTitleSlashChips(raw, deps) {
        const d = deps || {};
        const chips = [];
        let rest = String(raw ?? '').trim();
        if (!rest) return { chips, body: '' };
        for (let i = 0; i < 8; i++) {
            if (!rest.startsWith('/') && !/^\[Skill\s/i.test(rest)) break;
            const known = parseStoredSlashCommandHead(rest, d);
            const step = known && known.chips && known.chips.length
                ? known
                : parseProjectOrGenericSlashHead(rest, d.projectCmdItems);
            if (!step) break;
            if (step.chips && step.chips.length) {
                step.chips.forEach((c) => {
                    const key = titleChipKey(c).replace(/^\//, '').split(/\s+/)[0];
                    if (TITLE_SLASH_SKIP[key]) return;
                    chips.push(c);
                });
            }
            const next = String(step.body || '').trim();
            if (next === rest) break;
            rest = next;
        }
        return { chips, body: rest };
    }

    function normalizeSlashCommandStored(sc) {
        if (!sc) return null;
        if (Array.isArray(sc.chips) && sc.chips.length) return sc;
        if (sc.label && sc.meta) {
            return { chips: [{ label: sc.label, meta: sc.meta, category: sc.category || 'command' }] };
        }
        return null;
    }

    function slashCommandMetaFromUserMessage(userMessage, deps) {
        const p = parseStoredSlashCommandMessage(userMessage, deps);
        if (!p || !p.chips.length) return null;
        return { chips: p.chips };
    }

    function titleChipKey(c) {
        const src = String((c && (c.meta || c.prefix || c.label)) || '').toLowerCase().trim();
        const pipe = src.match(/^\/pipeline\s+(\S+)/);
        if (pipe) return '/pipeline ' + pipe[1];
        const cmd = src.match(/^\/cmd\s+([a-z][\w-]*)/);
        if (cmd) return '/' + cmd[1];
        const tok = src.match(/^\/([a-z][\w-]*)/);
        if (!tok) return src;
        const name = tok[1];
        if (name === 'cursor' || name === 'model' || name === 'plan' || name === 'ask' || name === 'agent') {
            return '/cursor';
        }
        return '/' + name;
    }

    function slashPaletteFilterTokens(filterLower) {
        return String(filterLower || '')
            .toLowerCase()
            .trim()
            .split(/\s+/)
            .filter(Boolean);
    }

    function slashPaletteCategoryLabel(cat) {
        if (cat === 'pipeline') return 'Pipeline';
        if (cat === 'skill') return 'Skill';
        if (cat === 'project') return 'Project';
        if (cat === 'project-cmd') return 'Project cmd';
        if (cat === 'cursor' || cat === 'cursor-model' || cat === 'cursor-cmd') return 'Cursor';
        if (cat === 'muse' || cat === 'muse-model' || cat === 'muse-effort' || cat === 'muse-cmd') return 'Muse';
        if (cat === 'hermes' || cat === 'hermes-model' || cat === 'hermes-effort' || cat === 'hermes-cmd') return 'Hermes';
        if (cat === 'opencode' || cat === 'opencode-model' || cat === 'opencode-effort' || cat === 'opencode-cmd') return 'OpenCode';
        if (cat === 'codex' || cat === 'codex-model' || cat === 'codex-effort' || cat === 'codex-cmd') return 'Codex';
        if (cat === 'claude-cmd') return 'Claude';
        if (cat === 'deepseek-cmd') return 'DeepSeek';
        if (cat === 'antigravity-cmd') return 'Antigravity';
        return 'Command';
    }

    /** Normalize palette categories into filter-badge buckets. */
    function slashPaletteTypeBucket(cat) {
        const c = cat || 'command';
        if (c === 'cursor-model' || c === 'cursor-cmd') return 'cursor';
        if (c === 'muse-model' || c === 'muse-effort' || c === 'muse-cmd') return 'muse';
        if (c === 'hermes-model' || c === 'hermes-effort' || c === 'hermes-cmd') return 'hermes';
        if (c === 'opencode-model' || c === 'opencode-effort' || c === 'opencode-cmd') return 'opencode';
        if (c === 'codex-model' || c === 'codex-effort' || c === 'codex-cmd') return 'codex';
        return c;
    }

    function slashPaletteTypeBadgeLabel(bucket) {
        if (bucket === 'all') return 'All';
        if (bucket === 'command') return 'Commands';
        if (bucket === 'project-cmd') return 'Project cmds';
        if (bucket === 'project') return 'Projects';
        if (bucket === 'pipeline') return 'Pipelines';
        if (bucket === 'skill') return 'Skills';
        if (bucket === 'cursor') return 'Cursor';
        if (bucket === 'muse') return 'Muse';
        if (bucket === 'hermes') return 'Hermes';
        if (bucket === 'opencode') return 'OpenCode';
        if (bucket === 'codex') return 'Codex';
        return slashPaletteCategoryLabel(bucket);
    }

    function slashPaletteItemHaystack(item) {
        return [
            ...slashPaletteIdentityFields(item),
            item.label,
            item.hint,
            item.meta,
            item.prefix ? String(item.prefix).replace(/^\//, '').trim() : '',
            item.pipelineId,
            item.skillRef,
            item.keywords,
            item.modelId,
            slashPaletteCategoryLabel(item.category),
        ]
            .filter(Boolean)
            .map((x) => String(x).toLowerCase())
            .join('\n');
    }

    /** Names/aliases are intent; hints, keywords and paths are discovery text. */
    function slashPaletteIdentityFields(item) {
        const aliases = Array.isArray(item.aliases) ? item.aliases : [item.aliases];
        return [item.prefix, item.label, item.name, ...aliases, item.projectCommandName,
            item.projectName, item.pipelineId, item.skillRef, item.modelId]
            .filter(Boolean)
            .map((value) => String(value).toLowerCase().replace(/^\//, '')
                .replace(/^★\s*/, '').replace(/\s+\(current\)$/, '').trim());
    }

    /** Lower ranks win. All query tokens must match, including subcommands. */
    function slashPaletteItemSearchRank(item, filter) {
        const tokens = slashPaletteFilterTokens(filter);
        if (!tokens.length) return 0;
        const query = tokens.join(' ');
        const fields = slashPaletteIdentityFields(item);
        if (fields.some((field) => field === query)) return 0;
        if (fields.some((field) => field.startsWith(query))) return 1;
        const words = fields.flatMap((field) => field.split(/[\s._/\-]+/));
        if (tokens.every((token) => words.includes(token))) return 2;
        if (tokens.every((token) => words.some((word) => word.startsWith(token)))) return 3;
        if (tokens.every((token) => fields.some((field) => field.includes(token)))) return 4;
        const compact = (value) => value.replace(/[\s._/\-]+/g, '');
        if (tokens.every((token) => compact(token)
            && fields.some((field) => compact(field).includes(compact(token))))) return 5;
        if (slashPaletteItemMatches(item, query)) return 6;
        return 7;
    }

    function slashPaletteItemMatches(item, filterLower) {
        if (!filterLower) return true;
        const tokens = slashPaletteFilterTokens(filterLower);
        if (!tokens.length) return true;
        const haystack = slashPaletteItemHaystack(item);
        // Compact form so "sonnet4" / "1m" still hit dashed ids and spaced labels.
        const compact = haystack.replace(/[\s._/-]+/g, '');
        return tokens.every((tok) => {
            if (haystack.includes(tok)) return true;
            const compactTok = tok.replace(/[\s._/-]+/g, '');
            return !!(compactTok && compact.includes(compactTok));
        });
    }

    /**
     * Sort key: starred sticky first, then the starred project. Inputs are
     * plain values (prefix list, project id/path) — prefs IO stays in page.
     */
    function starredRank(item, star) {
        const s = star || {};
        const prefixes = s.prefixes || [];
        if (item.stickySession && prefixes.indexOf(item.prefix) >= 0) return 0;
        if (item.category === 'project') {
            if (
                s.projectId != null
                && item.projectId != null
                && String(item.projectId) === String(s.projectId)
            ) {
                return 0;
            }
            if (s.projectPath && item.projectPath) {
                const norm = (p) => String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
                if (norm(item.projectPath) === norm(s.projectPath) && norm(s.projectPath)) {
                    return 0;
                }
            }
        }
        return 1;
    }

    function isSlashCommandStarred(prefixes, prefix) {
        return (prefixes || []).includes(prefix);
    }

    /** Starred sticky chips for welcome/new chats (local mode drops cloud). */
    function starredStickyChips(prefixes, mode, commands) {
        const list = prefixes || [];
        if (!list.length) return [];
        return list
            .map((prefix) => (commands || SLASH_COMMANDS)
                .find((c) => c.prefix === prefix && c.stickySession))
            .filter((c) => c && !(mode === 'local' && c.requiresCloud))
            .map((c) => ({
                prefix: c.prefix,
                label: c.label,
                category: c.category || 'command',
            }));
    }

    function getStickySlashCommandFromMessage(message, commands) {
        const m = String(message).trim();
        if (!m) return null;
        const candidates = (commands || SLASH_COMMANDS).filter((c) => c.stickySession).sort(
            (a, b) => b.prefix.length - a.prefix.length
        );
        const low = m.toLowerCase();
        for (const cmd of candidates) {
            const token = cmd.prefix.replace(/\s+$/, '');
            const tokenLow = token.toLowerCase();
            // Bare "/cursor", "/cursor …", or prefix-with-space "/cursor foo"
            // — same rules as api.starred_slash.sticky_prefix_from_text.
            if (
                low === tokenLow
                || low.startsWith(tokenLow + ' ')
                || m.startsWith(cmd.prefix)
            ) {
                return cmd;
            }
        }
        return null;
    }

    /** True when the API response indicates a failed sticky agent run (UI only). */
    function isStickySlashAssistantFailure(stickyCmd, data) {
        if (!stickyCmd || !data) return false;
        if (data.busy) return false;
        if (data.success === false) return true;
        const t = data.type;
        if (stickyCmd.prefix.startsWith('/opencode')) return t === 'opencode_error';
        if (stickyCmd.prefix.startsWith('/antigravity')) return t === 'antigravity_error';
        if (stickyCmd.prefix.startsWith('/hermes')) return t === 'hermes_error';
        if (stickyCmd.prefix.startsWith('/claude')) return t === 'claude_error' || t === 'error';
        if (stickyCmd.prefix.startsWith('/cursor')) return t === 'cursor_error' || t === 'error';
        if (stickyCmd.prefix.startsWith('/codex')) return t === 'codex_error' || t === 'error';
        if (stickyCmd.prefix.startsWith('/muse')) return t === 'muse_error' || t === 'error';
        if (stickyCmd.prefix.startsWith('/deepseek')) return t === 'deepseek_error' || t === 'error';
        return false;
    }

    /**
     * 'none' when this turn must skip the server-side sticky/starred agent.
     * Pure over explicit inputs (chip presence, cleared flag, known stars).
     */
    function stickyAgentOverrideForRequest(inputs) {
        const in_ = inputs || {};
        if (getStickySlashCommandFromMessage(in_.message)) return null;
        if (in_.hasChip) return null;
        if (in_.cleared) return 'none';
        // A star we know about with no badge on the composer means the user took
        // the badge off. An unknown star (prefs still hydrating) stays silent so
        // the server fallback can cover a genuinely dropped chip.
        return (in_.starredPrefixes || []).length ? 'none' : null;
    }

    /** Recover sticky agent from recent user turns when prefs were never saved / wiped. */
    function inferStickyChipsFromUserMessages(messages) {
        if (!Array.isArray(messages)) return [];
        for (let i = messages.length - 1; i >= 0; i--) {
            const m = messages[i];
            if (!m || m.role !== 'user') continue;
            let meta = m.metadata;
            if (typeof meta === 'string') {
                try { meta = JSON.parse(meta); } catch (_) { meta = null; }
            }
            if (meta && String(meta.speaker_kind || '') === 'parent') continue;
            const cmd = getStickySlashCommandFromMessage(m.content || '');
            if (cmd) {
                return [{
                    prefix: cmd.prefix,
                    label: cmd.label,
                    category: cmd.category || 'command',
                }];
            }
        }
        for (let i = messages.length - 1; i >= 0; i--) {
            const m = messages[i];
            if (!m || m.role !== 'assistant') continue;
            let meta = m.metadata;
            if (typeof meta === 'string') {
                try { meta = JSON.parse(meta); } catch (_) { meta = null; }
            }
            const sc = (meta && meta.slash_command) || m.slash_command;
            const chips = stickyChipsFromAssistantSlash(sc);
            if (chips.length) return chips;
        }
        return [];
    }

    function stickyChipsFromAssistantSlash(sc) {
        if (!sc || typeof sc !== 'object') return [];
        const chips = Array.isArray(sc.chips) ? sc.chips : [];
        if (!chips.length) return [];
        const c = chips[0] || {};
        const cat = String(c.category || '').toLowerCase();
        const meta = String(c.meta || '');
        let prefix = '';
        if (meta.startsWith('/')) prefix = meta.split(/[\s·]/)[0];
        if (!prefix && cat) prefix = '/' + cat.split('-')[0];
        if (!prefix || prefix === '/') return [];
        return [{
            prefix: prefix,
            label: String(c.label || prefix).trim() || prefix,
            category: cat || 'command',
        }];
    }

    /** Any composer (chat or welcome) currently holds a sticky agent badge. */
    function hasStickyAgentChip(chatChips, welcomeChips) {
        const lists = [chatChips || [], welcomeChips || []];
        for (const chips of lists) {
            for (const c of chips) {
                const match = SLASH_COMMANDS.find((s) => s.prefix === c.prefix);
                if (match && match.stickySession) return true;
            }
        }
        return false;
    }

    /** First sticky agent chip across chat then welcome composers. */
    function activeStickyAgentChip(chatChips, welcomeChips) {
        const lists = [chatChips || [], welcomeChips || []];
        for (const chips of lists) {
            for (const c of chips) {
                if (!c) continue;
                if (
                    isStickyAgentChip(c, 'cursor')
                    || isStickyAgentChip(c, 'muse')
                    || isStickyAgentChip(c, 'hermes')
                    || isStickyAgentChip(c, 'opencode')
                    || isStickyAgentChip(c, 'codex')
                ) {
                    return c;
                }
                const match = SLASH_COMMANDS.find((s) => s.prefix === c.prefix);
                if (match && match.stickySession) return c;
            }
        }
        return null;
    }

    function isNativeControlCommand(text) {
        const t = String(text || '').trim().toLowerCase();
        if (!t.startsWith('/')) return false;
        return SLASH_COMMANDS.some((c) => {
            if (!c.controlCommand) return false;
            const token = c.prefix.trim().toLowerCase();
            return t === token || t.startsWith(token + ' ');
        });
    }

    function buildProjectPaletteItems(projects) {
        return (projects || []).map((p) => {
            const name = String(p.name || '').trim() || ('Project ' + p.id);
            const path = String(p.path || '').trim();
            return {
                category: 'project',
                prefix: '/project ' + name + ' ',
                label: name,
                hint: path || 'Set this project as the chat working directory',
                meta: path,
                keywords: 'project cd directory cwd folder ' + name + ' ' + path,
                projectId: p.id,
                projectPath: path,
                projectName: name,
            };
        });
    }

    function buildProjectCommandPaletteItems(commands) {
        return (commands || []).map((c) => {
            const name = String(c.name || '').trim();
            if (!name) return null;
            const title = String(c.title || name).trim() || name;
            const desc = String(c.description || '').trim();
            const collision = !!c.reserved_collision;
            const prefix = collision ? ('/cmd ' + name + ' ') : ('/' + name + ' ');
            return {
                category: 'project-cmd',
                prefix,
                label: title,
                hint: desc || ('Run project command /' + name),
                meta: collision ? ('/cmd ' + name) : ('/' + name),
                keywords: [
                    'project command cmd',
                    name,
                    title,
                    desc,
                    (c.aliases || []).join(' '),
                ].join(' '),
                projectCommandName: name,
                aliases: c.aliases || [],
            };
        }).filter(Boolean);
    }

    /**
     * Sticky agent badge only (``/cursor``, ``/codex``, …). Nested cmds like
     * ``/usage`` or ``/codex model refresh`` must not match — a naive
     * ``startsWith('/codex')`` / ``startsWith(token + ' ')`` used to
     * relabel those chips as the agent badge (CH-000482 / CH-000482-1).
     * Legacy chips with ``category:'cursor'`` + ``prefix:'/usage'`` also
     * must not win.
     */
    function isStickyAgentChip(chip, agentId) {
        if (!chip || !agentId) return false;
        const id = String(agentId).toLowerCase();
        const cat = String((chip && chip.category) || '').toLowerCase();
        if (cat === id + '-cmd' || cat === id + '-model' || cat === id + '-effort') {
            return false;
        }
        const hay = String(chip.prefix || chip.meta || '')
            .toLowerCase()
            .trim();
        // Nested one-shots — never the sticky badge (incl. legacy wrong category).
        if (
            /^\/(usage-live|usage|cost)(\s|$)/.test(hay)
            || /^\/model\s+refresh\b/.test(hay)
            || /^\/(about|clear|plan|ask|sandbox|agent)\b/.test(hay)
            || new RegExp('^/' + id + '\\s+/').test(hay)
            || new RegExp(
                '^/' + id + '\\s+(model|effort|usage|cost|about|clear|plan|ask|sandbox|agent)\\b'
            ).test(hay)
        ) {
            return false;
        }
        if (cat === id) return true;
        if (!hay) return false;
        // Exact token, optional trailing space, or history meta `/codex · …`.
        const m = hay.match(new RegExp('^/' + id + '(?:\\s+(.*))?$'));
        if (!m) return false;
        const rest = String(m[1] || '').trim();
        if (!rest) return true;
        return /^(·|•)/.test(rest);
    }

    function isStickyCursorAgentChip(chip) {
        return isStickyAgentChip(chip, 'cursor');
    }
    function isStickyMuseAgentChip(chip) {
        return isStickyAgentChip(chip, 'muse');
    }
    function isStickyCodexAgentChip(chip) {
        return isStickyAgentChip(chip, 'codex');
    }
    function isStickyHermesAgentChip(chip) {
        return isStickyAgentChip(chip, 'hermes');
    }
    function isStickyOpenCodeAgentChip(chip) {
        return isStickyAgentChip(chip, 'opencode');
    }

    /** Nested Cursor Agent slash (``/usage``, ``/plan``, ``/model refresh``, …). */
    function isCursorNestedCommandChip(chip) {
        if (!chip) return false;
        if ((chip.category || '') === 'cursor-cmd') return true;
        // Real model *setting* picks stay companion chips, not nested one-shots.
        if ((chip.category || '') === 'cursor-model') return false;
        const pre = String(chip.prefix || chip.meta || '').toLowerCase().trim();
        if (pre === '/model' || /^\/model\s+refresh\b/.test(pre)) return true;
        if (/^\/model\b/.test(pre)) return false;
        return /^\/(usage|cost|about|clear|plan|ask|sandbox|agent)\b/.test(pre)
            && !isStickyCursorAgentChip(chip);
    }

    /** Nested harness cmd chip (``/usage``, ``/cost``, staged ``/model refresh``, …). */
    function isHarnessNestedCommandChip(chip) {
        if (!chip) return false;
        const cat = String(chip.category || '').toLowerCase();
        if (/^(cursor|muse|codex|hermes|opencode|claude|deepseek|antigravity)-cmd$/.test(cat)) return true;
        const pre = String(chip.prefix || chip.meta || '').toLowerCase().trim();
        return /^\/(usage-live|usage|cost)(\s|$)/.test(pre)
            || /^\/model\s+refresh\b/.test(pre);
    }

    const AGENT_CHIP_RE = /(?:^|[^\w])\/(cursor|muse|hermes|codex|opencode|claude|deepseek|antigravity)(?:\s|$)/i;
    const AGENT_LABEL_RE = /^(cursor|muse|hermes|codex|opencode|claude|deepseek|antigravity)\b/i;

    function isAgentHeaderChip(chip) {
        const cat = String((chip && chip.category) || '').toLowerCase();
        // Nested agent cmds (/usage, /cost, /plan, …) are command chips — not the agent badge.
        if (/^(cursor|muse|codex|hermes|opencode|claude|deepseek|antigravity)-cmd$/.test(cat)) return false;
        if (isHarnessNestedCommandChip(chip) && !isStickyAgentChip(chip, 'cursor')
            && !isStickyAgentChip(chip, 'muse') && !isStickyAgentChip(chip, 'codex')
            && !isStickyAgentChip(chip, 'hermes') && !isStickyAgentChip(chip, 'opencode')) {
            return false;
        }
        if (
            cat === 'cursor' || cat === 'cursor-model'
            || cat === 'muse' || cat === 'muse-model' || cat === 'muse-effort'
            || cat === 'codex' || cat === 'codex-model' || cat === 'codex-effort'
            || cat === 'hermes' || cat === 'hermes-model' || cat === 'hermes-effort'
            || cat === 'opencode' || cat === 'opencode-model' || cat === 'opencode-effort'
            || cat === 'agent'
        ) {
            // Model/effort companions are not the primary agent header chip.
            if (cat.endsWith('-model') || cat.endsWith('-effort')) return false;
            return true;
        }
        if (isStickyCursorAgentChip(chip) || isStickyMuseAgentChip(chip)
            || isStickyCodexAgentChip(chip) || isStickyHermesAgentChip(chip)
            || isStickyOpenCodeAgentChip(chip)) {
            return true;
        }
        const hay = String((chip && (chip.prefix || chip.meta || chip.label)) || '');
        if (/^\/(usage|cost|about|clear|plan|ask|sandbox|model)\b/i.test(hay.trim())) {
            return false;
        }
        // Only sticky-shaped agent tokens count — not `/codex model …` / `/codex /usage`.
        const pre = hay.trim().toLowerCase();
        const stickyOnly = pre.match(
            /^\/(cursor|muse|hermes|codex|opencode|claude|deepseek|antigravity)(?:\s|$)/i
        );
        if (stickyOnly) {
            const agentTok = stickyOnly[1].toLowerCase();
            return isStickyAgentChip({ prefix: pre, category: '' }, agentTok);
        }
        return AGENT_LABEL_RE.test(hay.trim()) && !/^usage\b/i.test(hay.trim());
    }

    /** Agent id owning a composer chip, or '' for agent-less chips. Pure. */
    function composerChipAgentId(chip) {
        const cat = String((chip && chip.category) || '').toLowerCase();
        const byCat = {
            'cursor': 'cursor', 'cursor-model': 'cursor', 'cursor-cmd': 'cursor',
            'muse': 'muse', 'muse-model': 'muse', 'muse-effort': 'muse', 'muse-cmd': 'muse',
            'hermes': 'hermes', 'hermes-model': 'hermes', 'hermes-effort': 'hermes', 'hermes-cmd': 'hermes',
            'opencode': 'opencode', 'opencode-model': 'opencode', 'opencode-effort': 'opencode', 'opencode-cmd': 'opencode',
            'codex': 'codex', 'codex-model': 'codex', 'codex-effort': 'codex', 'codex-cmd': 'codex',
            'claude': 'claude', 'claude-cmd': 'claude',
            'deepseek': 'deepseek', 'deepseek-cmd': 'deepseek',
            'antigravity': 'antigravity', 'antigravity-cmd': 'antigravity',
        };
        if (byCat[cat]) return byCat[cat];
        // History title chips often store meta `/codex · model …` with no prefix
        // (CH-000497-8). Prefer prefix, then meta — same haystack as sticky match.
        const pre = String((chip && (chip.prefix || chip.meta)) || '')
            .trim()
            .toLowerCase();
        const m = pre.match(/^\/(cursor-cli|cursor|muse|hermes|codex|opencode|claude|deepseek|antigravity)\b/);
        if (m) return m[1] === 'cursor-cli' ? 'cursor' : m[1];
        // Cursor-only nested cmds (plan/ask/…) typed after the agent chip.
        if (/^\/(model|plan|ask|about|clear|sandbox|agent)\b/.test(pre)) return 'cursor';
        // Label-only fallback (title-parsed "codex" / "Codex" with empty meta).
        const lab = String((chip && chip.label) || '').trim().toLowerCase();
        const labM = lab.match(
            /^(cursor(?:\s+agent)?|muse(?:\s+code)?|hermes(?:\s+agent)?|codex|opencode|claude(?:\s+code)?|deepseek(?:\s+harness)?|antigravity(?:\s+cli)?)\b/
        );
        if (labM) {
            const tok = labM[1].split(/\s+/)[0];
            return tok === 'cursor' || tok === 'muse' || tok === 'hermes'
                || tok === 'codex' || tok === 'opencode'
                || tok === 'claude' || tok === 'deepseek' || tok === 'antigravity'
                ? tok
                : '';
        }
        // Bare /usage: do not assume Cursor — category (*-cmd) should already
        // have mapped above. Leaving '' avoids bundling Codex Usage with Cursor.
        return '';
    }

    /**
     * Indexes to delete when the × on chips[idx] is clicked. A merged agent
     * badge (agent + its model/effort/subcommand chips) removes as a whole
     * so no residual subcommand chip is left behind. Pure.
     */
    function composerChipRemovalIndexes(chips, idx) {
        const list = Array.isArray(chips) ? chips : [];
        if (!Number.isFinite(idx) || idx < 0 || idx >= list.length) return [];
        const agent = composerChipAgentId(list[idx]);
        if (!agent) return [idx];
        const out = [];
        for (let n = 0; n < list.length; n++) {
            if (composerChipAgentId(list[n]) === agent) out.push(n);
        }
        return out.length ? out : [idx];
    }

    function isCursorRelatedSlashChip(c) {
        if (!c) return false;
        const cat = String(c.category || '');
        const meta = String(c.meta || c.prefix || '');
        const lab = String(c.label || '');
        // Nested cmds stay as their own chips on history (do not collapse into
        // the Cursor agent badge).
        if (cat === 'cursor-cmd' || isCursorNestedCommandChip(c)) return false;
        if (cat === 'cursor' || cat === 'cursor-model') return true;
        if (meta.toLowerCase().startsWith('/cursor')) return true;
        if (/^\/model\s+refresh\b/i.test(meta)) return false;
        if (/^\/model\b/i.test(meta)) return true;
        if (/^cursor(\s|-)/i.test(lab) || /^cursor$/i.test(lab.trim())) return true;
        return false;
    }

    /** Stable partition: agent header chips first, order otherwise kept. */
    function sortChipsAgentThenCommand(chips) {
        const agent = [];
        const command = [];
        (chips || []).forEach((c) => {
            if (isAgentHeaderChip(c)) agent.push(c);
            else command.push(c);
        });
        return agent.concat(command);
    }

    const api = {
        SLASH_COMMANDS,
        CURSOR_AGENT_SLASH_COMMANDS,
        HARNESS_USAGE_SLASH_BY_AGENT,
        HARNESS_COST_AGENT_LABELS,
        TITLE_SLASH_SKIP,
        harnessCostSlashCommand,
        mergeHarnessAgentsIntoSlashCommands,
        slashCommandsForCurrentMode,
        parseStoredSlashCommandHead,
        parseStoredSlashCommandMessage,
        parseProjectOrGenericSlashHead,
        parseTitleSlashChips,
        normalizeSlashCommandStored,
        slashCommandMetaFromUserMessage,
        titleChipKey,
        slashPaletteFilterTokens,
        slashPaletteCategoryLabel,
        slashPaletteTypeBucket,
        slashPaletteTypeBadgeLabel,
        slashPaletteItemHaystack,
        slashPaletteItemMatches,
        slashPaletteItemSearchRank,
        starredRank,
        isSlashCommandStarred,
        starredStickyChips,
        getStickySlashCommandFromMessage,
        isStickySlashAssistantFailure,
        stickyAgentOverrideForRequest,
        inferStickyChipsFromUserMessages,
        stickyChipsFromAssistantSlash,
        hasStickyAgentChip,
        activeStickyAgentChip,
        isNativeControlCommand,
        buildProjectPaletteItems,
        buildProjectCommandPaletteItems,
        isStickyAgentChip,
        isStickyCursorAgentChip,
        isStickyMuseAgentChip,
        isStickyCodexAgentChip,
        isStickyHermesAgentChip,
        isStickyOpenCodeAgentChip,
        isCursorNestedCommandChip,
        isHarnessNestedCommandChip,
        isAgentHeaderChip,
        isCursorRelatedSlashChip,
        sortChipsAgentThenCommand,
        composerChipAgentId,
        composerChipRemovalIndexes,
    };

    const ns = (root.CuttleChatSlash = root.CuttleChatSlash || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
