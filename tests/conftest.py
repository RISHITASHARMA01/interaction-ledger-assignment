import base64
from pathlib import Path

import pytest

from ledger.config import Config
from ledger.crypto import Crypto
from ledger.db import connect


@pytest.fixture(scope="session")
def config() -> Config:
    return Config.load()


@pytest.fixture
def crypto() -> Crypto:
    return Crypto(b"k" * 32)


@pytest.fixture
def conn(tmp_path: Path, crypto):
    c = connect(tmp_path / "t.db", crypto)
    yield c
    c.close()


@pytest.fixture
def pii_env(monkeypatch):
    monkeypatch.setenv("LEDGER_PII_KEY", base64.urlsafe_b64encode(b"x" * 32).decode())
