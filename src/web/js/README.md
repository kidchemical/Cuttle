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
| `projects/`, `router/`, `terminal/`, `dashboards/` | Their corresponding page controllers |
| `queries/` | Query log inspector |
| `achievements/` | Achievement UI and celebration presentation |
| `media/` | Wallpaper, background effects, YouTube parsing and emoticons |
| `shared/` | Shared boot, auth, notifications, navigation, CDN loading and UI utilities |

Page and shell controllers compose their feature modules; moving a file does not
change ownership or permit a leaf module to reach into a controller's globals.
When adding or moving a script, update HTML loaders, Node relative imports, test
asset paths and documentation. Run the asset-reference, JS and browser tests in
`src/tests/`; see its README for isolation requirements.
