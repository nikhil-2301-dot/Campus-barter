# ADR 0002: Rules enforced by the database itself (chunk C2)
Context: authorization and the status machine live in Python (can(), C4/C5), but the
cheapest bugs to catch are impossible rows. SQLite has no row-level security, so the
schema carries every rule that does not need application context.
Decision (migrations/0001_init.sql):
- CHECK constraints for year, mode, condition, statuses, 0/1 flags, lower-case email, and
  "status = removed exactly when deleted_at is set" (soft delete cannot half-happen).
- Partial unique indexes: one OPEN (pending/accepted) request per requester per listing;
  one ACCEPTED request per listing. Both come straight from the status model.
- A request can never offer the listing it asks for (CHECK).
- Foreign keys are ON for every connection (PRAGMA in db.connect) and NO cascades: rows are
  soft-deleted or anonymised, never hard-deleted, so a hard delete of a used row must fail.
- Categories are NOT a CHECK: they are a config list (docs/system-design.md section 5).
- Condition values (new, good, fair, worn) were chosen in C2; the design did not list them.
- updated_at is maintained by triggers (WHEN guard prevents re-firing). Timestamps are UTC text.
- Migrations: numbered files, SHA-256 recorded, editing an applied file is refused, each file
  runs in one transaction with its bookkeeping row.
Not in the database (stays in Python): requester != owner, email format check, who may
change which status, privacy of phone numbers.
Consequences: invalid states fail loudly in tests (tests/test_db.py). Changing a rule means a
new migration (SQLite needs a table rebuild to alter a CHECK), which is acceptable at this size.
