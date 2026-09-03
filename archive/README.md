# Archived Utilities

These standalone utilities predate the supported `data/collect` to
`data/calculate` workflow. They are retained for reference and are excluded
from the active test suite.

`fixtures/` holds the sample databases and JSON these scripts read and write. They use
the old `restaurants / sources / dishes / mentions` schema and are **not** the pipeline's
output — the pipeline builds exactly one database, `data/db/dishit.db`, on the canonical
schema in `data/db/schema.sql`.
