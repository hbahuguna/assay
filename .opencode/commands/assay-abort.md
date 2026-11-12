---
description: Request a manual quality-harness kill
---

Request a manual kill for the active run.

!`PYTHONPATH=/Users/skaparwan/github/assay /usr/local/bin/python3.11 -P -m harness abort --root ${1:-.harness} --reason "${2:-operator stop}"`
