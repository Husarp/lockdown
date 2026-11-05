"""Friendly labels are display-only, with strict suffix boundaries."""
import pytest

from network_services import SERVICE_DOMAINS, background_app, service_name


@pytest.mark.parametrize("service,suffix", [(service, suffix) for service, suffixes in SERVICE_DOMAINS.items() for suffix in suffixes])
def test_service_suffixes(service, suffix):
    assert service_name(suffix) == service
    assert service_name("cdn." + suffix.upper() + ".") == service
    parent = suffix.partition(".")[2]
    expected = service_name(parent)
    assert service_name("not" + suffix) == ("not" + suffix if expected == parent else expected)
    assert service_name(suffix + ".example.org") == suffix + ".example.org"


@pytest.mark.parametrize("host", ["unknown.example", "d123.cloudfront.net", "shared.akamaized.net", "1.2.3.4", "", "EXAMPLE.ORG."])
def test_unknown_and_shared_hosts_are_preserved(host):
    assert service_name(host) == host


def test_specific_services_win_over_parent_domains():
    assert service_name("youtubei.googleapis.com") == "YouTube"
    assert service_name("other.googleapis.com") == "Google"
    assert service_name("a.delivery.mp.microsoft.com") == "Microsoft/Windows Update"
    assert service_name("www.microsoft.com") == "Microsoft"


def test_background_is_explicit_and_does_not_guess_foreground_apps():
    assert background_app("agent.exe", "Infatica agent") == "Background: Infatica agent (bandwidth sharing)"
    assert background_app("infatica-service.exe", "Agent").startswith("Background:")
    assert background_app("svchost.exe", "Svchost", True) == "Background: Svchost"
    assert background_app("chrome.exe", "Chrome") == "Chrome"
