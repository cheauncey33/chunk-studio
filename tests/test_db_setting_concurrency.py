"""Settings reads must be safe under concurrent audit workers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from app import db


def test_concurrent_get_setting_does_not_raise(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "settings_conc.db")
    db._conn = None
    db.init_db()
    db.set_setting("llm.base_url", "https://example.test")
    db.set_setting("llm.api_key", "sk-test")

    def _read(_: int) -> str:
        return db.get_setting("llm.base_url")

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(_read, i) for i in range(64)]
        values = [future.result() for future in as_completed(futures)]

    assert all(value == "https://example.test" for value in values)
