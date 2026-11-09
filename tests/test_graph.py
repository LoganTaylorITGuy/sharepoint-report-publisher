from __future__ import annotations

from pathlib import Path

import pytest
import responses
from responses import matchers

from report_publisher.graph import (
    CHUNK_SIZE,
    GRAPH_ROOT,
    SIMPLE_UPLOAD_LIMIT,
    AppCredentials,
    GraphClient,
    GraphError,
    MsalTokenProvider,
)


class StaticToken:
    def __init__(self) -> None:
        self.calls = 0

    def get_token(self) -> str:
        self.calls += 1
        return "test-token"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(sleeps) -> GraphClient:
    return GraphClient(StaticToken(), sleep=sleeps.append)


AUTH = matchers.header_matcher({"Authorization": "Bearer test-token"})


# -- credentials -------------------------------------------------------------------------------


def test_credentials_prefer_certificate():
    creds = AppCredentials.from_env(
        {
            "GRAPH_TENANT_ID": "t",
            "GRAPH_CLIENT_ID": "c",
            "GRAPH_CERT_PATH": "/k.pem",
            "GRAPH_CERT_THUMBPRINT": "AB12",
            "GRAPH_CLIENT_SECRET": "ignored",
        }
    )
    assert creds.certificate_path == Path("/k.pem")
    assert creds.client_secret is None


def test_credentials_secret_fallback():
    creds = AppCredentials.from_env(
        {"GRAPH_TENANT_ID": "t", "GRAPH_CLIENT_ID": "c", "GRAPH_CLIENT_SECRET": "s"}
    )
    assert creds.client_secret == "s"
    assert creds.certificate_path is None


@pytest.mark.parametrize(
    ("env", "fragment"),
    [
        ({}, "GRAPH_TENANT_ID"),
        ({"GRAPH_TENANT_ID": "t", "GRAPH_CLIENT_ID": "c"}, "GRAPH_CLIENT_SECRET"),
        (
            {"GRAPH_TENANT_ID": "t", "GRAPH_CLIENT_ID": "c", "GRAPH_CERT_PATH": "/k.pem"},
            "set both",
        ),
    ],
)
def test_credentials_report_what_is_missing(env, fragment):
    with pytest.raises(GraphError, match=fragment):
        AppCredentials.from_env(env)


class FakeMsalApp:
    def __init__(self, result):
        self.result = result
        self.init_args = None

    def __call__(self, client_id, **kwargs):
        self.init_args = (client_id, kwargs)
        return self

    def acquire_token_for_client(self, scopes):
        assert scopes == ["https://graph.microsoft.com/.default"]
        return self.result


def test_msal_provider_with_certificate(tmp_path):
    key = tmp_path / "key.pem"
    key.write_text("PRIVATE KEY", encoding="utf-8")
    fake = FakeMsalApp({"access_token": "abc"})
    provider = MsalTokenProvider(AppCredentials("tenant", "client", key, "THUMB"), app_factory=fake)
    assert provider.get_token() == "abc"
    client_id, kwargs = fake.init_args
    assert client_id == "client"
    assert kwargs["authority"] == "https://login.microsoftonline.com/tenant"
    assert kwargs["client_credential"] == {"private_key": "PRIVATE KEY", "thumbprint": "THUMB"}


def test_msal_provider_with_secret_surfaces_errors():
    fake = FakeMsalApp({"error": "invalid_client", "error_description": "AADSTS7000215"})
    provider = MsalTokenProvider(AppCredentials("t", "c", client_secret="s"), app_factory=fake)
    assert fake.init_args[1]["client_credential"] == "s"
    with pytest.raises(GraphError, match="AADSTS7000215"):
        provider.get_token()


# -- site and library lookup -------------------------------------------------------------------


@responses.activate
def test_resolve_site_id(client):
    responses.get(
        f"{GRAPH_ROOT}/sites/contoso.sharepoint.com:/sites/Ops%20Team",
        json={"id": "site-1"},
        match=[AUTH],
    )
    assert client.resolve_site_id("contoso.sharepoint.com:/sites/Ops Team") == "site-1"


@pytest.mark.parametrize(
    "site",
    [
        "https://contoso.sharepoint.com/sites/Ops",
        "https://contoso.sharepoint.com/sites/Ops/",
        "contoso.sharepoint.com:/sites/Ops",
    ],
)
def test_parse_site_accepts_browser_url_and_graph_form(site):
    assert GraphClient.parse_site(site) == ("contoso.sharepoint.com", "sites/Ops")


def test_parse_site_accepts_teams_sites():
    assert GraphClient.parse_site("https://contoso.sharepoint.com/teams/IT")[1] == "teams/IT"


@pytest.mark.parametrize(
    "site",
    [
        "contoso.sharepoint.com",
        "https://contoso.sharepoint.com/",
        "https://contoso.sharepoint.com/Shared%20Documents",
        "http://contoso.sharepoint.com/sites/Ops",
        "Ops",
    ],
)
def test_parse_site_rejects_bad_input(client, site):
    with pytest.raises(GraphError, match="must look like"):
        client.resolve_site_id(site)


@responses.activate
def test_resolve_default_drive(client):
    responses.get(f"{GRAPH_ROOT}/sites/site-1/drive", json={"id": "drive-default"})
    assert client.resolve_drive_id("site-1") == "drive-default"


@responses.activate
def test_resolve_named_drive_case_insensitive(client):
    responses.get(
        f"{GRAPH_ROOT}/sites/site-1/drives",
        json={"value": [{"id": "d1", "name": "Documents"}, {"id": "d2", "name": "Reports"}]},
    )
    assert client.resolve_drive_id("site-1", "reports") == "d2"


@responses.activate
def test_unknown_drive_lists_available(client):
    responses.get(
        f"{GRAPH_ROOT}/sites/site-1/drives",
        json={"value": [{"id": "d1", "name": "Documents"}]},
    )
    with pytest.raises(GraphError, match=r"'Reports' not found.*available: Documents"):
        client.resolve_drive_id("site-1", "Reports")


# -- uploads ---------------------------------------------------------------------------------


@responses.activate
def test_simple_upload_replaces_file(client):
    responses.put(
        f"{GRAPH_ROOT}/drives/d1/root:/Reports/Weekly/report%202026-W45.md:/content",
        json={"id": "item-1", "webUrl": "https://contoso.sharepoint.com/x"},
        match=[
            AUTH,
            matchers.query_param_matcher({"@microsoft.graph.conflictBehavior": "replace"}),
        ],
    )
    item = client.upload_file("d1", "/Reports/Weekly/", "report 2026-W45.md", b"# hi")
    assert item["webUrl"] == "https://contoso.sharepoint.com/x"
    assert responses.calls[0].request.body == b"# hi"


@responses.activate
def test_upload_to_library_root(client):
    responses.put(f"{GRAPH_ROOT}/drives/d1/root:/r.md:/content", json={"id": "i"})
    assert client.upload_file("d1", "", "r.md", b"x") == {"id": "i"}


@responses.activate
def test_large_upload_uses_chunked_session_without_bearer_token(client):
    size = 2 * CHUNK_SIZE + 10  # > simple-upload limit; splits into 3 chunks
    assert size > SIMPLE_UPLOAD_LIMIT
    content = b"a" * size
    upload_url = "https://contoso.sharepoint.com/_api/upload?token=pre-authorised"
    responses.post(
        f"{GRAPH_ROOT}/drives/d1/root:/big.md:/createUploadSession",
        json={"uploadUrl": upload_url},
        match=[
            AUTH,
            matchers.json_params_matcher(
                {"item": {"@microsoft.graph.conflictBehavior": "replace"}}
            ),
        ],
    )
    responses.put(upload_url, status=202, json={"nextExpectedRanges": ["..."]})
    responses.put(upload_url, status=202, json={"nextExpectedRanges": ["..."]})
    responses.put(upload_url, status=201, json={"id": "big", "webUrl": "https://x/big.md"})

    item = client.upload_file("d1", "", "big.md", content)

    assert item["id"] == "big"
    chunk_calls = responses.calls[1:]
    assert len(chunk_calls) == 3
    ranges = [c.request.headers["Content-Range"] for c in chunk_calls]
    assert ranges == [
        f"bytes 0-{CHUNK_SIZE - 1}/{size}",
        f"bytes {CHUNK_SIZE}-{2 * CHUNK_SIZE - 1}/{size}",
        f"bytes {2 * CHUNK_SIZE}-{size - 1}/{size}",
    ]
    assert all("Authorization" not in c.request.headers for c in chunk_calls)
    assert sum(len(c.request.body) for c in chunk_calls) == size


@responses.activate
def test_upload_session_without_final_item_fails(client):
    upload_url = "https://contoso.sharepoint.com/_api/upload"
    responses.post(
        f"{GRAPH_ROOT}/drives/d1/root:/big.md:/createUploadSession", json={"uploadUrl": upload_url}
    )
    responses.put(upload_url, status=202, json={})
    with pytest.raises(GraphError, match="without returning the file"):
        client.upload_file("d1", "", "big.md", b"a" * (SIMPLE_UPLOAD_LIMIT + 1))


# -- resilience and errors ---------------------------------------------------------------------


@responses.activate
def test_retries_throttling_honouring_retry_after(client, sleeps):
    url = f"{GRAPH_ROOT}/sites/site-1/drive"
    responses.get(url, status=429, headers={"Retry-After": "7"})
    responses.get(url, status=503)
    responses.get(url, json={"id": "drive"})
    assert client.resolve_drive_id("site-1") == "drive"
    assert sleeps == [7.0, 2.0]


@responses.activate
def test_gives_up_after_max_retries(sleeps):
    client = GraphClient(StaticToken(), sleep=sleeps.append, max_retries=2)
    url = f"{GRAPH_ROOT}/sites/site-1/drive"
    for _ in range(3):
        responses.get(
            url, status=429, json={"error": {"code": "TooManyRequests", "message": "slow"}}
        )
    with pytest.raises(GraphError, match=r"429 TooManyRequests\): slow"):
        client.resolve_drive_id("site-1")
    assert sleeps == [1.0, 2.0]


@responses.activate
def test_error_message_is_useful_and_has_no_token(client):
    responses.get(
        f"{GRAPH_ROOT}/sites/site-1/drive",
        status=403,
        json={
            "error": {
                "code": "accessDenied",
                "message": "Either scp or roles claim need to be present",
            }
        },
    )
    with pytest.raises(GraphError) as info:
        client.resolve_drive_id("site-1")
    text = str(info.value)
    assert "GET /sites/site-1/drive failed (403 accessDenied)" in text
    assert "roles claim" in text
    assert "test-token" not in text


@responses.activate
def test_non_json_error_body(client):
    responses.get(f"{GRAPH_ROOT}/sites/site-1/drive", status=500, body="<html>oops</html>")
    with pytest.raises(GraphError, match=r"failed \(500\)"):
        client.resolve_drive_id("site-1")
