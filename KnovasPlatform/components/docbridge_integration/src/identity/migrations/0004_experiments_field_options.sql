-- Experimente: values people add to a selection field (enum, multi_enum) of
-- a domain, such as a new customer segment ("Kanzlei mittel").
--
-- A type's options are part of its versioned definition, and an experiment
-- keeps the version it was created with: adding an option there would reach
-- new experiments only. These values belong to the domain instead and apply
-- to every experiment of it, whatever its type version, for each field with
-- that key -- unless the type marks the field `extensible: false`.

CREATE TABLE IF NOT EXISTS exp_field_options (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_id   UUID NOT NULL REFERENCES exp_domains(id) ON DELETE CASCADE,
    field_key   TEXT NOT NULL CHECK (field_key ~ '^[a-z][a-z0-9_]{0,39}$'),
    value       TEXT NOT NULL CHECK (char_length(value) BETWEEN 1 AND 80 AND value = btrim(value)),
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One spelling per value: "Kanzlei mittel" and "kanzlei Mittel" are the same.
CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_field_options_value
    ON exp_field_options (domain_id, field_key, lower(value));
