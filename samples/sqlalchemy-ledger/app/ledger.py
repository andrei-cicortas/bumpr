"""Double-entry style ledger used by the billing service."""
from sqlalchemy import Column, ForeignKey, Integer, Numeric, String, create_engine, func, select
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base = declarative_base()


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    entries = relationship("Entry", back_populates="account")


class Entry(Base):
    __tablename__ = "entries"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    memo = Column(String(200))
    account = relationship("Account", back_populates="entries")


def make_engine(url="sqlite://"):
    return create_engine(url, future=False)


def init_db(engine):
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def get_account(session, account_id):
    return session.query(Account).get(account_id)


def balance(session, account_id):
    total = session.query(func.sum(Entry.amount)).filter(Entry.account_id == account_id).scalar()
    return float(total or 0)


def top_accounts(engine, limit=3):
    stmt = (
        select([Account.name, func.sum(Entry.amount).label("total")])
        .select_from(Account)
        .join(Entry)
        .group_by(Account.name)
        .order_by(func.sum(Entry.amount).desc())
        .limit(limit)
    )
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt)]


def raw_totals(engine):
    rows = engine.execute(
        "SELECT a.name AS name, COUNT(e.id) AS n FROM accounts a "
        "LEFT JOIN entries e ON e.account_id = a.id GROUP BY a.name ORDER BY a.name"
    )
    return {row["name"]: row["n"] for row in rows}


def record(engine, account_name, amount, memo=None):
    with engine.begin() as conn:
        account_id = conn.execute(
            select([Account.id]).where(Account.name == account_name)
        ).scalar()
        conn.execute(Entry.__table__.insert(), {"account_id": account_id, "amount": amount, "memo": memo})
