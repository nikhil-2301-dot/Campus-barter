-- 0004_campus_requests: colleges that students ask us to add (the "Add your campus" page).
-- Anyone may send one, even before they have an account, so there is no foreign key to users.
-- The same college in the same area is stored once (compared without regard to capitals).
-- Never edit this file after it has been applied anywhere; add 0005_*.sql instead.

CREATE TABLE campus_requests (
    id           INTEGER PRIMARY KEY,
    college_name TEXT NOT NULL CHECK (length(college_name) BETWEEN 3 AND 120),
    area         TEXT NOT NULL CHECK (length(area) BETWEEN 2 AND 80),
    kind         TEXT NOT NULL CHECK (kind IN ('engineering', 'polytechnic', 'other')),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX uq_campus_requests_college_area ON campus_requests (lower(college_name), lower(area));
CREATE INDEX ix_campus_requests_created ON campus_requests (created_at);
