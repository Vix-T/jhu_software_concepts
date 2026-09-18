"""SQLAlchemy ORM mapping for the existing `applicants` table.

This maps to the same PostgreSQL database and `applicants` table that
load_data.py creates and populates -- it does not create a second copy
of the data or a second table. Schema management (CREATE TABLE) stays
in load_data.py; this module only defines the mapping and connection
for querying via the ORM.
"""

import os
from datetime import date

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()

DATABASE_URL = (
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Applicant(Base):
    __tablename__ = "applicants"

    p_id: Mapped[int] = mapped_column(primary_key=True)
    program: Mapped[str | None] = mapped_column()
    comments: Mapped[str | None] = mapped_column()
    date_added: Mapped[date | None] = mapped_column()
    url: Mapped[str | None] = mapped_column()
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
