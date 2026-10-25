# Source checkpoint — 2026-09-18

This working-branch snapshot includes the dispatcher/driver interface updates,
configurable display units, routing-quality reporting, Valhalla route recovery,
PyVRP 0.14 support and per-vehicle service-time formulas.

This is a source checkpoint, not an application release. It also preserves the
unfinished, isolated PyVRP 1.x integration; that backend is not ready for use.
The existing seven failing test results relate to that unfinished work (version
validation wording and the missing Web solver panel). Do not interpret this
snapshot as a fully passing release or a completed PyVRP 1.x integration.

Local configuration backups are excluded. The published default upload token is
read from `BIZANT_MAP_UPLOAD_TOKEN`, or left empty; deployment credentials must
remain local. Previously deployed builds and their configuration are unchanged.
