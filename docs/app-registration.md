# Least-privilege setup: Entra app registration + `Sites.Selected`

Most guides for uploading to SharePoint from a script grant `Sites.ReadWrite.All`, which lets
the app write to **every site in the tenant**. This tool only needs one library, so this guide
uses **`Sites.Selected`**: the app has no SharePoint access at all until an admin grants it
access to a specific site.

## 1. Create a certificate

A certificate is preferred over a client secret: there's no shared string to leak, it can be
stored in a key vault or certificate store, and it's what Microsoft recommends for unattended
apps.

```bash
openssl req -x509 -newkey rsa:2048 -sha256 -days 365 -nodes \
  -keyout report-publisher.key -out report-publisher.crt -subj "/CN=report-publisher"

# PEM file MSAL expects (private key), and the SHA-1 thumbprint Entra displays
openssl x509 -in report-publisher.crt -noout -fingerprint -sha1 | sed 's/.*=//; s/://g'
```

Keep `report-publisher.key` out of source control. This repo's `.gitignore` already
excludes `*.key`, `*.pem` and `*.pfx`.

## 2. Register the app

1. **Entra admin center → App registrations → New registration.** Name: `report-publisher`.
   Single tenant. No redirect URI.
2. **Certificates & secrets → Certificates → Upload** `report-publisher.crt`.
3. **API permissions → Add → Microsoft Graph → Application permissions → `Sites.Selected`.**
4. **Grant admin consent.**

Note the **Directory (tenant) ID** and **Application (client) ID**.

## 3. Grant the app access to one site

`Sites.Selected` starts with zero access. A SharePoint or Global admin grants `write` on the
target site. With Microsoft Graph PowerShell:

```powershell
Connect-MgGraph -Scopes 'Sites.FullControl.All'

$site = Get-MgSite -SiteId 'contoso.sharepoint.com:/sites/Operations'
New-MgSitePermission -SiteId $site.Id -BodyParameter @{
    roles               = @('write')
    grantedToIdentities = @(@{ application = @{ id = '<client-id>'; displayName = 'report-publisher' } })
}
```

Revoking access is just as easy: `Remove-MgSitePermission`.

## 4. Configure the tool

```bash
export GRAPH_TENANT_ID="<tenant-id>"
export GRAPH_CLIENT_ID="<client-id>"
export GRAPH_CERT_PATH="/secure/path/report-publisher.key"
export GRAPH_CERT_THUMBPRINT="<sha1-thumbprint>"

report-publisher export.csv --site https://contoso.sharepoint.com/sites/Operations
```

`GRAPH_CLIENT_SECRET` works in place of the two certificate variables, but use it only for a
quick test.

## Troubleshooting

| Error | Usual cause |
|---|---|
| `AADSTS700027` / `AADSTS7000215` | Wrong thumbprint or secret, or the certificate uploaded to a different app |
| `403 accessDenied` on the site lookup | `Sites.Selected` consented but step 3 (site grant) not done |
| `library 'X' not found (available: …)` | Use the library's display name shown in the error, e.g. `Documents` |
| `429` in verbose logs | Throttling. The tool retries automatically and honours `Retry-After`. |

## Running it weekly

Any scheduler works (cron, Task Scheduler, Azure Automation, GitHub Actions). In GitHub
Actions, store the certificate and IDs as **encrypted repository secrets**, write the key to a
temp file at runtime, and run on a `schedule:` trigger such as `0 13 * * 1` (Mondays 13:00 UTC).
