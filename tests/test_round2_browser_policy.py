"""Round 2a item 14: keep registry originals through enforcement and uninstall."""
import importlib
import json

import pytest

from blocker import browser_policy as policy


CHROME = r"SOFTWARE\Policies\Google\Chrome"
FIREFOX = r"SOFTWARE\Policies\Mozilla\Firefox"


@pytest.fixture
def registry(monkeypatch):
    values = {}

    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def query(key, name):
        try:
            return values[key.path, name]
        except KeyError:
            raise FileNotFoundError(name) from None

    def delete(key, name):
        try:
            del values[key.path, name]
        except KeyError:
            raise FileNotFoundError(name) from None

    monkeypatch.setattr(policy.winreg, "CreateKeyEx", lambda root, path, *args: Key(path))
    monkeypatch.setattr(policy.winreg, "OpenKey", lambda root, path, *args: Key(path))
    monkeypatch.setattr(policy.winreg, "QueryValueEx", query)
    monkeypatch.setattr(policy.winreg, "SetValueEx",
                        lambda key, name, reserved, kind, data: values.__setitem__((key.path, name), (data, kind)))
    monkeypatch.setattr(policy.winreg, "DeleteValue", delete)
    return values


def test_uninstall_restores_originals_once_across_restarts(registry):
    originals = {
        (CHROME, "DnsOverHttpsMode"): ("secure", policy.REG_SZ),
        (CHROME, "QuicAllowed"): (1, policy.REG_DWORD),
        (CHROME, "BuiltInDnsClientEnabled"): (0, policy.REG_DWORD),  # already matches ours
        (FIREFOX, "Preferences"): ([json.dumps({"custom.pref": {"Value": "keep"}})], policy.REG_MULTI_SZ),
        (FIREFOX + r"\DNSOverHTTPS", "Enabled"): (1, policy.REG_DWORD),
        (CHROME, "ForceGoogleSafeSearch"): (0, policy.REG_DWORD),
        (CHROME, "ForceYouTubeRestrict"): (2, policy.REG_DWORD),
        (CHROME, "UnrelatedPolicy"): ("leave alone", policy.REG_SZ),
    }
    registry.update(originals)
    assert policy.apply(safe_search=True, youtube=True)
    assert registry[CHROME, "DnsOverHttpsMode"] == ("off", policy.REG_SZ)
    assert registry[FIREFOX, "Preferences"] == ([policy._FIREFOX_PREFS], policy.REG_MULTI_SZ)
    # A fresh module, like a restarted service/uninstaller, must use persisted originals.
    importlib.reload(policy)
    registry[CHROME, "QuicAllowed"] = (7, policy.REG_DWORD)
    assert policy.apply(safe_search=True, youtube=True)
    assert not policy.apply(safe_search=True, youtube=True)
    policy.remove()
    assert {key: value for key, value in registry.items() if key[0].startswith("SOFTWARE\\Policies\\")} == originals
    policy.remove()
    assert all(registry[key] == value for key, value in originals.items())


def test_optional_policies_restore_originals_when_disabled(registry):
    registry[CHROME, "ForceGoogleSafeSearch"] = (0, policy.REG_DWORD)
    registry[CHROME, "ForceYouTubeRestrict"] = (2, policy.REG_DWORD)
    policy.apply(safe_search=True, youtube=True)
    policy.apply(safe_search=False, youtube=False)
    assert registry[CHROME, "ForceGoogleSafeSearch"] == (0, policy.REG_DWORD)
    assert registry[CHROME, "ForceYouTubeRestrict"] == (2, policy.REG_DWORD)
    assert not policy.apply(safe_search=False, youtube=False)


def test_disabled_optional_policies_never_owned_are_untouched(registry):
    registry[CHROME, "ForceGoogleSafeSearch"] = (1, policy.REG_DWORD)
    policy.apply()
    assert registry[CHROME, "ForceGoogleSafeSearch"] == (1, policy.REG_DWORD)


def test_remove_without_prior_apply_preserves_unowned_policies(registry):
    registry[CHROME, "QuicAllowed"] = (1, policy.REG_DWORD)
    policy.remove()
    assert registry[CHROME, "QuicAllowed"] == (1, policy.REG_DWORD)


def test_backup_failure_does_not_overwrite_original(registry, monkeypatch):
    registry[CHROME, "DnsOverHttpsMode"] = ("secure", policy.REG_SZ)
    write = policy.winreg.SetValueEx

    def fail_backup(key, name, *args):
        if key.path.startswith(policy._BACKUP):
            raise PermissionError("backup denied")
        return write(key, name, *args)

    monkeypatch.setattr(policy.winreg, "SetValueEx", fail_backup)
    with pytest.raises(PermissionError, match="backup denied"):
        policy.apply()
    assert registry[CHROME, "DnsOverHttpsMode"] == ("secure", policy.REG_SZ)


def test_failed_restore_retains_original_for_retry(registry, monkeypatch):
    registry[CHROME, "DnsOverHttpsMode"] = ("secure", policy.REG_SZ)
    policy.apply()
    write = policy.winreg.SetValueEx

    def fail_restore(key, name, *args):
        if key.path == CHROME and name == "DnsOverHttpsMode":
            raise PermissionError("restore denied")
        return write(key, name, *args)

    monkeypatch.setattr(policy.winreg, "SetValueEx", fail_restore)
    with pytest.raises(PermissionError, match="restore denied"):
        policy.remove()
    assert registry[CHROME, "DnsOverHttpsMode"] == ("off", policy.REG_SZ)
    monkeypatch.setattr(policy.winreg, "SetValueEx", write)
    policy.remove()
    assert registry[CHROME, "DnsOverHttpsMode"] == ("secure", policy.REG_SZ)


def legacy_install(registry):
    # The complete old Lockdown DNS/browser fingerprint distinguishes its footprint from a single admin policy.
    for path, entries in policy.POLICIES.items():
        for name, (kind, data) in entries.items():
            registry[path, name] = (data, kind)
    for policies in (policy.SAFE_SEARCH, policy.YOUTUBE):
        for path, entries in policies.items():
            for name, (kind, data) in entries.items():
                registry[path, name] = (data, kind)


@pytest.mark.parametrize('upgrade_on', [False, True])
def test_legacy_lockdown_restrictions_are_not_saved_as_originals(registry, upgrade_on):
    legacy_install(registry)
    # Independent administrator value differs from Lockdown's old moderate restriction.
    registry[CHROME, 'ForceYouTubeRestrict'] = (2, policy.REG_DWORD)
    policy.apply(safe_search=upgrade_on, youtube=upgrade_on)
    policy.apply(safe_search=False, youtube=False)
    for path, entries in policy.SAFE_SEARCH.items():
        for name in entries:
            assert (path, name) not in registry
    assert registry[CHROME, 'ForceYouTubeRestrict'] == (2, policy.REG_DWORD)
    policy.remove()
    assert registry[CHROME, 'ForceYouTubeRestrict'] == (2, policy.REG_DWORD)
    assert (CHROME, 'DnsOverHttpsMode') not in registry


def test_isolated_admin_policy_matching_our_value_is_preserved(registry):
    registry[CHROME, 'ForceGoogleSafeSearch'] = (1, policy.REG_DWORD)
    policy.apply(safe_search=True)
    policy.apply(safe_search=False)
    policy.remove()
    assert registry[CHROME, 'ForceGoogleSafeSearch'] == (1, policy.REG_DWORD)


def test_admin_change_after_enforcement_is_preserved_when_disabled_and_removed(registry):
    policy.apply(safe_search=True, youtube=True)
    registry[CHROME, 'ForceGoogleSafeSearch'] = (0, policy.REG_DWORD)
    registry[CHROME, 'ForceYouTubeRestrict'] = (2, policy.REG_DWORD)
    policy.apply(safe_search=False, youtube=False)
    policy.remove()
    assert registry[CHROME, 'ForceGoogleSafeSearch'] == (0, policy.REG_DWORD)
    assert registry[CHROME, 'ForceYouTubeRestrict'] == (2, policy.REG_DWORD)


def test_previous_upgrade_captured_legacy_values_are_repaired(registry):
    legacy_install(registry)
    # Simulate the originals captured by the reviewed Round 2 implementation, before the migration marker existed.
    for path, entries in {**policy.POLICIES}.items():
        for name, (kind, data) in entries.items():
            registry[policy._BACKUP + '\\' + path, 'present.' + name] = (1, policy.REG_DWORD)
            registry[policy._BACKUP + '\\' + path, 'value.' + name] = (data, kind)
    for policies in (policy.SAFE_SEARCH, policy.YOUTUBE):
        for path, entries in policies.items():
            for name, (kind, data) in entries.items():
                registry[policy._BACKUP + '\\' + path, 'present.' + name] = (1, policy.REG_DWORD)
                registry[policy._BACKUP + '\\' + path, 'value.' + name] = (data, kind)
    policy.apply(safe_search=False, youtube=False)
    assert (CHROME, 'ForceGoogleSafeSearch') not in registry
    assert (CHROME, 'ForceYouTubeRestrict') not in registry
