# Running natively on Windows IIS

IIS has no Python/ASGI module, so FastAPI cannot be hosted directly. The native path is IIS's own Python handler — **wfastcgi (FastCGI)** or **ISAPI** — which speaks WSGI. FastAPI is ASGI, so `wsgi.py` bridges it with `a2wsgi`.

The bridge is safe here: the app uses no background tasks, no websockets, no lifespan startup, and `start_response` is called with the 3-arg form that wfastcgi implements. Verified end-to-end through `a2wsgi.ASGIMiddleware` (login POST → 303, cookie session → dashboard 200, check-in POST, history 200).

## 1. Install

```powershell
git clone https://github.com/binf-ai/facility-portal C:\portal
cd C:\portal
python -m venv venv
venv\Scripts\pip install -r requirements.txt   # includes a2wsgi, wfastcgi, pywin32
```

Use **Python 3.13 or older** — `a2wsgi` is tested through 3.13.

## 2. Enable wfastcgi in IIS

```powershell
# run once, as administrator
C:\portal\venv\Scripts\wfastcgi-enable
```

Requires the IIS **CGI** feature (Application Development > CGI) and ISAPI Filters.

## 3. IIS site configuration

Create a site pointed at `C:\portal` with this `web.config`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <handlers>
      <add name="Python_FastCGI"
           path="handler.fcgi"
           verb="*"
           modules="FastCgiModule"
           scriptProcessor="C:\portal\venv\Scripts\python.exe|C:\portal\venv\Lib\site-packages\wfastcgi.py"
           resourceFilter="Any" />
    </handlers>
  </system.webServer>
  <appSettings>
    <add key="wfastcgi.application" value="wsgi.application" />
    <add key="wfastcgi.pythonExecutablePath" value="C:\portal\venv\Scripts\python.exe" />
    <add key="wfastcgi.scriptPath" value="C:\portal\wsgi.py" />
    <add key="wfastcgi.wfastcgiLogPath" value="C:\portal\data\wfastcgi.log" />
    <add key="DATA_DIR" value="C:\portal\data" />
  </appSettings>
</configuration>
```

Requests route to `handler.fcgi`; the app sees the real path via `PATH_INFO`, so all routes (`/login`, `/checkin`, `/history`, ...) work unchanged. For clean URLs, add a URL Rewrite rule rewriting `.*` to `handler.fcgi/{R:0}`.

## 4. Permissions

The IIS worker process needs write access to `C:\portal\data` — `portal.db` and `secret.key` are created there on first run:

```powershell
icacls C:\portal\data /grant "IIS AppPool\DefaultAppPool:(OI)(CI)M"
```

`DATA_DIR` is read from the environment; `wsgi.py` defaults it to the repo's `data/` folder when not set.

## 5. Alternative: ISAPI instead of FastCGI

If FastCGI is not permitted, `isapi_wsgi` (PyPI) runs the same `wsgi.application` as an ISAPI extension — same bridge, same `DATA_DIR` rules, only the IIS handler registration differs.

## Known limits of native IIS hosting

- **App pool recycling** kills the Python process. `portal.db` and `secret.key` survive (they're files), but in-memory state does not. Set recycling to a window you control, or disable it.
- **Single process, SQLite** — fine at this scale; do not run multiple worker processes against the same DB file.
- `a2wsgi` is synchronous per request; concurrency comes from IIS's FastCGI process pool (`maxInstances`), not from async inside the app.

## Alternative (not native): reverse proxy

If the native handler is blocked by policy, `run.py` + NSSM as a Windows service with IIS reverse-proxying to `http://127.0.0.1:8590` is the fallback — but that requires running a service, which is what this deployment rules out. Docker Desktop with the existing `docker-compose.yml` is the other option.

## What changed in the app for Windows

- `wsgi.py` — ASGI→WSGI bridge entry point for IIS's Python handler.
- `DATA_DIR` defaults to a local `data/` folder on Windows instead of `/data` (Docker/Linux unchanged).
- `requirements.txt` adds `a2wsgi` plus `wfastcgi`/`pywin32` on `win32` only.
- `run.py` remains for service/Docker use (reads `PORT` / `PORTAL_HOST`).
- `zoneinfo` on Windows needs `tzdata` — already in `requirements.txt`.
