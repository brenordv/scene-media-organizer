-- Fresh install: create the login role and the database scene-media-organizer uses.
--
-- Run as a superuser, from a terminal, so psql can prompt for the new role's password
-- (\password sends it encrypted; it never appears in this file, the shell history or the server log):
--
--   psql -v ON_ERROR_STOP=1 -h <host> -U postgres -d postgres -f sql/create_app_role.sql
--
-- Optional variables: -v app_role=<name> (default media_organizer), -v app_db=<name> (default smo_watchdog),
-- -v set_password=1 to set a new password when the role already exists (any value turns it on; this is also the fix
-- when an earlier run was interrupted at the password prompt, leaving the role without a password).
-- Safe to run again: an existing role or database is left in place.
--
-- The role owns the database, so it governs the public schema (PostgreSQL 15+ gives that schema to the database
-- owner) and can create the tables the application's migrations need, without superuser rights. Owning the database
-- also lets the role rename or drop it. Requires PostgreSQL 13 or newer.

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
  \echo The role already exists; its password is unchanged.
  \if :{?set_password}
    \echo Enter the new password for the role:
    \password :app_role
  \endif
\endif

SELECT NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db') AS need_db \gset
\if :need_db
  SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_role') \gexec
  -- Only the owning role (and superusers) may connect to the new database.
  SELECT format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', :'app_db') \gexec
\else
  \echo The database already exists and was left unchanged; for an existing install use sql/grant_existing_database.sql.
\endif
