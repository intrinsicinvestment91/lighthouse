"""Unit tests for config.py"""

from config import AppConfig, ArchiveConfig, NostrConfig, PatentConfig


def test_patent_config_default_classes():
    cfg = PatentConfig()
    assert "G21B" in cfg.classes
    assert "C22C" in cfg.classes
    assert "B64G" in cfg.classes


def test_patent_config_has_timeout_fields():
    cfg = PatentConfig()
    assert cfg.http_timeout > 0
    assert cfg.playwright_timeout_ms > 0
    assert cfg.inter_class_delay >= 0


def test_patent_config_wipo_page_size():
    cfg = PatentConfig()
    assert cfg.wipo_page_size > 0


def test_archive_config_targets_have_required_keys():
    cfg = ArchiveConfig()
    assert len(cfg.targets) > 0
    for target in cfg.targets:
        assert "agency" in target
        assert "url" in target
        assert "keywords" in target


def test_archive_config_targets_have_selectors():
    cfg = ArchiveConfig()
    for target in cfg.targets:
        assert "article_link_selector" in target
        assert len(target["article_link_selector"]) > 0


def test_archive_config_feed_url_when_present_is_http():
    cfg = ArchiveConfig()
    for target in cfg.targets:
        feed_url = target.get("feed_url")
        if feed_url:
            assert feed_url.startswith("http")
            assert len(feed_url) > 0


def test_nostr_config_relays_nonempty():
    cfg = NostrConfig()
    assert len(cfg.relays) > 0
    for relay in cfg.relays:
        assert relay.startswith("wss://")


def test_nostr_config_has_timeout_fields():
    cfg = NostrConfig()
    assert cfg.ws_open_timeout > 0
    assert cfg.ws_recv_timeout > 0
    assert cfg.inter_event_sleep >= 0


def test_app_config_state_db_path(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "/tmp/lhp-contract")
    cfg = AppConfig()
    assert cfg.state_db_path.endswith(".db")
    assert cfg.state_db_path == "/tmp/lhp-contract/state.db"
