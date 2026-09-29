# Sibling projects — install-local delta

Tracked guidance is generic; this file adds the install's project map.
When a turn is clearly about another codebase, read that project's own
`.cuttle/` first and verify the chat is rooted there (`project_path`).

| Clue in the prompt | Project root | Load first |
|---|---|---|
| Escape Purgatory, Farmstead, Dark Forest, maze pig, EP Discord | `/path/to/Escape-Purgatory\` | that project's `.cuttle/rules/`, `.cuttle/docs/discord.md` |
| Epochs, tribes, civilization, primitive survival, space colonization | `E:\Game Dev\Epochs\` | that project's `.cuttle/rules/01-game-design.md`, `.cuttle/docs/game-overview.md` |
| Take 27, friendslop, short movies, filmmaking, FPS, chaos | `E:\Game Dev\Take-27\` | that project's `.cuttle/rules/01-game-design.md`, `.cuttle/docs/game-overview.md` |

Do not invent Cuttle-only answers for sibling-project work, and do not leave
the user thinking Cuttle rules alone govern it. Add rows here when new sibling
projects are dogfooded through Cuttle.
