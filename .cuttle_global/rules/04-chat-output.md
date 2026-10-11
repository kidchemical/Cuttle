# Cuttle chat output

When writing replies rendered in Cuttle chat, use standard Markdown for prose
and citations (ordinary links for sources and local files), and documented Cuttle
tags for interactive UI. Do not emit agent CLI or vendor UI-specific citation
markers, directives, follow-up chips, or control markup; Cuttle cannot render
those native components. Suggested next steps use plain Markdown; interactive
choices and follow-ups use `cuttle_action_form` → `action-forms.md`.

This governs reply formatting, not native tool calls used to perform the work.
