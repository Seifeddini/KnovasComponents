-- Brain Builder: a site's certificate request is bound to the site (BB-07 B3/B4, S-4 M2; FEAT-2026-11-05-knovas-brain-v1).
--
-- Under DEC-0056 A1 the Platform issues no certificate at enrolment, so it recorded none: the client-certificate check
-- (BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT) protected no Knovas Link, and nothing tied the request a site prints to the
-- site or its tenant. Enrolment now records the SHA-256 of the request's SubjectPublicKeyInfo (``request_fingerprint``,
-- hex) for the component that enrolled with it; the site page shows it, and the admin compares it with the request
-- before signing. Once signed, the admin records the certificate on the site page: the Builder takes it only for that
-- key and the Platform's tenant and keeps its fingerprint (``certificate_fingerprint``), which the terminator's header
-- must then match.
--
-- Additive only: the new column is nullable.

ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS request_fingerprint TEXT
    CHECK (request_fingerprint IS NULL OR request_fingerprint ~ '^[0-9a-f]{64}$');
