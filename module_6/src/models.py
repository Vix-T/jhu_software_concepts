"""SQLAlchemy ORM mapping for the existing `applicants` table.

This maps to the same PostgreSQL database and `applicants` table that
load_data.py creates and populates -- it does not create a second copy
of the data or a second table. Schema management (CREATE TABLE) stays
in load_data.py; this module only defines the mapping and connection
for querying via the ORM.

No engine is created at import time: callers build a session factory with
make_session_factory(), which reads the DB_* settings via config.py unless
a URL is passed in explicitly (e.g. by the Flask app factory or by tests).
"""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from config import get_db_url


def make_session_factory(database_url=None):
    """Return a sessionmaker bound to database_url (default: the DB_* settings)."""
    engine = create_engine(database_url or get_db_url())
    return sessionmaker(bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for the Module 6 ORM models."""

    @classmethod
    def column_names(cls):
        """The mapped table's column names, in table order."""
        return [column.name for column in cls.__table__.columns]

    def to_dict(self):
        """This row as a dict keyed by column name, in table order."""
        return {name: getattr(self, name) for name in self.column_names()}


class Applicant(Base):
    """One row of the applicants table (schema owned by load_data.CREATE_TABLE_SQL)."""

    __tablename__ = "applicants"

    p_id: Mapped[int] = mapped_column(primary_key=True)
    program: Mapped[str | None] = mapped_column()
    comments: Mapped[str | None] = mapped_column()
    date_added: Mapped[date | None] = mapped_column()
    url: Mapped[str | None] = mapped_column(unique=True)
    status: Mapped[str | None] = mapped_column()
    term: Mapped[str | None] = mapped_column()
    us_or_international: Mapped[str | None] = mapped_column()
    gpa: Mapped[float | None] = mapped_column()
    gre: Mapped[float | None] = mapped_column()
    gre_v: Mapped[float | None] = mapped_column()
    gre_aw: Mapped[float | None] = mapped_column()
    degree: Mapped[str | None] = mapped_column()
    llm_generated_program: Mapped[str | None] = mapped_column()
    llm_generated_university: Mapped[str | None] = mapped_column()
