-- Symma billing: wallets, wallet_transactions, demo_leads + trial backfill
-- Run against your app DB (e.g. symma_db / chatty):
--   psql "$DB_URL" -f scripts/migrate_billing_wallets_leads.sql

CREATE TABLE IF NOT EXISTS wallets (
    id           serial PRIMARY KEY,
    user_id      integer      NOT NULL UNIQUE,
    balance_inr  numeric(12, 2) NOT NULL DEFAULT 0,
    currency     varchar(8)   NOT NULL DEFAULT 'INR',
    updated_at   timestamptz  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at   timestamptz  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wallets_user_id ON wallets (user_id);

CREATE TABLE IF NOT EXISTS wallet_transactions (
    id                   serial PRIMARY KEY,
    user_id              integer        NOT NULL,
    amount_inr           numeric(12, 2) NOT NULL,
    source               varchar(16)    NOT NULL DEFAULT 'manual',
    note                 text,
    razorpay_payment_id  varchar(128),
    created_at           timestamptz    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wallet_tx_user_id ON wallet_transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_wallet_tx_created_at ON wallet_transactions (created_at);
CREATE INDEX IF NOT EXISTS idx_wallet_tx_source ON wallet_transactions (source);

CREATE TABLE IF NOT EXISTS demo_leads (
    id         serial PRIMARY KEY,
    name       varchar(255) NOT NULL,
    email      varchar(255) NOT NULL,
    store_url  varchar(512) NOT NULL,
    platform   varchar(64)  NOT NULL,
    source     varchar(64)  NOT NULL DEFAULT 'landing',
    status     varchar(16)  NOT NULL DEFAULT 'new',
    created_at timestamptz  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_demo_leads_email ON demo_leads (email);
CREATE INDEX IF NOT EXISTS idx_demo_leads_created_at ON demo_leads (created_at);
CREATE INDEX IF NOT EXISTS idx_demo_leads_status ON demo_leads (status);

-- Backfill wallets for existing users
INSERT INTO wallets (user_id, balance_inr, currency)
SELECT u.id, 0, 'INR'
FROM users u
WHERE NOT EXISTS (SELECT 1 FROM wallets w WHERE w.user_id = u.id);

-- Backfill 14-day trial from today for users with no subscription
INSERT INTO subscriptions (user_id, pack, type, start_date, end_date, created_at)
SELECT
    u.id,
    'trial',
    'monthly',
    CURRENT_DATE,
    CURRENT_DATE + 14,
    CURRENT_TIMESTAMP
FROM users u
WHERE NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id);

UPDATE users u
SET subscription_id = s.id
FROM subscriptions s
WHERE s.user_id = u.id
  AND u.subscription_id IS NULL
  AND s.id = (
      SELECT s2.id FROM subscriptions s2
      WHERE s2.user_id = u.id
      ORDER BY s2.created_at DESC
      LIMIT 1
  );
