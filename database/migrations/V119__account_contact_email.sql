BEGIN;
ALTER TABLE platform.user_ref ADD COLUMN email text
 CHECK (email IS NULL OR (length(email) <= 254 AND email ~ '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$'));
COMMENT ON COLUMN platform.user_ref.email IS 'Optional contact email, independent of account_code and phone login; not a login alias';
COMMIT;
