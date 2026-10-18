-- Run as the Goaletto DB owner only, never against a shared project database.
DO $access$
BEGIN
 IF current_database() <> 'goaletto' OR to_regprocedure(
   'goaletto_operator.report(text,timestamptz,timestamptz,jsonb)') IS NULL THEN
   RAISE EXCEPTION 'Expected Goaletto operator schema'; END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='goaletto_operator_reader') THEN
   CREATE ROLE goaletto_operator_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
     NOREPLICATION NOBYPASSRLS NOINHERIT;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='goaletto_operator_reader'
     AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolcanlogin))
 OR EXISTS (SELECT 1 FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.member
     WHERE r.rolname='goaletto_operator_reader')
 OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relkind IN ('r','v','m','p')
     AND has_table_privilege('goaletto_operator_reader',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'))
 OR has_schema_privilege('goaletto_operator_reader','public','CREATE') THEN
   RAISE EXCEPTION 'Existing operator role has unexpected privileges'; END IF;
END
$access$;
GRANT USAGE ON SCHEMA goaletto_operator TO goaletto_operator_reader;
GRANT EXECUTE ON FUNCTION goaletto_operator.report(text,timestamptz,timestamptz,jsonb)
 TO goaletto_operator_reader;
