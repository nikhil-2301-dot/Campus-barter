-- 0003_enrollment_unique: an enrollment number identifies one student across the whole
-- university (many colleges), so it must be unique. Rows created by 0002 hold the empty
-- default; give each a visible placeholder first so the unique index can be built.
-- Never edit this file after it has been applied anywhere; add 0004_*.sql instead.

UPDATE users SET enrollment_no = 'LEGACY-' || id WHERE enrollment_no = '';

CREATE UNIQUE INDEX uq_users_enrollment_no ON users (enrollment_no);
