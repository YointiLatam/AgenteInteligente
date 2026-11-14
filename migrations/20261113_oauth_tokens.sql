BEGIN;

CREATE SCHEMA IF NOT EXISTS inmobiliaria_pruebas;

-- Tokens OAuth2 de las integraciones (Meta, WhatsApp y X). Los valores se guardan cifrados con
-- Fernet; la clave (TOKEN_ENCRYPTION_KEY) nunca se guarda en la base de datos.
CREATE TABLE IF NOT EXISTS inmobiliaria_pruebas.oauth_tokens (
    provider TEXT PRIMARY KEY CHECK (provider IN ('meta', 'whatsapp', 'x')),
    access_token_enc TEXT NOT NULL,
    refresh_token_enc TEXT,
    expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE inmobiliaria_pruebas.oauth_tokens IS
    'Tokens OAuth2 cifrados (Fernet) de Meta, WhatsApp y X; una fila por proveedor.';

COMMIT;
