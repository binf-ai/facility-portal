# Running on Windows IIS

IIS cannot host an ASGI app directly — there is no IIS module for Python/ASGI. The supported pattern is:

**uvicorn runs as a Windows service → IIS reverse-proxies to it.**

The app itself needs no changes; `run.py` is the service entry point.

## 1. Install

```powershell
# on the server
git clone https://github.com/binf-ai/facility-portal C:\portal
cd C:\portal
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

## 2. Run uvicorn as a Windows service (NSSM)

NSSM keeps it running across reboots and restarts it on crash — IIS app pools are not suitable for a long-lived ASGI server.

```powershell
# download nssm: https://nssm.cc/download
nssm install FacilityPortal "C:\portal\venv\Scripts\python.exe" "C:\portal\run.py"
nssm set FacilityPortal AppDirectory C:\portal
nssm set FacilityPortal AppEnvironmentExtra DATA_DIR=C:\portal\data TZ=America/New_York PORT=8590
nssm start FacilityPortal
```

Notes:
- `PORTAL_HOST` defaults to `127.0.0.1` — keep it local so only IIS is exposed.
- `DATA_DIR` holds `portal.db` and `secret.key`; point it at a folder the service can write to.
- The app is single-process/SQLite — one service instance is enough.

## 3. IIS as reverse proxy

Install **URL Rewrite** and **Application Request Routing (ARR)** on IIS, then:

1. Enable proxy: IIS manager → Application Request Routing Cache → Server Proxy Settings → Enable proxy.
2. Add a site (e.g. `portal.yourdomain.com`) with this `web.config`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="Proxy to uvicorn" stopProcessing="true">
          <match url=".*" />
          <action type="Rewrite" url="http://127.0.0.1:8590/{R:0}" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
```

HTTPS termination, auth, and Windows group restrictions can all be handled at the IIS layer if you want them; the app's own login still applies.

## 4. Alternative: skip IIS entirely

If IIS is not a hard requirement, the simplest Windows deployment is just the NSSM service serving `http://server:8590` directly, or Docker Desktop with the existing `docker-compose.yml`. IIS only adds value here as the front door (TLS, domain, corporate auth).

## Things that changed in the app for Windows

- `DATA_DIR` now defaults to a local `data/` folder on Windows instead of `/data` (Docker/Linux behavior unchanged).
- `run.py` added as the service entry point (reads `PORT` / `PORTAL_HOST` env vars).
- `zoneinfo` on Windows needs the `tzdata` package — already in `requirements.txt`.
