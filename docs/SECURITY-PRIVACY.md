# Security and privacy

## Feed and HTTP handling

RetroNews handles HTTP(S) without embedded credentials, application cookies, authentication or environment proxies. It checks robots rules, uses cancellation, per-socket timeouts and body/redirect deadlines, and bounds redirects with urllib's limits. DNS resolution remains subject to the system resolver. Partial responses, unsupported HTTP statuses, compressed transfer bodies and HTTPS-to-HTTP redirects are rejected. Cross-origin redirects drop conditional cache validators. The default sync budget is 16 MB.

Automatic requests use a validated public socket address. Address checks bind to the actual socket destination, including after redirects. An explicitly configured private IP literal permits its own origin so local feeds and the demo can work. Derived URLs cannot use that exception for a different host, port or scheme. Private DNS hostnames are unsupported. Public-address checks assume normal network routing and do not enforce organization-specific egress policies.

XML uses Python's ElementTree with a two-megabyte byte limit, depth and element limits, and DTD/entity declarations disabled. Use a current system Python/Expat. Broken documents fail per feed; a malformed date becomes unknown. Duplicate article links are collapsed per feed. Titles are capped at 500 characters and rendered text at 160,000 characters. HTML is parsed to text and GTK displays it as text. Active HTML, scripts and a browser runtime are absent. Optional PNG/JPEG downloads are bounded by byte and pixel limits and use the operating system's decoder. Keep that decoder updated. [Python XML security documentation](https://docs.python.org/3/library/xml.html#xml-security).

## Local data and requests

| Data | Default location |
| --- | --- |
| Subscriptions and settings | `$XDG_CONFIG_HOME/retronews` or `~/.config/retronews` |
| Article JSON and images | `$XDG_CACHE_HOME/retronews` or `~/.cache/retronews` |
| SQLite index | `$XDG_STATE_HOME/retronews` or `~/.local/state/retronews` |
| Local command socket | `$XDG_RUNTIME_DIR/retronews`, with a state-directory fallback |

Permissions and the Unix socket are scoped to the local account. Subscriptions, source URLs, read flags and cached text are plain local data. Avoid sharing the profile, help screen, or CLI status/validation output when those contain personal URLs or query values. The application makes no telemetry or account-service requests. It can disclose the client IP, user agent, paths and query parameters to subscribed hosts, their robots endpoints, and eligible remote media or article hosts. Use HTTPS where available.

Article writes use a same-directory temporary file, fsync and replacement. Startup reconciles missing files and managed orphans. Bad article JSON is ignored. SQLite corruption causes a visible startup error without automatic deletion. Cleanup preserves user configuration and unrelated cache filenames. The default cap is 350 decimal MB for managed article/image files including in-progress writes. SQLite metadata is tracked separately. Old articles may be evicted by bytes, count or retention. Last usable cached articles for offline feeds are protected from age cleanup while they fit within the cap.

Use dedicated XDG paths owned by your account. RetroNews refuses symlink application directories and known SQLite file symlinks. It assumes the configured XDG parents and the local account are trusted. The cache and index are disposable offline storage; keep backups of important material outside them.
