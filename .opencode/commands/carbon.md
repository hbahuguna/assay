---
description: Run risk-based quality-harness checks like /carbon
---

Run the quality harness and summarize the result.

!`/usr/local/bin/python3.11 -m harness carbon --target ${1:-examples/sample-repo} --no-browser --personas-dir personas --root .harness`
