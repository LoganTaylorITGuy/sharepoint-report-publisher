"""Minimal Microsoft Graph client: app-only auth, site/library lookup, and file upload.

Deliberately small and dependency-light (requests + msal) instead of the full Graph SDK,
so the behaviour that matters for an unattended job is explicit and testable:
throttling retries, the 4 MB simple-upload limit, and chunked upload sessions.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import msal
import requests

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024
# Upload-session chunks must be a multiple of 320 KiB; Graph recommends 5-10 MiB.
CHUNK_SIZE = 320 * 1024 * 16
RETRY_STATUSES = frozenset({429, 502, 503, 504})


class GraphError(RuntimeError):
    """A Graph call failed. Never includes the access token."""


class TokenProvider(Protocol):
    def get_token(self) -> str: ...


@dataclass(frozen=True)
class AppCredentials:
    tenant_id: str
    client_id: str
    certificate_path: Path | None = None
    certificate_thumbprint: str | None = None
    client_secret: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str]) -> AppCredentials:
        """Read GRAPH_* variables. Certificate auth preferred; client secret is the fallback."""
        missing = [k for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID") if not env.get(k)]
        cert, thumb = env.get("GRAPH_CERT_PATH"), env.get("GRAPH_CERT_THUMBPRINT")
        secret = env.get("GRAPH_CLIENT_SECRET")
        if bool(cert) != bool(thumb):
            missing.append("GRAPH_CERT_PATH and GRAPH_CERT_THUMBPRINT (set both)")
        if not (cert or secret):
            missing.append("GRAPH_CERT_PATH + GRAPH_CERT_THUMBPRINT, or GRAPH_CLIENT_SECRET")
        if missing:
            raise GraphError("missing configuration: " + "; ".join(missing))
        return cls(
            tenant_id=env["GRAPH_TENANT_ID"],
            client_id=env["GRAPH_CLIENT_ID"],
            certificate_path=Path(cert) if cert else None,
            certificate_thumbprint=thumb,
            client_secret=None if cert else secret,
        )


class MsalTokenProvider:
    """Client-credentials (app-only) token via MSAL. MSAL caches tokens in memory."""

    def __init__(self, creds: AppCredentials, app_factory: Callable[..., Any] | None = None):
        credential: dict[str, str] | str
        if creds.certificate_path:
            credential = {
                "private_key": creds.certificate_path.read_text(encoding="utf-8"),
                "thumbprint": creds.certificate_thumbprint or "",
            }
        else:
            credential = creds.client_secret or ""
        factory = app_factory or msal.ConfidentialClientApplication
        self._app = factory(
            creds.client_id,
            authority=f"https://login.microsoftonline.com/{creds.tenant_id}",
            client_credential=credential,
        )

    def get_token(self) -> str:
        result = self._app.acquire_token_for_client(scopes=GRAPH_SCOPE)
        token = result.get("access_token")
        if not token:
            detail = result.get("error_description") or result.get("error") or "unknown error"
            raise GraphError(f"could not get a Graph token: {detail}")
        return str(token)


class GraphClient:
    def __init__(
        self,
        tokens: TokenProvider,
        session: requests.Session | None = None,
        *,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        self._tokens = tokens
        self._session = session or requests.Session()
        self._max_retries = max_retries
        self._sleep = sleep
        self._timeout = timeout

    # -- transport -------------------------------------------------------------------------

    def _send(
        self, method: str, url: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if auth:
            headers["Authorization"] = f"Bearer {self._tokens.get_token()}"
        for attempt in range(self._max_retries + 1):
            resp = self._session.request(
                method, url, headers=headers, timeout=self._timeout, **kwargs
            )
            if resp.status_code not in RETRY_STATUSES or attempt == self._max_retries:
                break
            self._sleep(self._retry_delay(resp, attempt))
        if resp.status_code >= 400:
            raise GraphError(self._describe_error(method, url, resp))
        return resp

    @staticmethod
    def _retry_delay(resp: requests.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return min(float(retry_after), 120.0)
        return float(min(2**attempt, 30))

    @staticmethod
    def _describe_error(method: str, url: str, resp: requests.Response) -> str:
        path = url.split("?", 1)[0].replace(GRAPH_ROOT, "")
        code, message = "", resp.reason or ""
        try:
            err = resp.json().get("error", {})
            code, message = err.get("code", ""), err.get("message", message)
        except ValueError:
            pass
        label = f"{resp.status_code} {code}".strip()
        return f"Graph {method} {path} failed ({label}): {message}"

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = self._send(method, f"{GRAPH_ROOT}{path}", **kwargs)
        data: dict[str, Any] = resp.json()
        return data

    # -- SharePoint ------------------------------------------------------------------------

    @staticmethod
    def parse_site(site: str) -> tuple[str, str]:
        """Accept a pasted browser URL or Graph's ``host:/path`` form -> (host, path)."""
        site = site.strip()
        if site.lower().startswith("https://"):
            parsed = urlparse(site)
            host, path = parsed.netloc, parsed.path
        elif ":/" in site and "://" not in site:
            host, path = site.split(":/", 1)
        else:
            host, path = "", ""
        path = path.strip("/")
        if not host or not path.lower().startswith(("sites/", "teams/")):
            raise GraphError(
                f"site {site!r} must look like https://contoso.sharepoint.com/sites/SiteName "
                "or contoso.sharepoint.com:/sites/SiteName"
            )
        return host, path

    def resolve_site_id(self, site: str) -> str:
        """Look up the Graph site id for a SharePoint site URL."""
        host, path = self.parse_site(site)
        return str(self._json("GET", f"/sites/{host}:/{quote(path)}")["id"])

    def resolve_drive_id(self, site_id: str, library: str | None = None) -> str:
        """The site's default library, or the document library with this display name."""
        if not library:
            return str(self._json("GET", f"/sites/{site_id}/drive")["id"])
        drives = self._json("GET", f"/sites/{site_id}/drives").get("value", [])
        for d in drives:
            if str(d.get("name", "")).casefold() == library.casefold():
                return str(d["id"])
        names = ", ".join(sorted(str(d.get("name")) for d in drives)) or "none"
        raise GraphError(f"library {library!r} not found on site (available: {names})")

    def upload_file(
        self, drive_id: str, folder: str, filename: str, content: bytes
    ) -> dict[str, Any]:
        """Upload (replacing any existing file). Returns the driveItem, including webUrl."""
        item_path = "/".join(p for p in (folder.strip("/"), filename) if p)
        target = f"/drives/{drive_id}/root:/{quote(item_path)}:"

        if len(content) <= SIMPLE_UPLOAD_LIMIT:
            return self._json(
                "PUT",
                f"{target}/content?@microsoft.graph.conflictBehavior=replace",
                data=content,
                headers={"Content-Type": "application/octet-stream"},
            )

        session = self._json(
            "POST",
            f"{target}/createUploadSession",
            json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        )
        upload_url = session["uploadUrl"]
        total = len(content)
        result: dict[str, Any] = {}
        for start in range(0, total, CHUNK_SIZE):
            chunk = content[start : start + CHUNK_SIZE]
            end = start + len(chunk) - 1
            # The upload URL is pre-authorised; sending a bearer token to it is rejected.
            resp = self._send(
                "PUT",
                upload_url,
                auth=False,
                data=chunk,
                headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{total}",
                },
            )
            if resp.status_code in (200, 201):
                result = resp.json()
        if not result:
            raise GraphError("upload session finished without returning the file")
        return result
