# Smart Optimizer UI v2.2.3

## Updates page refresh-loop hotfix

v2.2.3 fixes a browser refresh loop that could appear after a successful in-app update.

The updater correctly switched Smart Optimizer to the new application release, but the Updates page kept seeing the persistent `updated` status and redirected itself back to `/updates?refresh=1` every polling cycle.

The page now reloads only when the running application version differs from the version that originally rendered the page. That gives exactly one reconnect after an update:

- old page sees the new running version
- browser reloads once
- new page is rendered by the new version
- polling continues without further reloads

No optimizer, RULES, connection, authentication, or local-state behavior changed.
