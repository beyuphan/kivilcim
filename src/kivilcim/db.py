from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from kivilcim.models import Base


def make_engine(url: str) -> Engine:
    return create_engine(url)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
