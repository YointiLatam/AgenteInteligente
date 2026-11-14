-- Users table with roles for the admin panel (RBAC). Idempotent: safe to run more than once;
-- on a pre-existing `users` table it only adds what is missing and never drops or rewrites data.
BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS inmobiliaria_pruebas.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id varchar(255) UNIQUE,
    email varchar(255),
    role varchar(50) NOT NULL DEFAULT 'end_user'
        CHECK (role IN ('end_user', 'operator', 'admin')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- If `users` already existed (for example from the original architecture DDL), complete whatever
-- is missing instead of failing on the index below. Rows keep their data; new columns get defaults.
ALTER TABLE inmobiliaria_pruebas.users
    ADD COLUMN IF NOT EXISTS external_id varchar(255),
    ADD COLUMN IF NOT EXISTS email varchar(255),
    ADD COLUMN IF NOT EXISTS role varchar(50) NOT NULL DEFAULT 'end_user',
    ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS users_external_id_key
    ON inmobiliaria_pruebas.users (external_id);

-- A freshly created table already has this check under the same name. If an existing table holds
-- roles outside the set, this fails and the whole migration rolls back: fix those rows first.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_role_check'
          AND conrelid = 'inmobiliaria_pruebas.users'::regclass
    ) THEN
        ALTER TABLE inmobiliaria_pruebas.users
            ADD CONSTRAINT users_role_check CHECK (role IN ('end_user', 'operator', 'admin'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS users_role_idx ON inmobiliaria_pruebas.users (role);

-- Panel users log in with an API key exchanged at POST /auth/token; the JWT subject is the
-- API-key client name, which must equal users.external_id. Example: create the first admin
-- (replace 'demo' with the client name used in AUTH_API_KEYS):
--
--   INSERT INTO inmobiliaria_pruebas.users (external_id, email, role)
--   VALUES ('demo', 'admin@example.com', 'admin')
--   ON CONFLICT (external_id) DO UPDATE SET role = EXCLUDED.role;
--
-- Revoke access (takes effect on the next request, the role is read on every request):
--
--   UPDATE inmobiliaria_pruebas.users SET role = 'end_user' WHERE external_id = 'demo';

COMMIT;
