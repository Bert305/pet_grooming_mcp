-- Reference schema for the Pet Grooming Analytics MCP server.
--
-- This mirrors the Supabase/Postgres schema the tools query. Run it in your
-- Supabase SQL editor (or any Postgres) if you want a database to point the
-- server at. Enum values here match what the analytics assume (notably
-- 'scheduled' / 'completed' / 'cancelled' for appointments).

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
CREATE TYPE pet_species        AS ENUM ('dog', 'cat', 'rabbit', 'bird', 'other');
CREATE TYPE appointment_status AS ENUM ('scheduled', 'in_progress', 'completed', 'cancelled', 'no_show');
CREATE TYPE payment_method     AS ENUM ('card', 'cash', 'bank_transfer', 'online');
CREATE TYPE payment_status     AS ENUM ('pending', 'completed', 'failed', 'refunded');

-- ---------------------------------------------------------------------------
-- Core tables
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name    text        NOT NULL,
    email        text        UNIQUE NOT NULL,
    phone        text,
    address      text,
    preferences  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    is_active    boolean     NOT NULL DEFAULT true
);

CREATE TABLE breeds (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    species       pet_species NOT NULL,
    name          text        NOT NULL,
    size_category text,
    coat_type     text
);

CREATE TABLE pets (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       bigint      NOT NULL REFERENCES users (id),
    name          text        NOT NULL,
    species       pet_species NOT NULL,
    breed_id      bigint      REFERENCES breeds (id),
    date_of_birth date,
    weight_kg     numeric(6, 2),
    notes         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    is_active     boolean     NOT NULL DEFAULT true
);

CREATE TABLE services (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                  text          NOT NULL,
    description           text,
    species               pet_species   NOT NULL,
    base_duration_minutes integer       NOT NULL,
    base_price            numeric(10, 2) NOT NULL,
    is_active             boolean       NOT NULL DEFAULT true
);

CREATE TABLE appointments (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pet_id               bigint             NOT NULL REFERENCES pets (id),
    scheduled_start      timestamptz        NOT NULL,
    scheduled_end        timestamptz        NOT NULL,
    status               appointment_status NOT NULL DEFAULT 'scheduled',
    special_instructions text,
    created_at           timestamptz        NOT NULL DEFAULT now(),
    updated_at           timestamptz        NOT NULL DEFAULT now()
);

CREATE TABLE appointment_services (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    appointment_id           bigint  NOT NULL REFERENCES appointments (id),
    service_id               bigint  NOT NULL REFERENCES services (id),
    price_override           numeric(10, 2),
    duration_override_minutes integer
);

CREATE TABLE payments (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    appointment_id bigint         NOT NULL REFERENCES appointments (id),
    amount         numeric(10, 2) NOT NULL,
    currency       char(3)        NOT NULL DEFAULT 'USD',
    method         payment_method NOT NULL,
    status         payment_status NOT NULL DEFAULT 'pending',
    paid_at        timestamptz
);

-- ---------------------------------------------------------------------------
-- Helpful indexes for the analytics queries
-- ---------------------------------------------------------------------------
CREATE INDEX idx_pets_user_id                ON pets (user_id);
CREATE INDEX idx_appointments_pet_id         ON appointments (pet_id);
CREATE INDEX idx_appointments_start          ON appointments (scheduled_start);
CREATE INDEX idx_appointments_status         ON appointments (status);
CREATE INDEX idx_appt_services_appointment   ON appointment_services (appointment_id);
CREATE INDEX idx_appt_services_service       ON appointment_services (service_id);
CREATE INDEX idx_payments_appointment_id     ON payments (appointment_id);
CREATE INDEX idx_payments_status             ON payments (status);

-- ---------------------------------------------------------------------------
-- Recommended: a dedicated read-only role for the MCP server
-- ---------------------------------------------------------------------------
-- CREATE ROLE mcp_readonly LOGIN PASSWORD 'change-me';
-- GRANT CONNECT ON DATABASE postgres TO mcp_readonly;
-- GRANT USAGE ON SCHEMA public TO mcp_readonly;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;
