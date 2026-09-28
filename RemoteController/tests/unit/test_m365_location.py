"""Every way a browser spells a OneDrive/SharePoint folder resolves to one location."""
from __future__ import annotations

import pytest

from m365.location import LocationError, default_tenant_for_host, parse_folder_url

SP = ("sites", "Kanzlei", "Shared Documents", "Akten 2024")


@pytest.mark.parametrize(
    "url, expected",
    [
        # plain path
        ("https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten%202024", SP),
        # library view with the folder in ?id=
        (
            "https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Forms/AllItems.aspx"
            "?id=%2Fsites%2FKanzlei%2FShared%20Documents%2FAkten%202024&viewid=abc",
            SP,
        ),
        # library view without ?id= is the library root
        (
            "https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Forms/AllItems.aspx",
            ("sites", "Kanzlei", "Shared Documents"),
        ),
        # "copy link" that keeps the resource path
        ("https://contoso.sharepoint.com/:f:/r/sites/Kanzlei/Shared%20Documents/Akten%202024?csf=1&web=1", SP),
        # Teams-backed site
        ("https://contoso.sharepoint.com/teams/Recht/Freigegebene%20Dokumente", ("teams", "Recht", "Freigegebene Dokumente")),
        # OneDrive, plain
        (
            "https://contoso-my.sharepoint.com/personal/anna_contoso_ch/Documents/Akten",
            ("personal", "anna_contoso_ch", "Documents", "Akten"),
        ),
        # OneDrive's own page
        (
            "https://contoso-my.sharepoint.com/my?id=%2Fpersonal%2Fanna_contoso_ch%2FDocuments%2FAkten",
            ("personal", "anna_contoso_ch", "Documents", "Akten"),
        ),
        (
            "https://contoso-my.sharepoint.com/personal/anna_contoso_ch/_layouts/15/onedrive.aspx"
            "?id=%2Fpersonal%2Fanna_contoso_ch%2FDocuments%2FAkten&view=0",
            ("personal", "anna_contoso_ch", "Documents", "Akten"),
        ),
        # OneDrive root without ?id= is the whole OneDrive
        (
            "https://contoso-my.sharepoint.com/personal/anna_contoso_ch/_layouts/15/onedrive.aspx",
            ("personal", "anna_contoso_ch"),
        ),
        # the tenant's root site
        ("https://contoso.sharepoint.com/Shared%20Documents/Akten", ("Shared Documents", "Akten")),
    ],
)
def test_browser_addresses_parse_to_the_same_segments(url, expected):
    loc = parse_folder_url(url)
    assert loc.segments == expected


def test_onedrive_is_recognised():
    assert parse_folder_url("https://contoso-my.sharepoint.com/personal/a/Documents").is_onedrive
    assert not parse_folder_url("https://contoso.sharepoint.com/sites/x/Shared%20Documents").is_onedrive


@pytest.mark.parametrize(
    "url, needle",
    [
        ("", "No OneDrive/SharePoint"),
        ("http://contoso.sharepoint.com/sites/x", "https://"),
        ("https://example.com/sites/x", "not a OneDrive or SharePoint host"),
        ("https://contoso.sharepoint.com/:f:/s/Kanzlei/EgAbCdEf?e=xyz", "sharing link"),
        ("https://contoso.sharepoint.com/:w:/g/personal/x/EabC", "sharing link"),
        ("https://contoso.sharepoint.com/sites/Kanzlei/SitePages/Home.aspx", "is a page"),
        ("https://contoso-my.sharepoint.com/my", "whose OneDrive"),
        ("https://contoso.sharepoint.com/sites/x/%2E%2E/y", "relative"),
    ],
)
def test_unusable_addresses_say_why(url, needle):
    with pytest.raises(LocationError) as err:
        parse_folder_url(url)
    assert needle in str(err.value)


@pytest.mark.parametrize(
    "host, tenant",
    [
        ("contoso.sharepoint.com", "contoso.onmicrosoft.com"),
        ("contoso-my.sharepoint.com", "contoso.onmicrosoft.com"),
        ("contoso.sharepoint.de", ""),
        ("a.b.sharepoint.com", ""),
    ],
)
def test_tenant_is_derived_from_the_host(host, tenant):
    assert default_tenant_for_host(host) == tenant
