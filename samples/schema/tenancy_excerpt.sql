-- Adsorra initial schema (excerpt: the core tenant tables).
--
-- Multi-tenancy rule: every tenant-owned row carries agency_id, and child tables
-- reference their parent through a composite (id, agency_id) foreign key. That makes
-- it impossible, at the database level, to link a row to a parent owned by another agency.

CREATE TABLE agencies (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clients (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id     uuid NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    name          text NOT NULL,
    report_email  text,
    goal          text NOT NULL DEFAULT 'CPA' CHECK (goal IN ('CPA', 'ROAS', 'CTR')),
    min_spend     numeric(14,2) NOT NULL DEFAULT 0 CHECK (min_spend >= 0),
    currency      text NOT NULL DEFAULT 'USD',
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, agency_id)
);
CREATE INDEX clients_agency_idx ON clients (agency_id);

CREATE TABLE users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id      uuid NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    email          text NOT NULL,
    password_hash  text NOT NULL,
    role           text NOT NULL CHECK (role IN ('owner', 'member', 'client')),
    client_id      uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    -- client logins are tied to exactly one client of the same agency
    CHECK ((role = 'client') = (client_id IS NOT NULL)),
    FOREIGN KEY (client_id, agency_id) REFERENCES clients (id, agency_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX users_email_unique ON users (lower(email));
CREATE INDEX users_agency_idx ON users (agency_id);

-- One row per connected ad account (a client can have several platforms, several accounts).
-- Platform is validated in the application so new platforms need no schema change.
CREATE TABLE connections (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id            uuid NOT NULL,
    client_id            uuid NOT NULL,
    platform             text NOT NULL,
    external_account_id  text NOT NULL,
    display_name         text,
    credentials_enc      text NOT NULL,           -- Fernet-encrypted JSON (tokens never stored in plain text)
    status               text NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'needs_reauth', 'error', 'disabled')),
    conversion_action    text,                    -- which result counts as a conversion (platform specific)
    last_synced_at       timestamptz,
    last_error           text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (client_id, agency_id) REFERENCES clients (id, agency_id) ON DELETE CASCADE,
    UNIQUE (id, agency_id),
    UNIQUE (client_id, platform, external_account_id)
);
CREATE INDEX connections_agency_idx ON connections (agency_id);

-- Ads/creatives, in one standard shape for every platform.
CREATE TABLE ads (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id              uuid NOT NULL,
    client_id              uuid NOT NULL,
    connection_id          uuid NOT NULL,
    platform               text NOT NULL,
    external_ad_id         text NOT NULL,
    name                   text,
    campaign_name          text,
    creative_url           text,                  -- platform link (often expires)
    creative_stored_path   text,                  -- our own copy of the image (filled by a later storage job)
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (connection_id, agency_id) REFERENCES connections (id, agency_id) ON DELETE CASCADE,
    FOREIGN KEY (client_id, agency_id) REFERENCES clients (id, agency_id) ON DELETE CASCADE,
    UNIQUE (id, agency_id),
    UNIQUE (connection_id, external_ad_id)
);
CREATE INDEX ads_client_idx ON ads (client_id);

CREATE TABLE ad_metrics_daily (
    ad_id        uuid NOT NULL,
    agency_id    uuid NOT NULL,
    client_id    uuid NOT NULL,
    date         date NOT NULL,
    spend        numeric(14,4) NOT NULL DEFAULT 0,
    impressions  bigint NOT NULL DEFAULT 0,
    clicks       bigint NOT NULL DEFAULT 0,
    conversions  numeric(14,2) NOT NULL DEFAULT 0,
    revenue      numeric(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (ad_id, date),
    FOREIGN KEY (ad_id, agency_id) REFERENCES ads (id, agency_id) ON DELETE CASCADE
);
