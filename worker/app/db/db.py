import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool
from config import config
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from typing import Optional, Any

engine: Optional[Engine] = None
SessionLocal: Optional[Session] = None
Base: Optional[Any] = declarative_base()

def initialize_db(db_path: str = config.SQLITE_PATH, wipe_db: bool = False):
    global engine, SessionLocal, Base

    # wipe out the db file if wipe_db is True
    if wipe_db and os.path.exists(db_path):
        os.remove(db_path)

    # SQLite URL
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{db_path}"

    # check_same_thread=False for SQLite in FastAPI context; StaticPool avoids oddities in dev
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    from . import models  # noqa: F401  (ensure models are imported)
    
def get_db() -> Session:
    global engine, SessionLocal, Base
    if not engine:
        raise RuntimeError("Database not initialized")
    if not SessionLocal:
        raise RuntimeError("SessionLocal not initialized")
    return SessionLocal()
