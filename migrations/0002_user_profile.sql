-- 0002_user_profile: extend the users table with college name, enrollment number,
-- social/contact link and address. All were absent in 0001; existing rows get empty defaults.
-- Never edit this file after it has been applied anywhere; add 0003_*.sql instead.

ALTER TABLE users ADD COLUMN college_name    TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN enrollment_no   TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN social_link     TEXT;   -- Reddit u/ or Instagram @, optional
ALTER TABLE users ADD COLUMN address         TEXT;   -- optional, private like phone
