# Cuttle safety core (non-severable)

These two rules protect shared infrastructure and other projects. They compile
for every chat even when a project's `GLOBAL.ini` severs other global layers.
To change them for everyone, edit this tree (`.cuttle_global/rules/`) — a
per-project `GLOBAL.ini` cannot switch them off, and a project `00-safety.md`
can only *append* (never replace them).

1. Never force-kill Flask, `cuttle_daemon`, or the Discord bot from an agent those
   processes host (on Windows that means no `taskkill` / `Stop-Process` on them) →
   `action-forms.md` (Flask restart) or `/restart …`. Killing the host breaks
   delivery for every chat, not just yours.
2. Writes outside the active project root — especially another project's
   `.cuttle/` — require a confirm form first (`cuttle_confirm`). Compare the
   target path against the `active project root` in your context envelope; when
   unsure, use the equivalent of `cwd.same_project()`. This is etiquette with a
   paper trail, not a sandbox gate.
