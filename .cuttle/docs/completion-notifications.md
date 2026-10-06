# Completion notifications (experimental)

Enable **Settings → Experimental → Completion notifications**, then open
**Settings → General → Notifications → Enable completion notifications** on each
browser/device. The second click requests browser permission. Both opt-ins and
the existing Notifications master switch must be enabled. Permission denial is
reported with a pointer to browser site settings; no completion prompts for
permission. Chat titles/job labels are hidden unless the device privacy checkbox
is checked. Reply content is never included.

## Supported delivery

This browser implementation notifies for replies lasting at least one minute,
started in this browser, and observed mesh batch completion. Notification clicks
focus Cuttle and open the originating chat; normal authentication still applies.
Failed and cancelled events use explicit terminal outcomes. Frame completion is
not described as a playable video. Existing chirps, toasts, and unread markers
continue through their existing owners.

The page must remain open and processing events. Starts persist across reloads;
old completed history cannot trigger a new reply notification. Watches notify
only on a freshly observed terminal transition with a stable run/batch identity.
Closing or navigating away from a watched card can end observation. Work started
on another device is not tracked by this slice. Mobile browsers commonly require
service-worker notifications, and suspended pages cannot reliably poll. Full
mobile background push is a separate future delivery service, not implemented
here. See [MDN Notifications API](https://developer.mozilla.org/en-US/docs/Web/API/Notifications_API).
LAN HTTP pages may not offer notification permission: use a trusted HTTPS origin
or the browser's supported local origin. An unsupported OS notification API
never interrupts chat delivery.

## Owners and state

- `src/web/js/shared/completion_notifications.js` owns permission/settings presentation,
  the pure completion plan, and one broker per shell with injected delivery/clock/
  storage capabilities. Panes share it; standalone chat pages create their own.
- `chat_page.js` reports turn starts, server-id adoption, Stop, and the existing
  `notifyAssistantResponseReady` completion seam.
- `chat_action_cards.js` reports new watch terminal transitions through optional
  `host.notifyWorkCompletion`. Saved terminal cards do not trigger delivery.
- `api.experimental.features` owns the default-off `completion_notifications` flag.
  The broker checks the authenticated flag API at delivery and fails closed.
- Browser-local `cuttle.completion.*` keys store preferences, active turn ids/times,
  and the last 256 delivered ids. They contain no transcript text, titles, or labels.
  Web Locks serialize delivery across tabs when available; a per-broker queue and
  stable OS notification tags bound duplicate delivery in other clients.

No new route, database, polling loop, Electron path, service worker, or external
push subscription is added. Notifications after page shutdown would require a
server-owned authenticated event store, subscription revocation, push transport,
and a service worker/mobile client that handles safe deep links. This is deferred
until that client scope is requested.

Tests: `src/tests/shell/test_completion_notifications.py` (fake storage/OS delivery) and
`src/tests/e2e/test_completion_notifications.py` (production settings, click-only
permission, real reply/watch hooks, click destination, and reload suppression).

Teardown: remove the registry row, module/script includes, General settings row,
page reporting seams, and optional card host callback. Existing notification
history and chirps have no dependency on this feature.
