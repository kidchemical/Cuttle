# Frontend JavaScript

Vanilla scripts served under `/js/`; HTML declares dependency order. Files retain
their existing globals and module interfaces. No bundler or path aliases.

| Directory | Owner |
| --- | --- |
| `chat/` | Chat page orchestration, composer, messages, turn state, streaming, cards, widgets and VFX |
| `shell/` | App shell, launcher and desktop update policy |
| `spaces/` | Spaces state, groups, ordering, activity and drops |
| `git/` | Git graph, pending changes, commit/diff viewers and push reports |
| `settings/` | Settings tab controller and release UI |
| `projects/`, `router/`, `dashboards/`, `jobs/`, `mesh/` | Their corresponding page controllers |
| `queries/` | Query log inspector |
| `agent_events/` | Agent Feed: pure row/work models, diff rendering and the feed page |
| `achievements/` | Achievement UI and celebration presentation |
| `gizmos/` | Gizmos model, shell docks, pop-out page, Gizmos App and the composer Tasks gizmo |
| `tasks/`, `supervision/`, `orchestrator/` | Tasks, Supervision, Orchestrator and Agents Apps (pure model + page controller each) |
| `secret_vault/` | Secrets App: pure model and page controller |
| `media_library/` | Media App: pure model and page controller |
| `test_lab/` | Registered-project test suite discovery and run history |
| `ide/` | IDE App: pure model, Cuttle palette bridge, Monaco engine, explorer, search, quick input, terminal and AI chat panels |
| `media/` | Wallpaper, background effects, YouTube parsing and emoticons |
| `shared/` | Shared boot, auth, notifications, navigation, CDN loading and UI utilities |

Page and shell controllers compose their feature modules; moving a file does not
change ownership or permit a leaf module to reach into a controller's globals.
When adding or moving a script, update HTML loaders, Node relative imports, test
asset paths and documentation. Run the asset-reference, JS and browser tests in
`src/tests/`; see its README for isolation requirements.
