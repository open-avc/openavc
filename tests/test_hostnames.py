"""THE name rule: what this host answers to, and where the ".local" goes.

One question — *is this name already resolvable?* — was answered three
different ways by three surfaces, and each got it wrong on a different
platform. The setup screen appended ".local" to a name that already ended in
it (`Aarons-MacBook-Air.local.local`); the certificate's SAN list ran the
whole hostname through a single-*label* sanitizer, which strips dots
(`Aarons-MacBook-Airlocal`). Both produce a name no lookup can return.

The rule is one line — a name carrying a dot is already resolvable — and it
lives in ``openavc.utils.hostnames``. These cases pin it against the three
hostname shapes the product actually meets.
"""

import pytest

from openavc.utils.hostnames import (
    cert_hostnames,
    resolvable_hostname,
    sanitize_hostname,
    sanitize_label,
)


# --- The three shapes of an OS hostname -----------------------------------
#
# An appliance image and most Linux boxes: bare. macOS: already dotted, and
# the dotted form IS the mDNS name. A managed host: a full domain name.

APPLIANCE = "openavc"
MACOS = "Aarons-MacBook-Air.local"
MANAGED = "box.corp.example.com"


class TestTheSuffixGoesOnABareNameOnly:
    def test_a_bare_name_becomes_the_mdns_name(self):
        assert resolvable_hostname(APPLIANCE) == "openavc.local"

    def test_a_name_that_already_ends_in_local_is_left_alone(self):
        """The doubling F-003 photographed on the setup screen."""
        assert resolvable_hostname(MACOS) == "Aarons-MacBook-Air.local"

    def test_a_domain_name_is_left_alone(self):
        """The site's DNS answers this; ".local" would answer nowhere."""
        assert resolvable_hostname(MANAGED) == "box.corp.example.com"

    def test_a_trailing_dot_is_not_a_suffix(self):
        assert resolvable_hostname("openavc.") == "openavc.local"

    @pytest.mark.parametrize("raw", ["", "   ", "localhost", "localhost.localdomain"])
    def test_a_host_with_no_name_of_its_own_offers_none(self, raw):
        """There is nothing to print, and "" is what says so."""
        assert resolvable_hostname(raw) == ""


class TestALabelIsNotAHostname:
    """The mixup behind F-052: dots are illegal in a label and load-bearing
    in a hostname, so one function cannot serve both."""

    def test_a_label_drops_the_dots(self):
        assert sanitize_label(MACOS) == "Aarons-MacBook-Airlocal"

    def test_a_hostname_keeps_them(self):
        assert sanitize_hostname(MACOS) == "Aarons-MacBook-Air.local"

    def test_every_label_is_scrubbed_individually(self):
        assert sanitize_hostname("Aaron's Box.Corp Net.example.com") == (
            "Aarons-Box.Corp-Net.example.com"
        )

    def test_a_label_that_scrubs_away_is_dropped_rather_than_left_empty(self):
        assert sanitize_hostname("box..example.com") == "box.example.com"

    def test_a_label_is_capped_at_the_dns_limit(self):
        assert len(sanitize_label("a" * 200)) == 63

    def test_an_empty_label_falls_back_so_the_advertiser_always_has_one(self):
        assert sanitize_label("!!!") == "openavc"
        assert sanitize_label("!!!", fallback="") == ""


class TestTheNamesACertificateHasToCover:
    """A SAN list a browser will actually match.

    ``localhost`` is the caller's to add — it is true of every host and says
    nothing about this one.
    """

    def test_an_appliance_gets_both_the_machine_name_and_its_mdns_name(self):
        assert cert_hostnames(APPLIANCE) == ["openavc", "openavc.local"]

    def test_macos_gets_its_real_name_and_not_the_stripped_one(self):
        """F-052 exactly: the SAN used to read `Aarons-MacBook-Airlocal`, so
        installing the CA still warned on the name mDNS announces."""
        names = cert_hostnames(MACOS)
        assert names == ["Aarons-MacBook-Air.local"]
        assert "Aarons-MacBook-Airlocal" not in names

    def test_a_managed_box_gets_its_domain_name_and_the_short_mdns_form(self):
        """DNS answers the first; avahi and the setup screen use the second."""
        assert cert_hostnames(MANAGED) == ["box.corp.example.com", "box.local"]

    @pytest.mark.parametrize("raw", ["", "localhost", "!!!"])
    def test_a_host_with_no_name_contributes_none(self, raw):
        assert cert_hostnames(raw) == []
