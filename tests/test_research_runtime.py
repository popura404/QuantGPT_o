"""Changing installed numerical dependencies must invalidate stored results."""

from quantgpt.research import runtime


def test_installed_numpy_change_invalidates_engine_identity_without_source_edit(monkeypatch):
    before = runtime.local_engine_identity()
    assert before == runtime.local_engine_identity()
    real_version = runtime.metadata.version

    def changed_version(name):
        return "999.0.test" if name == "numpy" else real_version(name)

    monkeypatch.setattr(runtime.metadata, "version", changed_version)
    after = runtime.local_engine_identity()
    assert after.version == before.version
    assert after.code_version != before.code_version
    assert after == runtime.local_engine_identity()
