# RetroNews 0.1.1

The showcase now uses a synthetic signal chart attached to its Atom article. The README demonstration follows the headline list, a category filter and the cached reader, including its theme-filtered and original image modes. The newsreader behavior is the same as in 0.1.0.

## RetroNews 0.1.0

- Standalone GTK4 application with RSS/Atom, category and language filters, and offline reading.
- Four synthetic showcase subscriptions covering both supported feed formats.
- Fresh installations begin with empty subscriptions. Existing XDG configuration is retained.
- Feed parsing and downloads enforce size limits, XML structure limits and safe request destinations.
- The cache rejects malformed article data and avoids following local symlinks when reading article JSON. Cleanup is limited to managed names.
- Local installation uses the distribution's Python and GTK packages.

The reader uses a German-first interface and JSON subscription editing. It supports plaintext rendering, optional PNG/JPEG media and best-effort article extraction. Proxy-required networks and authenticated feeds are unsupported. See [installation](INSTALL.md), [security](SECURITY-PRIVACY.md) and the [test report](TEST-REPORT.md).

## Package verification

From the extracted root, run `sha256sum --check SHA256SUMS`. `MANIFEST.txt` lists every packaged file. The checksums cover every listed file apart from the checksum list itself. The detached archive checksum protects the complete archive. The release archive uses sorted paths, numeric ownership, a fixed UTC modification time of 2026-11-21 00:00:00 and gzip without a timestamp.
