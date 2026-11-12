---
description: Run quality-harness and summarize the result
---

Run the deterministic quality harness against the requested target and summarize the report.

!`/usr/local/bin/python3.11 -m harness run --target ${1:-examples/sample-repo} --no-browser --personas-dir personas --root .harness`
