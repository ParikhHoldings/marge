"""
Database setup for Marge.

Uses SQLite for local development and Postgres for production.
Controlled by the DATABASE_URL environment variable:
  - SQLite:   sqlite:///./marge.db
  - Postgres: postgresql://user:pass@host/dbname
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./marge.db")

# SQLite needs check_same_thread=False; Postgres does not
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a database session and closes it when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Called on app startup."""
    # Import models so they register with Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
