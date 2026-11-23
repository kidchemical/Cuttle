/* ================================================================
   Cuttle Chat — execution node display decisions (pure).
   Owner of what the header chip says, which options the node dialog
   lists and what a Save sends. No DOM, no fetch: the controller is
   chat_exec_node.js; the data is GET /api/workers/exec/chats/<id>.
   ================================================================ */
(function (root) {
    'use strict';

    const STATE_TEXT = { online: 'online', busy: 'busy', unavailable: 'unavailable' };

    /** "KCsLaptop — Windows" (the OS is left out when unknown). */
    function nodeName(node) {
        const label = String((node && node.label) || (node && node.id) || '');
        return node && node.os ? label + ' — ' + node.os : label;
    }

    /** One line on a node's state: "online", "busy: 1 agent turn running", "unavailable: offline". */
    function stateLine(node) {
        const state = STATE_TEXT[node && node.state] || 'unavailable';
        return node && node.reason ? state + ': ' + node.reason : state;
    }

    /** The header chip marks a chat bound to a remote node. */
    function chip(binding) {
        if (!binding || !binding.node || binding.node.host) return null;
        const node = binding.node;
        const down = node.state === 'unavailable';
        const busy = node.state === 'busy';
        return {
            text: String(node.label || node.id),
            compact: down ? '⚠ Node' : busy ? 'Node · busy' : 'Node',
            warn: down,
            busy,
            title: 'Agent runs on ' + nodeName(node) + ' (' + stateLine(node) + '). Click to change.',
        };
    }

    /** Select rows: the host first, every node selectable (an unavailable one says why). */
    function options(nodes, binding) {
        const current = binding ? String(binding.node_id || 'host') : 'host';
        const rows = (nodes || []).map((node) => ({
            value: String(node.id),
            label: node.host ? nodeName(node) + ' · this Cuttle host' : nodeName(node) + ' · ' + stateLine(node),
            selected: String(node.id) === current,
        }));
        // A bound node that is no longer paired still shows, so the chat's state is honest.
        if (binding && binding.node && !rows.some((r) => r.selected)) {
            rows.push({ value: current, label: nodeName(binding.node) + ' · ' + stateLine(binding.node), selected: true });
        }
        return rows;
    }

    /** Text under the form for the picked node. */
    function hint(node, working) {
        const parts = [];
        if (node && !node.host) {
            if (node.state === 'unavailable') parts.push('This machine is unavailable (' + (node.reason || 'offline') + '): turns will not run until it is back.');
            else if (node.state === 'busy') parts.push('Busy (' + node.reason + '): a new turn waits for a free slot.');
            parts.push('The agent uses that machine’s files, tools and CLI login. The chat and its history stay here.');
        } else {
            parts.push('The agent runs on this Cuttle host.');
        }
        if (working) parts.push('A turn is running: the change applies from the next one.');
        return parts.join(' ');
    }

    /** Placeholder for the working-directory field of a node. */
    function cwdPlaceholder(node) {
        if (!node || node.host) return '';
        return node.default_cwd ? 'Default: mapped project, else ' + node.default_cwd : 'e.g. C:\\Users\\me\\Dev\\project';
    }

    /** What Save does: nothing, or a PUT body. */
    function plan(binding, form) {
        const nodeId = String((form && form.nodeId) || 'host');
        const cwd = nodeId === 'host' ? '' : String((form && form.cwd) || '').trim();
        const was = binding || {};
        if (String(was.node_id || 'host') === nodeId && String(was.cwd || '') === cwd) return { kind: 'none' };
        return { kind: 'save', body: { node_id: nodeId, cwd } };
    }

    const api = { chip, options, hint, cwdPlaceholder, plan, nodeName, stateLine };
    root.CuttleChatExecNodeModel = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
