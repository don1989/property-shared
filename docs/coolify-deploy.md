# Coolify Deployment Guide

Step-by-step for hosting Property Shared on a self-hosted
[Coolify](https://coolify.io/) instance. Two services off the same repo
(REST API + MCP), both auto-deployed on push to `main`.

> Replaces the previous Fly.io setup. See `.github/workflows/release.yml`
> — it now only publishes to PyPI; deploys are handled by Coolify webhooks,
> not GitHub Actions.

## What you'll have when you're done

| Service | Container | URL example | What it is |
|---|---|---|---|
| `property-shared` | `Dockerfile` | `https://api.yourdomain.com` | FastAPI REST API — `/v1/health`, `/v1/ppd/...`, etc. |
| `propertydata` | `Dockerfile.app` | `https://mcp.yourdomain.com` | FastMCP MCP server — `/mcp` endpoint that Claude connects to |

Both fit comfortably on a single 2 GB VPS (Hetzner CX22 ~€5/mo,
DigitalOcean $6 droplet, or anything similar). curl_cffi handles Zoopla
without a browser, so no chromium / extra RAM bloat.

## 1. Provision a VPS

Any Linux x86_64 box with:
- ≥ 2 GB RAM (1 GB if you're sure you'll never run more than the two app containers)
- ≥ 20 GB disk
- Public IPv4
- SSH access as root (or a user with sudo)

Open ports `80`, `443`, and `22` in the provider's firewall. Coolify needs
nothing else inbound from the internet.

## 2. Install Coolify

SSH into the VPS and run:

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | sudo bash
```

Wait ~3-5 min. Coolify pulls its Docker images, sets up Caddy as the
inbound reverse proxy, and prints the admin URL. Browse to
`http://<vps-ip>:8000`, set the admin email/password, and finish the
onboarding wizard.

## 3. Point DNS at the VPS

Add two A records in your DNS provider (Cloudflare / Route 53 / etc.):

```
api.yourdomain.com   A   <vps-ip>
mcp.yourdomain.com   A   <vps-ip>
```

Coolify provisions Let's Encrypt certificates automatically once it sees
HTTP-01 challenges succeed for those names.

## 4. Add the GitHub repo as a Source

Coolify dashboard → **Sources** → **+ New Source** → GitHub App.

Follow the prompts to install Coolify's GitHub App on `don1989/property-shared`
(or your fork). Coolify will then have read access to the repo and can
react to push webhooks.

## 5. Create the REST API service (`property-shared`)

Coolify dashboard → **Projects** → **+ New** → **Application** → **Public Repository**
or **Private Repository (via GitHub App)**.

| Field | Value |
|---|---|
| Repository | `don1989/property-shared` |
| Branch | `main` |
| Build Pack | **Dockerfile** |
| Dockerfile location | `Dockerfile` |
| Port | `8080` |
| Healthcheck path | `/v1/health` |
| Domain | `https://api.yourdomain.com` |

In the **Environment Variables** tab add:

```
EPC_API_EMAIL=<your registered email>
EPC_API_KEY=<your EPC key>
COMPANIES_HOUSE_API_KEY=<your CH key>
RIGHTMOVE_DELAY_SECONDS=0.6
```

Hit **Deploy**. First build takes ~3 min (uv install + bytecode compile);
subsequent rebuilds are ~30s thanks to layer caching.

## 6. Create the MCP service (`propertydata`)

Same flow as step 5, but:

| Field | Value |
|---|---|
| Repository | `don1989/property-shared` (same repo) |
| Branch | `main` |
| Build Pack | **Dockerfile** |
| **Dockerfile location** | `Dockerfile.app` |
| Port | `8080` |
| Healthcheck path | `/health` |
| Domain | `https://mcp.yourdomain.com` |

Environment variables:

```
MCP_TRANSPORT=http              # CRITICAL — defaults to stdio otherwise
MCP_PUBLIC_URL=https://mcp.yourdomain.com
FASTMCP_HOST=0.0.0.0
FASTMCP_PORT=8080
EPC_API_EMAIL=<same as above>
EPC_API_KEY=<same as above>
COMPANIES_HOUSE_API_KEY=<same as above>
```

`MCP_PUBLIC_URL` is the canonical origin for this service — it's used as
the Prefab CSP allowlist domain and as the base for the `/img` proxy
URLs surfaced in test components. Without it, those defaults to
`http://localhost:8080`.

> `MCP_TRANSPORT=http` is mandatory. Without it, `property_app/server.py`
> defaults to stdio mode and the container never opens a TCP listener,
> so Coolify's healthcheck times out and the service stays unhealthy.

## 7. Wire up auto-deploy on push

In each application's settings, under **Webhooks**, copy the deploy
webhook URL Coolify generated. Paste each one into
**GitHub repo → Settings → Webhooks → Add webhook**:

- Payload URL: the Coolify webhook URL
- Content type: `application/json`
- Events: **Just the `push` event**

Now any push to `main` triggers a rebuild + zero-downtime swap on Coolify.

## 8. Connect Claude to the MCP

### claude.ai (web)

Settings → Connectors → Add custom connector.
URL: `https://mcp.yourdomain.com/mcp`. No auth required (the MCP server
is unauthenticated by design — protect with a Coolify-level basic-auth
middleware if you want to lock it down).

### Claude Code (CLI)

```bash
claude mcp add --transport http property-shared https://mcp.yourdomain.com/mcp
```

Then `/mcp` inside a Claude Code session lists the tools.

## 9. Sanity-check the deploy

```bash
curl -s https://api.yourdomain.com/v1/health
# {"status":"ok"}

curl -s https://mcp.yourdomain.com/health
# {"status":"ok"}

curl -s "https://api.yourdomain.com/v1/zoopla/listing/72192746" | jq '.result.price, .result.tenure'
# 2389000
# "Freehold"
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Coolify health-check times out on `propertydata` | `MCP_TRANSPORT` not set to `http` | Add the env var, redeploy |
| `502 Bad Gateway` on first request after deploy | App still starting (cold uv import) | Wait ~5 s; first request warms it |
| Zoopla returns `502 Zoopla blocked (likely Cloudflare)` from one specific VPS | Provider IP range flagged by CF | Try a different impersonate profile (`safari17_2_ios`, `firefox133`) by passing `?impersonate=...` once we expose it; or set up a residential proxy |
| Out of memory during build | 1 GB VPS | Bump to 2 GB; uv compile step is the spike |
| Let's Encrypt cert never issues | DNS not pointing at the VPS, or port 80 blocked | `dig api.yourdomain.com` to confirm A record; check provider firewall |

## Tearing down the old Fly setup (when you're ready)

```bash
fly apps destroy property-shared
fly apps destroy propertydata
```

Then in GitHub repo settings → Secrets, remove `FLY_API_TOKEN` and
`FLY_API_TOKEN_PROPERTYDATA`. Done.
