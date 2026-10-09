"""Create or update the read-only role the web service connects as.

The worker applies it at startup (consumer.prepare_database), connected as
the database OWNER, once initialize_database() has created the tables --
whenever WEB_DB_USER and WEB_DB_PASSWORD are set. It can also be run by hand,
as the owner:

    DATABASE_URL=postgresql://<owner>@HOST:PORT/DBNAME \\
    WEB_DB_USER=gradcafe_web WEB_DB_PASSWORD=... python src/db/setup_roles.py

The web role can only read:

- role attributes: LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  NOBYPASSRLS NOINHERIT
- CONNECT on the database (PUBLIC's default CONNECT/TEMPORARY is revoked,
  so the role can't create temporary tables either)
- USAGE (not CREATE) on schema public
- SELECT on applicants, analysis_summary and ingestion_watermarks; nothing
  else: no INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES or TRIGGER, no
  sequence privileges, and no CREATE anywhere

Every run first revokes the role's privileges on the database, the schema,
and every table and sequence in it, then re-applies the attributes,
password and grants above, so it is idempotent and always leaves exactly
those privileges (a stray grant from earlier is removed).
"""

import os
import sys
from dataclasses import dataclass

import psycopg2
from psycopg2 import sql

from load_data import (
    ALL_TABLES,
    ConfigError,
    TableMissingError,
    connect,
    init_lock_query,
    missing_tables,
)
from sql_utils import SINGLE_ROW, clamp_limit

ROLE_ATTRIBUTES = sql.SQL(
    "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
)
PUBLIC_SCHEMA = "public"
PASSWORD_MASK = "********"
# The web role to create, and its password (read from the environment).
WEB_ROLE_VARS = ("WEB_DB_USER", "WEB_DB_PASSWORD")
# The tables the web role may read: everything the web service queries.
READ_TABLES = ALL_TABLES


def web_role_from_env():
    """(WEB_DB_USER, WEB_DB_PASSWORD), or None when neither is set.

    Raises ConfigError if only one of them is set (or one is empty): a half
    configured role is a mistake, not a request to skip it.
    """
    values = {name: os.environ.get(name, "").strip() for name in WEB_ROLE_VARS}
    if not any(values.values()):
        return None
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(
            f"Missing web role setting(s): {', '.join(missing)}. "
            "Set both WEB_DB_USER and WEB_DB_PASSWORD, or neither (see .env.example)."
        )
    return values["WEB_DB_USER"], values["WEB_DB_PASSWORD"]


@dataclass(frozen=True)
class CatalogState:
    """What setup needs to know about the database, looked up live as the owner.

    Attributes:
        role_exists: Whether the web role already exists (ALTER instead of CREATE).
        public_can_create: Whether PUBLIC still has CREATE on schema public
            (PostgreSQL 15+ removed it by default; older servers need a REVOKE).
    """

    role_exists: bool
    public_can_create: bool


def role_exists_query(role):
    """SELECT whether a role named `role` exists."""
    stmt = sql.SQL("SELECT 1 FROM pg_roles WHERE rolname = %s LIMIT %s")
    return stmt, [role, clamp_limit(SINGLE_ROW)]


def public_create_query():
    """SELECT whether PUBLIC may CREATE objects in schema public."""
    stmt = sql.SQL("SELECT has_schema_privilege(%s, %s, %s) LIMIT %s")
    return stmt, ["public", PUBLIC_SCHEMA, "CREATE", clamp_limit(SINGLE_ROW)]


def read_catalog(cur, role):
    """Look up the CatalogState for `role` on cur's database."""
    stmt, params = role_exists_query(role)
    cur.execute(stmt, params)
    role_exists = cur.fetchone() is not None

    stmt, params = public_create_query()
    cur.execute(stmt, params)
    public_can_create = cur.fetchone()[0]

    return CatalogState(role_exists, public_can_create)


def build_role_statements(role, password, dbname, *, catalog, tables=READ_TABLES):
    """Return the statements that create/update `role` with exactly the web's privileges.

    Names (role, database, schema, tables) are sql.Identifier. The password
    is sql.Literal: CREATE/ALTER ROLE and GRANT are utility (DDL) statements,
    which PostgreSQL can't prepare with bind parameters, so a %s placeholder
    isn't possible here; sql.Literal is psycopg2's quoting for exactly this
    case. Nothing is executed; see apply_role_setup().
    """
    role_id = sql.Identifier(role)
    db_id = sql.Identifier(dbname)
    schema_id = sql.Identifier(PUBLIC_SCHEMA)
    table_ids = sql.SQL(", ").join(sql.Identifier(table) for table in tables)

    verb = sql.SQL("ALTER" if catalog.role_exists else "CREATE")
    statements = [
        sql.SQL("{} ROLE {} WITH {} PASSWORD {}").format(
            verb, role_id, ROLE_ATTRIBUTES, sql.Literal(password)
        )
    ]
    if catalog.public_can_create:
        statements.append(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM PUBLIC").format(schema_id))
    statements += [
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db_id),
        sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(db_id, role_id),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db_id, role_id),
        sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(schema_id, role_id),
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema_id, role_id),
        sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(schema_id, role_id),
        sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(schema_id, role_id),
        sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(table_ids, role_id),
    ]
    return statements


def apply_role_setup(conn, role, password):
    """Create/update `role` on conn's database in one transaction (conn must be the owner).

    Takes the same advisory lock as load_data.initialize_database(), so two
    workers starting at once can't both try to CREATE the role. Returns the
    statements built with the password masked, for display. Raises
    TableMissingError (nothing changed) if any READ_TABLES table doesn't exist yet.
    """
    dbname = conn.info.dbname
    with conn:
        with conn.cursor() as cur:
            cur.execute(*init_lock_query())
            missing = missing_tables(cur, READ_TABLES)
            if missing:
                raise TableMissingError(
                    f"table(s) missing: {', '.join(missing)}; start the worker "
                    "(it creates them) before setting up the web role"
                )
            catalog = read_catalog(cur, role)
            for statement in build_role_statements(role, password, dbname, catalog=catalog):
                cur.execute(statement)
    return build_role_statements(role, PASSWORD_MASK, dbname, catalog=catalog)


def main():
    """CLI: set up WEB_DB_USER as the owner ($DATABASE_URL); print what ran, password masked."""
    try:
        settings = web_role_from_env()
        if settings is None:
            raise ConfigError("WEB_DB_USER and WEB_DB_PASSWORD are not set (see .env.example).")
        role, password = settings
        conn = connect()
    except (ConfigError, psycopg2.OperationalError) as exc:
        print(f"ROLE SETUP FAILED: {str(exc).strip()}")
        sys.exit(1)

    try:
        masked = apply_role_setup(conn, role, password)
        lines = [f"{statement.as_string(conn)};" for statement in masked]
        owner, dbname = conn.info.user, conn.info.dbname
    except TableMissingError as exc:
        print(f"ROLE SETUP FAILED: {exc}")
        sys.exit(1)
    except psycopg2.Error as exc:  # e.g. not the owner: "permission denied to create role"
        print(f"ROLE SETUP FAILED: {str(exc).strip().splitlines()[0]}")
        print("Run setup_roles.py as the database owner (DATABASE_URL).")
        sys.exit(1)
    finally:
        conn.close()

    print(f"Role setup for {role} on database {dbname}, run as {owner}:")
    for line in lines:
        print(f"  {line}")


if __name__ == "__main__":
    main()
