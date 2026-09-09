BEGIN;
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.

INFO  [alembic.runtime.migration] Generating static SQL
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 20260908_0019 -> 20260908_0020, Close portfolio audit findings around authority, ledger and valuation.
-- Running upgrade 20260908_0019 -> 20260908_0020

DO $$ BEGIN IF EXISTS (SELECT 1 FROM portfolio_positions LIMIT 1) OR EXISTS (SELECT 1 FROM portfolio_snapshots LIMIT 1) THEN RAISE EXCEPTION 'M4 positions/snapshots require trusted provenance migration'; END IF; END $$;

ALTER TABLE portfolios ADD COLUMN current_mandate_id UUID;

ALTER TABLE portfolios ADD COLUMN unit_count NUMERIC(36, 12) DEFAULT 0 NOT NULL;

ALTER TABLE portfolios ADD COLUMN last_nav_per_unit NUMERIC(36, 12) DEFAULT 1 NOT NULL;

ALTER TABLE portfolios ADD CONSTRAINT ck_portfolio_unit_count CHECK (unit_count >= 0);

ALTER TABLE portfolios ADD CONSTRAINT ck_portfolio_nav_positive CHECK (last_nav_per_unit > 0);

UPDATE portfolios AS p SET unit_count = GREATEST(COALESCE((SELECT sum(CASE WHEN t.event_type = 'funding' THEN t.amount WHEN t.event_type = 'withdrawal' THEN -t.amount ELSE 0 END) FROM portfolio_ledger_transactions t WHERE t.tenant_id = p.tenant_id AND t.portfolio_id = p.id), 0), 0);

ALTER TABLE portfolio_mandates ADD CONSTRAINT uq_mandate_identity_portfolio UNIQUE (id, tenant_id, portfolio_id);

ALTER TABLE portfolio_mandates DROP CONSTRAINT ck_mandate_mode;

ALTER TABLE portfolio_mandates ADD CONSTRAINT ck_mandate_mode CHECK (mode IN ('backtest','shadow','paper'));

UPDATE portfolios AS p SET current_mandate_id = latest.id FROM (SELECT DISTINCT ON (tenant_id, portfolio_id) id, tenant_id, portfolio_id FROM portfolio_mandates ORDER BY tenant_id, portfolio_id, version DESC) AS latest WHERE p.tenant_id = latest.tenant_id AND p.id = latest.portfolio_id;

ALTER TABLE portfolios ADD CONSTRAINT fk_portfolio_current_mandate FOREIGN KEY(current_mandate_id, tenant_id, id) REFERENCES portfolio_mandates (id, tenant_id, portfolio_id) ON DELETE RESTRICT;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.portfolios p JOIN public.portfolio_mandates m ON m.id = p.current_mandate_id AND m.tenant_id = p.tenant_id AND m.portfolio_id = p.id LEFT JOIN LATERAL (SELECT s.id, s.status, s.expires_at, s.effective_profile FROM public.portfolio_suitability_assessments s WHERE s.tenant_id = p.tenant_id AND s.user_id = p.owner_user_id ORDER BY s.version DESC LIMIT 1) current_s ON true WHERE m.status = 'active' AND (current_s.id IS NULL OR m.suitability_id <> current_s.id OR current_s.status <> 'valid' OR current_s.expires_at <= clock_timestamp() OR CASE m.profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END > CASE current_s.effective_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END)) THEN RAISE EXCEPTION 'M4 active mandate requires current valid suitability'; END IF; END $$;

CREATE FUNCTION portfolio_assert_current_mandate() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ DECLARE pid uuid; tenant uuid; pointed uuid; latest uuid; active_status text; active_suitability uuid; active_profile text; owner_id uuid; latest_suitability uuid; latest_status text; latest_expires timestamptz; latest_profile text; violations bigint; BEGIN IF TG_TABLE_NAME = 'portfolio_suitability_assessments' THEN IF TG_OP = 'DELETE' THEN tenant := OLD.tenant_id; owner_id := OLD.user_id; ELSE tenant := NEW.tenant_id; owner_id := NEW.user_id; END IF; SELECT count(*) INTO violations FROM public.portfolios p JOIN public.portfolio_mandates m ON m.id = p.current_mandate_id AND m.tenant_id = p.tenant_id AND m.portfolio_id = p.id LEFT JOIN LATERAL (SELECT s.id, s.status, s.expires_at, s.effective_profile FROM public.portfolio_suitability_assessments s WHERE s.tenant_id = p.tenant_id AND s.user_id = p.owner_user_id ORDER BY s.version DESC LIMIT 1) current_s ON true WHERE p.tenant_id = tenant AND p.owner_user_id = owner_id AND m.status = 'active' AND (current_s.id IS NULL OR m.suitability_id <> current_s.id OR current_s.status <> 'valid' OR current_s.expires_at <= clock_timestamp() OR CASE m.profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END > CASE current_s.effective_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END); IF violations <> 0 THEN RAISE EXCEPTION 'active mandate is incompatible with latest suitability'; END IF; RETURN NULL; END IF; IF TG_TABLE_NAME = 'portfolios' THEN pid := NEW.id; tenant := NEW.tenant_id; ELSE pid := NEW.portfolio_id; tenant := NEW.tenant_id; END IF; SELECT current_mandate_id INTO pointed FROM public.portfolios WHERE id = pid AND tenant_id = tenant; SELECT id INTO latest FROM public.portfolio_mandates WHERE portfolio_id = pid AND tenant_id = tenant ORDER BY version DESC LIMIT 1; IF pointed IS DISTINCT FROM latest THEN RAISE EXCEPTION 'portfolio current mandate does not reference latest version'; END IF; SELECT m.status, m.suitability_id, m.profile, p.owner_user_id INTO active_status, active_suitability, active_profile, owner_id FROM public.portfolios p LEFT JOIN public.portfolio_mandates m ON m.id = p.current_mandate_id AND m.tenant_id = p.tenant_id AND m.portfolio_id = p.id WHERE p.id = pid AND p.tenant_id = tenant; IF active_status = 'active' THEN SELECT s.id, s.status, s.expires_at, s.effective_profile INTO latest_suitability, latest_status, latest_expires, latest_profile FROM public.portfolio_suitability_assessments s WHERE s.tenant_id = tenant AND s.user_id = owner_id ORDER BY s.version DESC LIMIT 1; IF latest_suitability IS NULL OR active_suitability <> latest_suitability OR latest_status <> 'valid' OR latest_expires <= clock_timestamp() OR CASE active_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END > CASE latest_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END THEN RAISE EXCEPTION 'active mandate is incompatible with latest suitability'; END IF; END IF; RETURN NULL; END; $$;

CREATE CONSTRAINT TRIGGER portfolio_current_mandate_pointer AFTER INSERT OR UPDATE OF current_mandate_id, owner_user_id ON portfolios DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_current_mandate();

CREATE CONSTRAINT TRIGGER mandate_current_pointer AFTER INSERT OR UPDATE OF status, suitability_id, profile, version ON portfolio_mandates DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_current_mandate();

CREATE CONSTRAINT TRIGGER suitability_active_mandate_guard AFTER INSERT OR UPDATE OR DELETE ON portfolio_suitability_assessments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_current_mandate();

ALTER TABLE portfolio_ledger_transactions ADD COLUMN provenance VARCHAR(32);

ALTER TABLE portfolio_ledger_transactions ADD COLUMN verification_status VARCHAR(24);

ALTER TABLE portfolio_ledger_transactions ADD COLUMN unit_delta NUMERIC(36, 12);

ALTER TABLE portfolio_ledger_transactions ADD COLUMN nav_per_unit NUMERIC(36, 12);

DROP TRIGGER IF EXISTS portfolio_ledger_transactions_append_only ON portfolio_ledger_transactions;

UPDATE portfolio_ledger_transactions SET provenance = CASE WHEN event_type IN ('funding','withdrawal') THEN 'user_declared' ELSE 'internal' END, verification_status = CASE WHEN event_type IN ('funding','withdrawal') THEN 'unverified' ELSE 'internal' END, unit_delta = CASE WHEN event_type = 'funding' THEN amount WHEN event_type = 'withdrawal' THEN -amount ELSE 0 END, nav_per_unit = 1;

ALTER TABLE portfolio_ledger_transactions ALTER COLUMN provenance SET NOT NULL;

ALTER TABLE portfolio_ledger_transactions ALTER COLUMN verification_status SET NOT NULL;

ALTER TABLE portfolio_ledger_transactions ALTER COLUMN unit_delta SET NOT NULL;

ALTER TABLE portfolio_ledger_transactions ALTER COLUMN nav_per_unit SET NOT NULL;

ALTER TABLE portfolio_ledger_transactions ADD CONSTRAINT ck_ledger_transaction_provenance CHECK (provenance IN ('user_declared','broker_reconciled','internal'));

ALTER TABLE portfolio_ledger_transactions ADD CONSTRAINT ck_ledger_transaction_verification CHECK (verification_status IN ('unverified','verified','internal'));

ALTER TABLE portfolio_ledger_transactions ADD CONSTRAINT ck_ledger_transaction_provenance_coherence CHECK ((provenance = 'user_declared' AND verification_status = 'unverified' AND unit_delta <> 0) OR (provenance = 'broker_reconciled' AND verification_status = 'verified') OR (provenance = 'internal' AND verification_status = 'internal' AND unit_delta = 0));

ALTER TABLE portfolio_ledger_transactions ADD CONSTRAINT ck_ledger_transaction_nav CHECK (nav_per_unit > 0);

ALTER TABLE portfolio_ledger_transactions ADD CONSTRAINT uq_ledger_transaction_identity_portfolio UNIQUE (id, tenant_id, portfolio_id);

CREATE TRIGGER portfolio_ledger_transactions_append_only BEFORE UPDATE OR DELETE ON portfolio_ledger_transactions FOR EACH ROW EXECUTE FUNCTION portfolio_reject_history_mutation();

ALTER TABLE portfolio_ledger_postings DROP CONSTRAINT fk_ledger_posting_transaction;

ALTER TABLE portfolio_ledger_postings ADD CONSTRAINT fk_ledger_posting_transaction FOREIGN KEY(transaction_id, tenant_id, portfolio_id) REFERENCES portfolio_ledger_transactions (id, tenant_id, portfolio_id) ON DELETE RESTRICT;

ALTER TABLE portfolio_positions ADD COLUMN asset_class VARCHAR(16) NOT NULL;

ALTER TABLE portfolio_positions ADD CONSTRAINT ck_position_asset_class CHECK (asset_class IN ('crypto','stock'));

ALTER TABLE portfolio_snapshots ADD COLUMN unit_count NUMERIC(36, 12) DEFAULT 1 NOT NULL;

ALTER TABLE portfolio_snapshots ADD COLUMN nav_per_unit NUMERIC(36, 12) DEFAULT 1 NOT NULL;

ALTER TABLE portfolio_snapshots ADD COLUMN high_water_nav NUMERIC(36, 12) DEFAULT 1 NOT NULL;

ALTER TABLE portfolio_snapshots ADD COLUMN daily_drawdown NUMERIC(18, 12) DEFAULT 0 NOT NULL;

ALTER TABLE portfolio_snapshots ADD COLUMN valuation_source VARCHAR(32) DEFAULT 'market_data_pit' NOT NULL;

ALTER TABLE portfolio_snapshots ADD COLUMN valuation_observed_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE portfolio_snapshots ADD COLUMN valuation_available_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE portfolio_snapshots ADD COLUMN valuation_digest VARCHAR(64);

ALTER TABLE portfolio_snapshots ALTER COLUMN valuation_observed_at SET NOT NULL;

ALTER TABLE portfolio_snapshots ALTER COLUMN valuation_available_at SET NOT NULL;

ALTER TABLE portfolio_snapshots ALTER COLUMN valuation_digest SET NOT NULL;

ALTER TABLE portfolio_snapshots ADD CONSTRAINT ck_snapshot_unit_count CHECK (unit_count > 0);

ALTER TABLE portfolio_snapshots ADD CONSTRAINT ck_snapshot_nav CHECK (nav_per_unit > 0 AND high_water_nav > 0);

ALTER TABLE portfolio_snapshots ADD CONSTRAINT ck_snapshot_daily_drawdown CHECK (daily_drawdown >= 0 AND daily_drawdown <= 1);

ALTER TABLE portfolio_snapshots ADD CONSTRAINT ck_snapshot_valuation_source CHECK (valuation_source IN ('broker_reconciliation','market_data_pit'));

ALTER TABLE portfolio_snapshots ADD CONSTRAINT ck_snapshot_valuation_digest CHECK (length(valuation_digest) = 64);

ALTER TABLE portfolio_snapshots ADD CONSTRAINT ck_snapshot_valuation_timeline CHECK (valuation_observed_at <= valuation_available_at AND valuation_available_at <= captured_at);

DROP TRIGGER IF EXISTS portfolio_ledger_balanced ON portfolio_ledger_postings;

DROP FUNCTION IF EXISTS portfolio_assert_balanced_ledger();

CREATE FUNCTION portfolio_assert_complete_ledger() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ DECLARE tx uuid; posting_count bigint; total numeric; cash_count bigint; external_count bigint; tx_amount numeric; invalid_rows bigint; BEGIN IF TG_TABLE_NAME = 'portfolio_ledger_transactions' THEN tx := NEW.id; ELSE tx := COALESCE(NEW.transaction_id, OLD.transaction_id); END IF; SELECT t.amount, count(p.id), COALESCE(sum(p.quantity), 0), count(p.id) FILTER (WHERE p.account = 'cash'), count(p.id) FILTER (WHERE p.account = 'external'), count(p.id) FILTER (WHERE p.portfolio_id <> t.portfolio_id OR p.tenant_id <> t.tenant_id OR p.asset <> t.currency) INTO tx_amount, posting_count, total, cash_count, external_count, invalid_rows FROM public.portfolio_ledger_transactions t LEFT JOIN public.portfolio_ledger_postings p ON p.transaction_id = t.id WHERE t.id = tx GROUP BY t.amount, t.currency; IF NOT FOUND OR posting_count <> 2 OR total <> 0 OR cash_count <> 1 OR external_count <> 1 OR invalid_rows <> 0 OR NOT EXISTS (SELECT 1 FROM public.portfolio_ledger_postings p WHERE p.transaction_id = tx AND p.account = 'cash' AND abs(p.quantity) = tx_amount) THEN RAISE EXCEPTION 'ledger transaction is incomplete or inconsistent'; END IF; RETURN NULL; END; $$;

CREATE CONSTRAINT TRIGGER portfolio_ledger_complete_transaction AFTER INSERT ON portfolio_ledger_transactions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_complete_ledger();

CREATE CONSTRAINT TRIGGER portfolio_ledger_complete_posting AFTER INSERT OR UPDATE OR DELETE ON portfolio_ledger_postings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_complete_ledger();

UPDATE alembic_version SET version_num='20260908_0020' WHERE alembic_version.version_num = '20260908_0019';

COMMIT;
