import pytest

from app import ledger


@pytest.fixture
def setup():
    engine = ledger.make_engine()
    Session = ledger.init_db(engine)
    session = Session()
    session.add_all([ledger.Account(id=1, name="cash"), ledger.Account(id=2, name="bank"), ledger.Account(id=3, name="fees")])
    session.commit()
    for name, amount in [("cash", 100), ("cash", -30), ("bank", 500), ("bank", 250), ("fees", 5)]:
        ledger.record(engine, name, amount)
    yield engine, session
    session.close()


def test_get_account(setup):
    _, session = setup
    assert ledger.get_account(session, 2).name == "bank"
    assert ledger.get_account(session, 99) is None


def test_balance(setup):
    _, session = setup
    assert ledger.balance(session, 1) == 70.0
    assert ledger.balance(session, 2) == 750.0


def test_top_accounts(setup):
    engine, _ = setup
    top = ledger.top_accounts(engine, limit=2)
    assert [r["name"] for r in top] == ["bank", "cash"]
    assert float(top[0]["total"]) == 750.0


def test_raw_totals(setup):
    engine, _ = setup
    assert ledger.raw_totals(engine) == {"bank": 2, "cash": 2, "fees": 1}


def test_record_with_memo(setup):
    engine, session = setup
    ledger.record(engine, "fees", 2.5, memo="wire")
    assert ledger.balance(session, 3) == 7.5
