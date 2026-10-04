"""Create or update the least-privilege role the app connects as.

Run as the database OWNER (the role that owns the database and the
applicants table, and that ran load_data.py), after the table exists:

    DB_USER=<owner> DB_PASSWORD= python src/setup_roles.py

The DB_* settings (environment first, then module_5/.env) say where to
connect as the owner; APP_DB_USER / APP_DB_PASSWORD name the app role to
create. Afterwards, point the app's DB_USER / DB_PASSWORD at the app role.

The app role can only read and add applicant rows:

- role attributes: LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  NOBYPASSRLS NOINHERIT
- CONNECT on the database (PUBLIC's default CONNECT/TEMPORARY is revoked,
  so the role can't create temporary tables either)
- USAGE (not CREATE) on schema public
- SELECT and INSERT on applicants, and USAGE on its p_id sequence (needed
  for SERIAL inserts); nothing else: no UPDATE, DELETE, TRUNCATE,
  REFERENCES, TRIGGER or CREATE

Every run revokes the role's existing privileges on those objects before
granting, and re-applies the attributes and password, so it is idempotent
and always leaves exactly the privileges above.
"""

import sys
from dataclasses import dataclass

import psycopg2
from psycopg2 import sql

from config import ConfigError, get_app_role, psycopg2_dsn
from load_data import TABLE_MISSING_MESSAGE, TableMissingError, table_exists_query
from sql_utils import APPLICANTS_TABLE, SINGLE_ROW, clamp_limit

ROLE_ATTRIBUTES = sql.SQL(
    "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
)
PUBLIC_SCHEMA = "public"
SERIAL_COLUMN = "p_id"
PASSWORD_MASK = "********"


@dataclass(frozen=True)
class CatalogState:
    """What setup needs to know about the database, looked up live as the owner.

    Attributes:
        role_exists: Whether the app role already exists (ALTER instead of CREATE).
        sequence: (schema, name) of the table's SERIAL sequence, from
            pg_get_serial_sequence() rather than a hard-coded name.
        public_can_create: Whether PUBLIC still has CREATE on schema public
            (PostgreSQL 15+ removed it by default; older servers need a REVOKE).
    """

    role_exists: bool
    sequence: tuple
    public_can_create: bool


def role_exists_query(role):
    """SELECT whether a role named `role` exists."""
    stmt = sql.SQL("SELECT 1 FROM pg_roles WHERE rolname = %s LIMIT %s")
    return stmt, [role, clamp_limit(SINGLE_ROW)]


def serial_sequence_query(table):
    """SELECT (schema, name) of table's p_id sequence, via pg_get_serial_sequence()."""
    stmt = sql.SQL(
        "SELECT n.nspname, c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.oid = pg_get_serial_sequence(%s, %s)::regclass LIMIT %s"
    )
    return stmt, [table, SERIAL_COLUMN, clamp_limit(SINGLE_ROW)]


def public_create_query():
    """SELECT whether PUBLIC may CREATE objects in schema public."""
    stmt = sql.SQL("SELECT has_schema_privilege(%s, %s, %s) LIMIT %s")
    return stmt, ["public", PUBLIC_SCHEMA, "CREATE", clamp_limit(SINGLE_ROW)]


def read_catalog(cur, role, table=APPLICANTS_TABLE):
    """Look up the CatalogState for `role` and `table` on cur's database."""
    stmt, params = role_exists_query(role)
    cur.execute(stmt, params)
    role_exists = cur.fetchone() is not None

    stmt, params = serial_sequence_query(table)
    cur.execute(stmt, params)
    sequence = tuple(cur.fetchone())

    stmt, params = public_create_query()
    cur.execute(stmt, params)
    public_can_create = cur.fetchone()[0]

    return CatalogState(role_exists, sequence, public_can_create)


def build_role_statements(role, password, dbname, table=APPLICANTS_TABLE, *, catalog):
    """Return the statements that create/update `role` with exactly the app's privileges.

    Names (role, database, schema, table, sequence) are sql.Identifier. The
    password is sql.Literal: CREATE/ALTER ROLE and GRANT are utility (DDL)
    statements, which PostgreSQL can't prepare with bind parameters, so a
    %s placeholder isn't possible here; sql.Literal is psycopg2's quoting for
    exactly this case. Nothing is executed; see apply_role_setup().
    """
    role_id = sql.Identifier(role)
    db_id = sql.Identifier(dbname)
    schema_id = sql.Identifier(PUBLIC_SCHEMA)
    table_id = sql.Identifier(table)
    sequence_id = sql.Identifier(*catalog.sequence)

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
        sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(table_id, role_id),
        sql.SQL("GRANT SELECT, INSERT ON TABLE {} TO {}").format(table_id, role_id),
        sql.SQL("REVOKE ALL ON SEQUENCE {} FROM {}").format(sequence_id, role_id),
        sql.SQL("GRANT USAGE ON SEQUENCE {} TO {}").format(sequence_id, role_id),
    ]
    return statements


def apply_role_setup(conn, role, password, table=APPLICANTS_TABLE):
    """Create/update `role` on conn's database in one transaction (conn must be the owner).

    Returns the same statements built with the password masked, for display.
    Raises TableMissingError if the table doesn't exist yet.
    """
    dbname = conn.info.dbname
    with conn:
        with conn.cursor() as cur:
            stmt, params = table_exists_query()
            cur.execute(stmt, params)
            if cur.fetchone()[0] is None:
                raise TableMissingError(f"{TABLE_MISSING_MESSAGE} before running setup_roles.py")
            catalog = read_catalog(cur, role, table)
            for statement in build_role_statements(role, password, dbname, table, catalog=catalog):
                cur.execute(statement)
    return build_role_statements(role, PASSWORD_MASK, dbname, table, catalog=catalog)


def main():
    """CLI: set up APP_DB_USER as the owner (DB_*); print what ran, password masked."""
    try:
        role, password = get_app_role()
        conn = psycopg2.connect(psycopg2_dsn())
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
    except psycopg2.Error as exc:  # e.g. not the owner: "must be owner of table applicants"
        print(f"ROLE SETUP FAILED: {str(exc).strip().splitlines()[0]}")
        print("Run setup_roles.py as the database owner (DB_USER/DB_PASSWORD).")
        sys.exit(1)
    finally:
        conn.close()

    print(f"Role setup for {role} on database {dbname}, run as {owner}:")
    for line in lines:
        print(f"  {line}")


if __name__ == "__main__":
    main()
