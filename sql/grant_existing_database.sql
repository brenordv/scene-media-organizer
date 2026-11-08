-- Existing install: give a dedicated login role the database that 2.x created (usually as the postgres user).
--
-- Run this before the first 3.0.0 start: 3.0.0's migrations alter these tables, which takes ownership. Stop the 2.x
-- service first (changing a table's owner locks it), then run as a superuser, from a terminal, so psql can prompt for
-- the role's password:
--
--   psql -v ON_ERROR_STOP=1 -h <host> -U postgres -d postgres -f sql/grant_existing_database.sql
--
-- Optional variables (the switches below are on whenever the variable is set, whatever its value):
--   -v app_role=<name>      the role (default media_organizer); created when missing
--   -v app_db=<name>        the existing database (default smo_watchdog)
--   -v set_password=1       set a new password when the role already exists (also the fix when an earlier run was
--                           interrupted at the password prompt, leaving the role without one)
--   -v revoke_public=1      stop every other non-superuser role from connecting to the database; leave it off if
--                           something else (a dashboard, another login) reads this database
--   -v pg14_public_schema=1 for a cluster upgraded from PostgreSQL 14 or older, where everyone may still create
--                           objects in public: take that right away and give the schema to the role
-- Safe to run again.

\set ON_ERROR_STOP on

\if :{?app_role}
\else
  \set app_role media_organizer
\endif
\if :{?app_db}
\else
  \set app_db smo_watchdog
\endif

SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role') AS need_role \gset
\if :need_role
  SELECT format('CREATE ROLE %I LOGIN', :'app_role') \gexec
  \echo Enter the password for the new role:
  \password :app_role
\else
  \if :{?set_password}
    \echo Enter the new password for the role:
    \password :app_role
  \endif
\endif

-- Owning the database lets the role rename or drop it, and on PostgreSQL 15+ makes it the owner of the public schema.
SELECT format('ALTER DATABASE %I OWNER TO %I', :'app_db', :'app_role') \gexec

\connect :app_db

-- The migrations create the schema_migrations ledger and indexes in public, which needs CREATE there.
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'app_role') \gexec

SELECT format('ALTER TABLE IF EXISTS %I OWNER TO %I', table_name, :'app_role')
FROM unnest(ARRAY['work_queue', 'batch_control', 'activity_tracker']) AS table_name \gexec

\if :{?revoke_public}
  SELECT format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', :'app_db') \gexec
\endif

\if :{?pg14_public_schema}
  REVOKE CREATE ON SCHEMA public FROM PUBLIC;
  SELECT format('ALTER SCHEMA public OWNER TO %I', :'app_role') \gexec
\endif

\echo Done. Set MEDIA_ORGANIZER_POSTGRES_CONNECTION_STRING in Infisical to connect as this role.
