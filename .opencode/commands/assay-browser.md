---
description: Run quality-harness live browser assessment (requires a URL)
---

Run the quality-harness live browser assessment for $ARGUMENTS.

If no URL was provided, explain that `/assay-browser` needs a reachable URL such as
`/assay-browser http://localhost:3000`. A directory-only run is a static scan, and
`/assay-browser` must never present that as browser coverage.

!`if [ -z "$1" ]; then echo "ERROR: /assay-browser requires a URL, for example /assay-browser http://localhost:3000"; else url="$1"; shift; QH_SSRF_ALLOWLIST="${QH_SSRF_ALLOWLIST:-127.0.0.0/8,::1,localhost}" PYTHONPATH=/Users/skaparwan/github/assay /usr/local/bin/python3.11 -P -m harness run --target "$url" --personas-dir /Users/skaparwan/github/assay/personas --intent "Assay browser assessment" --crawl --max-pages 8 --mobile --headed "$@"; fi`
