-- Settings an administrator changes on the settings page instead of in config.yaml or the
-- environment (WP KC-0.1). One row per (tenant, key); tenant '' is the whole installation.
--
-- A plain value is JSON (text, number, map). A secret is never stored in ``value``: it is
-- encrypted with the deployment's PLATFORM_SETTINGS_KEY before it reaches this table and is
-- only ever decrypted in the Platform process. Exactly one of the two columns is set.
--
-- Precedence lives in the code (platform_settings.py): a value the deployment pins in the
-- environment wins over a row here, and a row here wins over config.yaml's default.

CREATE TABLE IF NOT EXISTS platform_settings (
    tenant      VARCHAR(80)  NOT NULL DEFAULT '',
    key         VARCHAR(120) NOT NULL CHECK (length(key) > 0),
    value       JSONB        NULL,
    secret      BYTEA        NULL,
    updated_by  UUID         NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, key),
    CHECK ((value IS NULL) <> (secret IS NULL))
);
