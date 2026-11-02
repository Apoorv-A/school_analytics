"""Tests for tenant_config.version_rules (mirrors config repo scripts/version_rules.py)."""

from tenant_config.version_rules import RELEASED, SYNTACTIC, explain, is_released


def test_released_accepts_plain_and_hotfix():
    for tag in ("1.0.0", "12.3.45", "1.0.0-hotfix.1"):
        assert RELEASED.match(tag)
        assert is_released(tag)


def test_released_rejects_latest_and_pre_releases():
    for tag in ("latest", "0.1.0-SNAPSHOT", "1.0.0-beta.7", "1.0.0-rc.1"):
        assert not RELEASED.match(tag)
        assert not is_released(tag)


def test_syntactic_allows_dev_pre_releases():
    assert SYNTACTIC.match("0.1.0-SNAPSHOT")


def test_explain_latest():
    assert "latest" in explain("latest")
