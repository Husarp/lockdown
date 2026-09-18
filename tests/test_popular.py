from blocker.hosts import normalize_host
from importer.popular import POPULAR_SITES


def test_all_hostnames_are_valid_and_normalized():
    for sites in POPULAR_SITES.values():
        for name, hostnames in sites.items():
            assert hostnames, name
            for h in hostnames:
                assert normalize_host(h) == h, (name, h)
