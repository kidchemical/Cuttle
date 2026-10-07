/* Pure fleet row updates and filters, shared by history and live delivery. */
(function(root) {
    'use strict';
    function matches(row, filters) {
        return ['agent','model','kind','project','chat','search'].every(function(key) {
            var value = String(filters[key] || '').toLowerCase();
            if (!value) return true;
            if (key==='search' && row.search_match) return true;
            if (key==='kind' && value==='failure') return !!row.failed;
            var candidate = {agent:row.agent_id, model:row.model, kind:row.kind, project:row.project_root,
                chat:'CH-' + String(row.chat_session_id || '').padStart(6,'0'), search:row.summary}[key];
            return String(candidate || '').toLowerCase().includes(value);
        });
    }
    function merge(rows, incoming) {
        var map = new Map(rows.map(function(row) {return [row.id,row];}));
        incoming.forEach(function(row) {
            var old = map.get(row.id);
            if (!old || Number(row.rev) >= Number(old.rev)) map.set(row.id,row);
        });
        return Array.from(map.values()).sort(function(a,b) {return b.ts-a.ts || b.id-a.id;});
    }
    root.CuttleAgentFeed = {matches:matches,merge:merge};
})(typeof window === 'undefined' ? globalThis : window);
