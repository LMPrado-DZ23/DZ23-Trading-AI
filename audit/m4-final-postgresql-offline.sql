BEGIN;

-- Running upgrade 20260908_0019 -> 20260908_0020

DO $$ BEGIN IF EXISTS (SELECT 1 FROM portfolio_positions LIMIT 1) OR EXISTS (SELECT 1 FROM portfolio_snapshots LIMIT 1) THEN RAISE EXCEPTION 'M4 positions/snapshots require trusted provenance migration'; END IF; END $$;

ALTER TABLE portfolios ADD COLUMN current_mandate_id UUID;

ALTER TABLE portfolios ADD COLUMN unit_count NUMERIC(36, 12) DEFAULT 0 NOT NULL;

ALTER TABLE portfolios ADD COLUMN last_nav_per_unit NUMERIC(36, 12) DEFAULT 1 NOT NULL;

ALTER TABLE portfolios ADD CONSTRAINT ck_portfolio_unit_count CHECK (unit_count >= 0);

ALTER TABLE portfolios ADD CONSTRAINT ck_portfolio_nav_positive CHECK (last_nav_per_unit > 0);

UPDATE portfolios AS p SET unit_count = COALESCE((SELECT sum(CASE WHEN t.event_type IN ('funding','paper_funding','shadow_funding') THEN t.amount WHEN t.event_type IN ('withdrawal','paper_withdrawal','shadow_withdrawal') THEN -t.amount ELSE 0 END) FROM portfolio_ledger_transactions t WHERE t.tenant_id = p.tenant_id AND t.portfolio_id = p.id), 0);

ALTER TABLE portfolio_mandates ADD CONSTRAINT uq_mandate_identity_portfolio UNIQUE (id, tenant_id, portfolio_id);

ALTER TABLE portfolio_mandates DROP CONSTRAINT ck_mandate_mode;

ALTER TABLE portfolio_mandates ADD CONSTRAINT ck_mandate_mode CHECK (mode IN ('backtest','shadow','paper'));

ALTER TABLE portfolio_mandates ADD COLUMN pause_reason VARCHAR(40);

DROP TRIGGER IF EXISTS portfolio_mandates_append_only ON portfolio_mandates;

UPDATE portfolio_mandates SET pause_reason = 'legacy_pause' WHERE status = 'paused';

ALTER TABLE portfolio_mandates ADD CONSTRAINT ck_mandate_pause_reason CHECK ((status = 'paused' AND pause_reason IN ('suitability_reassessment','user_requested','legacy_pause')) OR (status <> 'paused' AND pause_reason IS NULL));

CREATE TRIGGER portfolio_mandates_append_only BEFORE UPDATE OR DELETE ON portfolio_mandates FOR EACH ROW EXECUTE FUNCTION portfolio_reject_history_mutation();

ALTER TABLE portfolio_mandates ADD CONSTRAINT ck_mandate_ended_at CHECK ((status = 'closed' AND ended_at IS NOT NULL AND ended_at >= effective_at) OR (status <> 'closed' AND ended_at IS NULL));

UPDATE portfolios AS p SET current_mandate_id = latest.id FROM (SELECT DISTINCT ON (tenant_id, portfolio_id) id, tenant_id, portfolio_id FROM portfolio_mandates ORDER BY tenant_id, portfolio_id, version DESC) AS latest WHERE p.tenant_id = latest.tenant_id AND p.id = latest.portfolio_id;

ALTER TABLE portfolios ADD CONSTRAINT fk_portfolio_current_mandate FOREIGN KEY(current_mandate_id, tenant_id, id) REFERENCES portfolio_mandates (id, tenant_id, portfolio_id) ON DELETE RESTRICT;

ALTER TABLE portfolio_cash_reservations ADD CONSTRAINT ck_reservation_status_timestamps CHECK ((status = 'active' AND released_at IS NULL AND consumed_at IS NULL) OR (status = 'released' AND released_at IS NOT NULL AND consumed_at IS NULL) OR (status = 'consumed' AND consumed_at IS NOT NULL AND released_at IS NULL) OR (status = 'expired' AND released_at IS NULL AND consumed_at IS NULL));

ALTER TABLE portfolio_cash_reservations DROP CONSTRAINT fk_reservation_mandate;

ALTER TABLE portfolio_cash_reservations ADD CONSTRAINT fk_reservation_mandate FOREIGN KEY(mandate_id, tenant_id, portfolio_id) REFERENCES portfolio_mandates (id, tenant_id, portfolio_id) ON DELETE RESTRICT;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.portfolios p JOIN public.portfolio_mandates m ON m.id = p.current_mandate_id AND m.tenant_id = p.tenant_id AND m.portfolio_id = p.id LEFT JOIN LATERAL (SELECT s.id, s.status, s.expires_at, s.effective_profile FROM public.portfolio_suitability_assessments s WHERE s.tenant_id = p.tenant_id AND s.user_id = p.owner_user_id ORDER BY s.version DESC LIMIT 1) current_s ON true WHERE m.status = 'active' AND (current_s.id IS NULL OR m.suitability_id <> current_s.id OR current_s.status <> 'valid' OR current_s.expires_at <= clock_timestamp() OR CASE m.profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END > CASE current_s.effective_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END)) THEN RAISE EXCEPTION 'M4 active mandate requires current valid suitability'; END IF; END $$;

CREATE FUNCTION portfolio_assert_current_mandate() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ DECLARE pid uuid; tenant uuid; pointed uuid; latest uuid; active_status text; active_suitability uuid; active_profile text; owner_id uuid; latest_suitability uuid; latest_status text; latest_expires timestamptz; latest_profile text; violations bigint; BEGIN IF TG_TABLE_NAME = 'portfolio_suitability_assessments' THEN IF TG_OP = 'DELETE' THEN tenant := OLD.tenant_id; owner_id := OLD.user_id; ELSE tenant := NEW.tenant_id; owner_id := NEW.user_id; END IF; SELECT count(*) INTO violations FROM public.portfolios p JOIN public.portfolio_mandates m ON m.id = p.current_mandate_id AND m.tenant_id = p.tenant_id AND m.portfolio_id = p.id LEFT JOIN LATERAL (SELECT s.id, s.status, s.expires_at, s.effective_profile FROM public.portfolio_suitability_assessments s WHERE s.tenant_id = p.tenant_id AND s.user_id = p.owner_user_id ORDER BY s.version DESC LIMIT 1) current_s ON true WHERE p.tenant_id = tenant AND p.owner_user_id = owner_id AND m.status = 'active' AND (current_s.id IS NULL OR m.suitability_id <> current_s.id OR current_s.status <> 'valid' OR current_s.expires_at <= clock_timestamp() OR CASE m.profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END > CASE current_s.effective_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END); IF violations <> 0 THEN RAISE EXCEPTION 'active mandate is incompatible with latest suitability'; END IF; RETURN NULL; END IF; IF TG_TABLE_NAME = 'portfolios' THEN pid := NEW.id; tenant := NEW.tenant_id; ELSE pid := NEW.portfolio_id; tenant := NEW.tenant_id; END IF; SELECT current_mandate_id INTO pointed FROM public.portfolios WHERE id = pid AND tenant_id = tenant; SELECT id INTO latest FROM public.portfolio_mandates WHERE portfolio_id = pid AND tenant_id = tenant ORDER BY version DESC LIMIT 1; IF pointed IS DISTINCT FROM latest THEN RAISE EXCEPTION 'portfolio current mandate does not reference latest version'; END IF; SELECT m.status, m.suitability_id, m.profile, p.owner_user_id INTO active_status, active_suitability, active_profile, owner_id FROM public.portfolios p LEFT JOIN public.portfolio_mandates m ON m.id = p.current_mandate_id AND m.tenant_id = p.tenant_id AND m.portfolio_id = p.id WHERE p.id = pid AND p.tenant_id = tenant; IF active_status = 'active' THEN SELECT s.id, s.status, s.expires_at, s.effective_profile INTO latest_suitability, latest_status, latest_expires, latest_profile FROM public.portfolio_suitability_assessments s WHERE s.tenant_id = tenant AND s.user_id = owner_id ORDER BY s.version DESC LIMIT 1; IF latest_suitability IS NULL OR active_suitability <> latest_suitability OR latest_status <> 'valid' OR latest_expires <= clock_timestamp() OR (CASE active_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END ) > (CASE latest_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END ) THEN RAISE EXCEPTION 'active mandate is incompatible with latest suitability'; END IF; END IF; RETURN NULL; END; $$;

CREATE CONSTRAINT TRIGGER portfolio_current_mandate_pointer AFTER INSERT OR UPDATE OF current_mandate_id, owner_user_id ON portfolios DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_current_mandate();

CREATE CONSTRAINT TRIGGER mandate_current_pointer AFTER INSERT OR UPDATE OF status, suitability_id, profile, version ON portfolio_mandates DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_current_mandate();

CREATE CONSTRAINT TRIGGER suitability_active_mandate_guard AFTER INSERT OR UPDATE OR DELETE ON portfolio_suitability_assessments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_current_mandate();

ALTER TABLE portfolio_ledger_transactions ADD COLUMN provenance VARCHAR(32);

ALTER TABLE portfolio_ledger_transactions ADD COLUMN verification_status VARCHAR(24);

ALTER TABLE portfolio_ledger_transactions ADD COLUMN unit_delta NUMERIC(36, 12);

ALTER TABLE portfolio_ledger_transactions ADD COLUMN nav_per_unit NUMERIC(36, 12);

DROP TRIGGER IF EXISTS portfolio_ledger_transactions_append_only ON portfolio_ledger_transactions;

UPDATE portfolio_ledger_transactions SET provenance = CASE WHEN event_type IN ('funding','paper_funding','shadow_funding','withdrawal','paper_withdrawal','shadow_withdrawal') THEN 'user_declared' ELSE 'internal' END, verification_status = CASE WHEN event_type IN ('funding','paper_funding','shadow_funding','withdrawal','paper_withdrawal','shadow_withdrawal') THEN 'unverified' ELSE 'internal' END, unit_delta = CASE WHEN event_type IN ('funding','paper_funding','shadow_funding') THEN amount WHEN event_type IN ('withdrawal','paper_withdrawal','shadow_withdrawal') THEN -amount ELSE 0 END, nav_per_unit = 1;

ALTER TABLE portfolio_ledger_transactions ALTER COLUMN provenance SET NOT NULL;

ALTER TABLE portfolio_ledger_transactions ALTER COLUMN verification_status SET NOT NULL;

ALTER TABLE portfolio_ledger_transactions ALTER COLUMN unit_delta SET NOT NULL;

ALTER TABLE portfolio_ledger_transactions ALTER COLUMN nav_per_unit SET NOT NULL;

ALTER TABLE portfolio_ledger_transactions ADD CONSTRAINT ck_ledger_transaction_provenance CHECK (provenance IN ('user_declared','broker_reconciled','internal'));

ALTER TABLE portfolio_ledger_transactions ADD CONSTRAINT ck_ledger_transaction_verification CHECK (verification_status IN ('unverified','verified','internal'));

ALTER TABLE portfolio_ledger_transactions ADD CONSTRAINT ck_ledger_transaction_provenance_coherence CHECK ((provenance = 'user_declared' AND verification_status = 'unverified' AND ((event_type IN ('funding','paper_funding','shadow_funding') AND unit_delta > 0) OR (event_type IN ('withdrawal','paper_withdrawal','shadow_withdrawal') AND unit_delta < 0))) OR (provenance = 'broker_reconciled' AND verification_status = 'verified' AND ((event_type = 'broker_funding' AND unit_delta > 0) OR (event_type = 'broker_withdrawal' AND unit_delta < 0) OR (event_type NOT IN ('broker_funding','broker_withdrawal') AND unit_delta = 0))) OR (provenance = 'internal' AND verification_status = 'internal' AND event_type NOT IN ('funding','paper_funding','shadow_funding','withdrawal','paper_withdrawal','shadow_withdrawal','broker_funding','broker_withdrawal') AND unit_delta = 0));

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

CREATE FUNCTION portfolio_assert_complete_ledger() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ DECLARE tx uuid; posting_count bigint; total numeric; cash_count bigint; external_count bigint; tx_amount numeric; invalid_rows bigint; tx_event text; tx_delta numeric; tx_nav numeric; cash_quantity numeric; BEGIN IF TG_TABLE_NAME = 'portfolio_ledger_transactions' THEN tx := NEW.id; ELSE tx := COALESCE(NEW.transaction_id, OLD.transaction_id); END IF; SELECT t.amount, t.event_type, t.unit_delta, t.nav_per_unit, count(p.id), COALESCE(sum(p.quantity), 0), count(p.id) FILTER (WHERE p.account = 'cash'), count(p.id) FILTER (WHERE p.account = 'external'), max(p.quantity) FILTER (WHERE p.account = 'cash'), count(p.id) FILTER (WHERE p.portfolio_id <> t.portfolio_id OR p.tenant_id <> t.tenant_id OR p.asset <> t.currency) INTO tx_amount, tx_event, tx_delta, tx_nav, posting_count, total, cash_count, external_count, cash_quantity, invalid_rows FROM public.portfolio_ledger_transactions t LEFT JOIN public.portfolio_ledger_postings p ON p.transaction_id = t.id WHERE t.id = tx GROUP BY t.amount, t.event_type, t.unit_delta, t.nav_per_unit, t.currency; IF NOT FOUND OR posting_count <> 2 OR total <> 0 OR cash_count <> 1 OR external_count <> 1 OR invalid_rows <> 0 OR NOT EXISTS (SELECT 1 FROM public.portfolio_ledger_postings p WHERE p.transaction_id = tx AND p.account = 'cash' AND abs(p.quantity) = tx_amount) OR (tx_event IN ('funding','paper_funding','shadow_funding','broker_funding') AND (cash_quantity <= 0 OR tx_delta <> round(tx_amount / tx_nav, 12))) OR (tx_event IN ('withdrawal','paper_withdrawal','shadow_withdrawal','broker_withdrawal') AND (cash_quantity >= 0 OR tx_delta <> -round(tx_amount / tx_nav, 12))) OR (tx_event = 'fee' AND cash_quantity >= 0) THEN RAISE EXCEPTION 'ledger transaction is incomplete or inconsistent'; END IF; RETURN NULL; END; $$;

CREATE CONSTRAINT TRIGGER portfolio_ledger_complete_transaction AFTER INSERT ON portfolio_ledger_transactions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_complete_ledger();

CREATE CONSTRAINT TRIGGER portfolio_ledger_complete_posting AFTER INSERT OR UPDATE OR DELETE ON portfolio_ledger_postings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_complete_ledger();

CREATE FUNCTION portfolio_assert_operational_mandate() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ DECLARE latest_mandate uuid; active_mandate uuid; owner_id uuid; mandate_status text; mandate_effective timestamptz; mandate_ended timestamptz; mandate_suitability uuid; mandate_profile text; latest_suitability uuid; suitability_status text; suitability_assessed timestamptz; suitability_expires timestamptz; suitability_profile text; snapshot_count bigint; requires_operational boolean := true; BEGIN IF TG_TABLE_NAME = 'portfolio_cash_reservations' THEN requires_operational := NEW.status IN ('active','consumed'); IF TG_OP = 'INSERT' AND NEW.status <> 'active' THEN RAISE EXCEPTION 'cash reservation must be created active'; END IF; IF NEW.status = 'consumed' THEN RAISE EXCEPTION 'cash reservation consumption is unavailable before execution fencing'; END IF; IF TG_OP = 'UPDATE' THEN IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id OR OLD.portfolio_id IS DISTINCT FROM NEW.portfolio_id OR OLD.mandate_id IS DISTINCT FROM NEW.mandate_id OR OLD.idempotency_key_hash IS DISTINCT FROM NEW.idempotency_key_hash OR OLD.currency IS DISTINCT FROM NEW.currency OR OLD.amount IS DISTINCT FROM NEW.amount OR OLD.purpose_digest IS DISTINCT FROM NEW.purpose_digest OR OLD.expires_at IS DISTINCT FROM NEW.expires_at OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN RAISE EXCEPTION 'cash reservation immutable fields cannot change'; END IF; IF OLD.status <> 'active' OR NEW.status NOT IN ('active','consumed','released','expired') THEN RAISE EXCEPTION 'cash reservation transition is invalid'; END IF; END IF; END IF; SELECT p.current_mandate_id, p.owner_user_id INTO active_mandate, owner_id FROM public.portfolios p WHERE p.id = NEW.portfolio_id AND p.tenant_id = NEW.tenant_id; SELECT m.id INTO latest_mandate FROM public.portfolio_mandates m WHERE m.portfolio_id = NEW.portfolio_id AND m.tenant_id = NEW.tenant_id ORDER BY m.version DESC LIMIT 1; SELECT m.status, m.effective_at, m.ended_at, m.suitability_id, m.profile INTO mandate_status, mandate_effective, mandate_ended, mandate_suitability, mandate_profile FROM public.portfolio_mandates m WHERE m.id = active_mandate AND m.tenant_id = NEW.tenant_id AND m.portfolio_id = NEW.portfolio_id; SELECT s.id, s.status, s.assessed_at, s.expires_at, s.effective_profile INTO latest_suitability, suitability_status, suitability_assessed, suitability_expires, suitability_profile FROM public.portfolio_suitability_assessments s WHERE s.tenant_id = NEW.tenant_id AND s.user_id = owner_id ORDER BY s.version DESC LIMIT 1; IF active_mandate IS NULL OR active_mandate <> latest_mandate OR mandate_status <> 'active' OR mandate_effective > clock_timestamp() OR mandate_ended IS NOT NULL OR mandate_suitability <> latest_suitability OR suitability_status <> 'valid' OR suitability_assessed > clock_timestamp() OR suitability_expires <= clock_timestamp() OR (CASE mandate_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END ) > (CASE suitability_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END ) THEN IF requires_operational THEN RAISE EXCEPTION 'portfolio has no operational mandate'; END IF; END IF; IF TG_TABLE_NAME = 'portfolio_cash_reservations' THEN IF NEW.status IN ('active','consumed') AND NEW.mandate_id IS DISTINCT FROM active_mandate THEN RAISE EXCEPTION 'cash reservation mandate does not match portfolio'; END IF; IF TG_OP = 'INSERT' AND (NEW.created_at > clock_timestamp() OR NEW.expires_at < NEW.created_at + interval '30 seconds' OR NEW.expires_at > NEW.created_at + interval '1 hour') THEN RAISE EXCEPTION 'cash reservation timeline is invalid'; END IF; IF NEW.status = 'released' AND (NEW.released_at < NEW.created_at OR NEW.released_at > clock_timestamp()) THEN RAISE EXCEPTION 'cash reservation release timeline is invalid'; END IF; IF requires_operational THEN SELECT count(*) INTO snapshot_count FROM public.portfolio_snapshots s WHERE s.tenant_id = NEW.tenant_id AND s.portfolio_id = NEW.portfolio_id; IF snapshot_count = 0 THEN RAISE EXCEPTION 'portfolio has no risk baseline snapshot'; END IF; END IF; END IF; RETURN NULL; END; $$;

CREATE CONSTRAINT TRIGGER ledger_operational_mandate AFTER INSERT ON portfolio_ledger_transactions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_operational_mandate();

CREATE CONSTRAINT TRIGGER reservation_operational_mandate AFTER INSERT OR UPDATE ON portfolio_cash_reservations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_operational_mandate();

CREATE CONSTRAINT TRIGGER snapshot_operational_mandate AFTER INSERT ON portfolio_snapshots DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_operational_mandate();

CREATE CONSTRAINT TRIGGER position_operational_mandate AFTER INSERT OR UPDATE ON portfolio_positions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_operational_mandate();

CREATE FUNCTION portfolio_assert_derived_state() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ DECLARE pid uuid; tenant uuid; expected_units numeric; expected_nav numeric; actual_units numeric; actual_nav numeric; BEGIN IF TG_TABLE_NAME = 'portfolios' THEN pid := NEW.id; tenant := NEW.tenant_id; ELSE pid := NEW.portfolio_id; tenant := NEW.tenant_id; END IF; SELECT COALESCE(sum(t.unit_delta), 0) INTO expected_units FROM public.portfolio_ledger_transactions t WHERE t.tenant_id = tenant AND t.portfolio_id = pid; SELECT COALESCE((SELECT s.nav_per_unit FROM public.portfolio_snapshots s WHERE s.tenant_id = tenant AND s.portfolio_id = pid ORDER BY s.sequence DESC LIMIT 1), 1) INTO expected_nav; SELECT p.unit_count, p.last_nav_per_unit INTO actual_units, actual_nav FROM public.portfolios p WHERE p.tenant_id = tenant AND p.id = pid; IF actual_units IS DISTINCT FROM expected_units OR actual_nav IS DISTINCT FROM expected_nav THEN RAISE EXCEPTION 'portfolio derived state diverges from immutable ledger'; END IF; RETURN NULL; END; $$;

CREATE CONSTRAINT TRIGGER portfolio_derived_state AFTER INSERT OR UPDATE OF unit_count, last_nav_per_unit ON portfolios DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_derived_state();

CREATE CONSTRAINT TRIGGER ledger_derived_state AFTER INSERT ON portfolio_ledger_transactions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_derived_state();

CREATE CONSTRAINT TRIGGER snapshot_derived_state AFTER INSERT ON portfolio_snapshots DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_derived_state();

DO $$ DECLARE role_row record; BEGIN SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, rolbypassrls, rolcanlogin INTO role_row FROM pg_roles WHERE rolname = 'tradingagents_reservation_api'; IF NOT FOUND THEN CREATE ROLE tradingagents_reservation_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS; ELSIF role_row.rolsuper OR role_row.rolcreatedb OR role_row.rolcreaterole OR role_row.rolinherit OR role_row.rolreplication OR role_row.rolbypassrls OR role_row.rolcanlogin THEN RAISE EXCEPTION 'existing tradingagents_reservation_api role has unsafe attributes'; END IF; IF EXISTS (SELECT 1 FROM pg_auth_members membership JOIN pg_roles guard ON guard.rolname = 'tradingagents_reservation_api' WHERE membership.roleid = guard.oid OR membership.member = guard.oid) THEN RAISE EXCEPTION 'tradingagents_reservation_api must have no memberships'; END IF; END $$;

CREATE SCHEMA IF NOT EXISTS tradingagents_private AUTHORIZATION tradingagents_reservation_api;

ALTER SCHEMA tradingagents_private OWNER TO tradingagents_reservation_api;

REVOKE ALL ON SCHEMA tradingagents_private FROM PUBLIC;

REVOKE CREATE ON SCHEMA public FROM PUBLIC, tradingagents_app, tradingagents_worker;

GRANT USAGE ON SCHEMA public, tradingagents_private TO tradingagents_reservation_api;

GRANT USAGE ON SCHEMA tradingagents_private TO tradingagents_app;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON public.portfolio_cash_reservations FROM tradingagents_app, tradingagents_worker, PUBLIC;

GRANT SELECT ON public.identity_sessions, public.identity_memberships, public.identity_users, public.identity_tenants, public.portfolios, public.portfolio_mandates, public.portfolio_suitability_assessments, public.portfolio_ledger_postings, public.portfolio_snapshots, public.portfolio_circuit_breakers, public.portfolio_cash_reservations TO tradingagents_reservation_api;

GRANT INSERT, UPDATE ON public.portfolio_cash_reservations TO tradingagents_reservation_api;

GRANT UPDATE (balance_version) ON public.portfolios TO tradingagents_reservation_api;

CREATE POLICY "identity_sessions_reservation_api" ON public."identity_sessions" FOR SELECT TO tradingagents_reservation_api USING (id = NULLIF(current_setting('app.portfolio_session_id', true), '')::uuid);

CREATE POLICY "identity_memberships_reservation_api" ON public."identity_memberships" FOR SELECT TO tradingagents_reservation_api USING (EXISTS (SELECT 1 FROM public.identity_sessions scoped_session WHERE scoped_session.id = NULLIF(current_setting('app.portfolio_session_id', true), '')::uuid AND scoped_session.membership_id = identity_memberships.id));

CREATE POLICY "identity_users_reservation_api" ON public."identity_users" FOR SELECT TO tradingagents_reservation_api USING (EXISTS (SELECT 1 FROM public.identity_sessions scoped_session WHERE scoped_session.id = NULLIF(current_setting('app.portfolio_session_id', true), '')::uuid AND scoped_session.user_id = identity_users.id));

CREATE POLICY "identity_tenants_reservation_api" ON public."identity_tenants" FOR SELECT TO tradingagents_reservation_api USING (EXISTS (SELECT 1 FROM public.identity_sessions scoped_session WHERE scoped_session.id = NULLIF(current_setting('app.portfolio_session_id', true), '')::uuid AND scoped_session.tenant_id = identity_tenants.id));

CREATE POLICY "portfolio_mandates_reservation_api" ON public."portfolio_mandates" FOR SELECT TO tradingagents_reservation_api USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY "portfolio_suitability_assessments_reservation_api" ON public."portfolio_suitability_assessments" FOR SELECT TO tradingagents_reservation_api USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY "portfolio_ledger_postings_reservation_api" ON public."portfolio_ledger_postings" FOR SELECT TO tradingagents_reservation_api USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY "portfolio_snapshots_reservation_api" ON public."portfolio_snapshots" FOR SELECT TO tradingagents_reservation_api USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY "portfolio_circuit_breakers_reservation_api" ON public."portfolio_circuit_breakers" FOR SELECT TO tradingagents_reservation_api USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY portfolios_reservation_api ON public.portfolios FOR ALL TO tradingagents_reservation_api USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY cash_reservations_reservation_api ON public.portfolio_cash_reservations FOR ALL TO tradingagents_reservation_api USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE FUNCTION tradingagents_private.portfolio_reserve_cash_v1(p_portfolio_id uuid, p_amount numeric, p_purpose_digest text, p_idempotency_key_hash text, p_ttl_seconds integer) RETURNS public.portfolio_cash_reservations LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog SET row_security = on AS $$ DECLARE v_session uuid; v_tenant uuid; v_user uuid; v_membership uuid; v_role text; v_now timestamptz := clock_timestamp(); v_portfolio public.portfolios%ROWTYPE; v_mandate public.portfolio_mandates%ROWTYPE; v_suitability public.portfolio_suitability_assessments%ROWTYPE; v_latest_mandate uuid; v_existing public.portfolio_cash_reservations%ROWTYPE; v_created public.portfolio_cash_reservations%ROWTYPE; v_cash numeric; v_reserved numeric; BEGIN BEGIN v_session := NULLIF(current_setting('app.portfolio_session_id', true), '')::uuid; EXCEPTION WHEN others THEN RAISE EXCEPTION 'PORTFOLIO_SESSION_REQUIRED' USING ERRCODE = 'P0001'; END; SELECT s.tenant_id, s.user_id, s.membership_id, m.role INTO v_tenant, v_user, v_membership, v_role FROM public.identity_sessions s JOIN public.identity_memberships m ON m.id = s.membership_id AND m.tenant_id = s.tenant_id AND m.user_id = s.user_id JOIN public.identity_users u ON u.id = s.user_id JOIN public.identity_tenants t ON t.id = s.tenant_id WHERE s.id = v_session AND s.revoked_at IS NULL AND s.expires_at > v_now AND m.is_active AND u.is_active AND t.is_active; IF NOT FOUND OR v_role NOT IN ('owner','admin','operator') OR v_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid THEN RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE = 'P0001'; END IF; IF p_amount IS NULL OR p_amount <= 0 OR p_amount::text IN ('NaN','Infinity','-Infinity') OR p_amount IS DISTINCT FROM round(p_amount, 12) THEN RAISE EXCEPTION 'RESERVATION_AMOUNT_INVALID' USING ERRCODE = 'P0001'; END IF; IF p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 30 AND 3600 THEN RAISE EXCEPTION 'RESERVATION_TTL_INVALID' USING ERRCODE = 'P0001'; END IF; IF p_purpose_digest IS NULL OR p_purpose_digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'PURPOSE_DIGEST_INVALID' USING ERRCODE = 'P0001'; END IF; IF p_idempotency_key_hash IS NULL OR p_idempotency_key_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'IDEMPOTENCY_KEY_INVALID' USING ERRCODE = 'P0001'; END IF; SELECT * INTO v_portfolio FROM public.portfolios WHERE tenant_id = v_tenant AND id = p_portfolio_id FOR UPDATE; IF NOT FOUND OR v_portfolio.status <> 'active' THEN RAISE EXCEPTION 'PORTFOLIO_NOT_ACTIVE' USING ERRCODE = 'P0001'; END IF; SELECT id INTO v_latest_mandate FROM public.portfolio_mandates WHERE tenant_id = v_tenant AND portfolio_id = p_portfolio_id ORDER BY version DESC LIMIT 1; SELECT * INTO v_mandate FROM public.portfolio_mandates WHERE tenant_id = v_tenant AND portfolio_id = p_portfolio_id AND id = v_portfolio.current_mandate_id; SELECT * INTO v_suitability FROM public.portfolio_suitability_assessments WHERE tenant_id = v_tenant AND user_id = v_portfolio.owner_user_id ORDER BY version DESC LIMIT 1; IF v_mandate.id IS NULL OR v_mandate.id IS DISTINCT FROM v_latest_mandate OR v_mandate.status <> 'active' OR v_mandate.mode NOT IN ('backtest','shadow','paper') OR v_mandate.effective_at > v_now OR v_mandate.ended_at IS NOT NULL OR v_suitability.id IS NULL OR v_mandate.suitability_id IS DISTINCT FROM v_suitability.id OR v_suitability.status <> 'valid' OR v_suitability.assessed_at > v_now OR v_suitability.expires_at <= v_now OR (CASE v_mandate.profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END) > (CASE v_suitability.effective_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END) THEN RAISE EXCEPTION 'ACTIVE_MANDATE_REQUIRED' USING ERRCODE = 'P0001'; END IF; IF NOT EXISTS (SELECT 1 FROM public.portfolio_snapshots WHERE tenant_id = v_tenant AND portfolio_id = p_portfolio_id) THEN RAISE EXCEPTION 'BASELINE_SNAPSHOT_REQUIRED' USING ERRCODE = 'P0001'; END IF; IF EXISTS (SELECT 1 FROM public.portfolio_circuit_breakers WHERE tenant_id = v_tenant AND portfolio_id = p_portfolio_id AND status = 'open') THEN RAISE EXCEPTION 'CIRCUIT_BREAKER_OPEN' USING ERRCODE = 'P0001'; END IF; SELECT * INTO v_existing FROM public.portfolio_cash_reservations WHERE tenant_id = v_tenant AND portfolio_id = p_portfolio_id AND idempotency_key_hash = p_idempotency_key_hash FOR UPDATE; IF FOUND THEN IF v_existing.amount IS DISTINCT FROM p_amount OR v_existing.purpose_digest IS DISTINCT FROM p_purpose_digest THEN RAISE EXCEPTION 'IDEMPOTENCY_PAYLOAD_MISMATCH' USING ERRCODE = 'P0001'; END IF; IF v_existing.status <> 'active' OR v_existing.expires_at <= v_now OR v_existing.mandate_id IS DISTINCT FROM v_mandate.id THEN RAISE EXCEPTION 'IDEMPOTENCY_RESERVATION_FINALIZED' USING ERRCODE = 'P0001'; END IF; RETURN v_existing; END IF; SELECT COALESCE(sum(quantity), 0) INTO v_cash FROM public.portfolio_ledger_postings WHERE tenant_id = v_tenant AND portfolio_id = p_portfolio_id AND account = 'cash' AND asset = v_portfolio.base_currency; SELECT COALESCE(sum(amount), 0) INTO v_reserved FROM public.portfolio_cash_reservations WHERE tenant_id = v_tenant AND portfolio_id = p_portfolio_id AND status = 'active' AND expires_at > v_now; IF p_amount > v_cash - v_reserved THEN RAISE EXCEPTION 'INSUFFICIENT_AVAILABLE_CASH' USING ERRCODE = 'P0001'; END IF; INSERT INTO public.portfolio_cash_reservations (id, tenant_id, portfolio_id, mandate_id, idempotency_key_hash, currency, amount, status, purpose_digest, expires_at, created_at) VALUES (gen_random_uuid(), v_tenant, p_portfolio_id, v_mandate.id, p_idempotency_key_hash, v_portfolio.base_currency, p_amount, 'active', p_purpose_digest, v_now + make_interval(secs => p_ttl_seconds), v_now) RETURNING * INTO v_created; UPDATE public.portfolios SET balance_version = balance_version + 1 WHERE tenant_id = v_tenant AND id = p_portfolio_id; RETURN v_created; END; $$;

ALTER FUNCTION tradingagents_private.portfolio_reserve_cash_v1(uuid,numeric,text,text,integer) OWNER TO tradingagents_reservation_api;

CREATE FUNCTION tradingagents_private.portfolio_release_cash_v1(p_reservation_id uuid) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog SET row_security = on AS $$ DECLARE v_session uuid; v_tenant uuid; v_role text; v_portfolio uuid; v_status text; v_now timestamptz := clock_timestamp(); BEGIN BEGIN v_session := NULLIF(current_setting('app.portfolio_session_id', true), '')::uuid; EXCEPTION WHEN others THEN RAISE EXCEPTION 'PORTFOLIO_SESSION_REQUIRED' USING ERRCODE = 'P0001'; END; SELECT s.tenant_id, m.role INTO v_tenant, v_role FROM public.identity_sessions s JOIN public.identity_memberships m ON m.id = s.membership_id AND m.tenant_id = s.tenant_id AND m.user_id = s.user_id JOIN public.identity_users u ON u.id = s.user_id JOIN public.identity_tenants t ON t.id = s.tenant_id WHERE s.id = v_session AND s.revoked_at IS NULL AND s.expires_at > v_now AND m.is_active AND u.is_active AND t.is_active; IF NOT FOUND OR v_role NOT IN ('owner','admin','operator') OR v_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid THEN RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE = 'P0001'; END IF; SELECT portfolio_id INTO v_portfolio FROM public.portfolio_cash_reservations WHERE tenant_id = v_tenant AND id = p_reservation_id; IF NOT FOUND THEN RETURN NULL; END IF; PERFORM 1 FROM public.portfolios WHERE tenant_id = v_tenant AND id = v_portfolio FOR UPDATE; SELECT status INTO v_status FROM public.portfolio_cash_reservations WHERE tenant_id = v_tenant AND portfolio_id = v_portfolio AND id = p_reservation_id FOR UPDATE; IF v_status = 'active' THEN UPDATE public.portfolio_cash_reservations SET status = 'released', released_at = v_now WHERE tenant_id = v_tenant AND portfolio_id = v_portfolio AND id = p_reservation_id; UPDATE public.portfolios SET balance_version = balance_version + 1 WHERE tenant_id = v_tenant AND id = v_portfolio; RETURN 'released'; END IF; RETURN v_status; END; $$;

ALTER FUNCTION tradingagents_private.portfolio_release_cash_v1(uuid) OWNER TO tradingagents_reservation_api;

REVOKE ALL ON FUNCTION tradingagents_private.portfolio_reserve_cash_v1(uuid,numeric,text,text,integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION tradingagents_private.portfolio_reserve_cash_v1(uuid,numeric,text,text,integer) TO tradingagents_app;

REVOKE ALL ON FUNCTION tradingagents_private.portfolio_release_cash_v1(uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION tradingagents_private.portfolio_release_cash_v1(uuid) TO tradingagents_app;

ALTER DEFAULT PRIVILEGES FOR ROLE tradingagents_reservation_api IN SCHEMA tradingagents_private REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

UPDATE alembic_version SET version_num='20260908_0020' WHERE alembic_version.version_num = '20260908_0019';

COMMIT;
