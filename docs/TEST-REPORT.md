# RetroNews 0.1.0 test report

Run on 2026-11-21 in Arch Linux / Omarchy x86_64, native Wayland, Hyprland 0.56.2, 1920 × 1080 output at 125% scale. Python 3.14.8, GTK 4.24.1, PyGObject 3.58.1, GdkPixbuf 2.44.8, Expat 2.9.0 and SQLite 3.54.0. The release changes are listed in [release notes](RELEASE-NOTES.md). The exact release files are identified by [SHA-256](../SHA256SUMS).

| Result | Scope | Evidence |
| --- | --- | --- |
| PASS | Original unittest baseline | 36 passed on the native Wayland session. |
| PASS | Release unittest suite | 43 passed. RSS and Atom, empty/malformed XML, declaration/depth/size bounds, encodings, unknown dates, duplicate links, plaintext, redirects, destination restrictions, robots, HTTP errors/timeouts, index and cache failures, atomic writes, retention, process restart and filtering. |
| PASS | Installed GTK4 window on native Wayland | Installed into a temporary prefix, loaded four subscriptions from JSON, refreshed manually and received 12 articles. Opened the long Atom entry, scrolled, navigated back, changed language and category, opened help and quit through the GTK window. |
| PASS | Desktop offline restart | With the HTTP server stopped, manual refresh reported errors for all four subscriptions. Twelve articles persisted and the long Atom entry opened from cache in a new process. The fixture reset only the last-success timestamps to bypass the normal 60-second refresh floor. |
| PASS | `tests/smoke_gtk.py` | GTK mapping, cached text and image, keyboard handlers, filters, help, scroll extent and theme reload. |
| PASS | `tests/smoke_offline.py` | A second GTK process opened previously fetched article text and a cached image, then kept them through a failed refresh. |
| PASS | `tests/smoke_layout.py` | Five real GTK allocation/snapshot cases in dark and light palettes, including a 400 × 700 logical layout and portrait image. |
| PASS | `tests/qa_image_indicator.py` | Cached-image marker, invalid media handling, and cleanup preserving configuration. |
| FAIL (corrected) | Early sandbox and smoke invocations | The initial sandbox denied loopback and desktop resources. The follow-up baseline passed on Wayland. Intermediate smoke runs also exposed a relative Wayland socket tied to the overridden runtime path and a layout fixture tied to the former default feed list. Both smokes passed after correction. |
| SKIP | External feeds, live publisher content, and HTTPS end-to-end | This run used synthetic loopback feeds and fixtures. System TLS verification remains enabled for HTTPS. |
| SKIP | Other distributions, X11 and historical diagnostics | The release verification covers the Linux setup above. |
| SKIP | Full-text search | The application supports category and language filters. |

```bash
/usr/bin/python3 -m unittest discover -s tests -v
/usr/bin/python3 tests/smoke_gtk.py
/usr/bin/python3 tests/smoke_offline.py
RETRONEWS_QA_OUTPUT="$(mktemp -d)" /usr/bin/python3 tests/smoke_layout.py
/usr/bin/python3 tests/qa_image_indicator.py
```

For manual acceptance, follow the isolated demo steps in [installation](INSTALL.md). Compare the headline, full-text and language views with the [showcase](../README.md).
