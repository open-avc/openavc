"""Root landing hub.

Served at GET / (the bare host:port). Without this, hitting the root path
returns FastAPI's default {"detail":"Not Found"} JSON 404, which reads like
a broken install even though the server is fine and the real entry points
are /panel and /programmer.

This page is navigation only: OpenAVC branding, the instance (project) name
and version, and two buttons that link to the touch panel and the Programmer.
It renders no device, project, or instance secrets and fetches no live data,
so it adds no attack surface beyond the fixed paths the instance already
advertises (mDNS, /pair, /setup). Each destination still enforces its own
auth (Programmer login, panel lock code).

On a dedicated-panel / kiosk / appliance deployment the root redirects
straight to /panel: an end user standing at a wall-mounted panel should land
on the room controls, not a hub that offers the Programmer.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from server.api._engine import _get_engine
from server.system_config import get_system_config
from server.updater.platform import DeploymentType, detect_deployment_type
from server.version import __version__

router = APIRouter()


def _is_panel_only_deployment() -> bool:
    """Whether the bare root should send visitors straight to the panel.

    True when this instance drives its own panel display: kiosk mode is
    enabled (a Pi image or dedicated panel configured to show the panel), or
    the deployment is the all-in-one appliance, which is panel-first by
    nature. A general server or dev instance returns False and shows the hub.
    """
    try:
        if bool(get_system_config().get("kiosk", "enabled", False)):
            return True
    except Exception:
        pass
    try:
        return detect_deployment_type() == DeploymentType.ANDROID_APPLIANCE
    except Exception:
        return False


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1a1a2e">
<title>{title}</title>
<style>
  :root {{
    color-scheme: dark light;
    --bg: #1a1a2e;
    --text: #ffffff;
    --muted: rgba(255,255,255,0.6);
    --accent: #8AB493;
    --accent-hover: #7aa483;
    --surface: #16213e;
    --border: rgba(255,255,255,0.12);
  }}
  @media (prefers-color-scheme: light) {{
    :root {{
      --bg: #f7f7fa;
      --text: #1a1a2e;
      --muted: rgba(26,26,46,0.6);
      --surface: #ffffff;
      --border: rgba(26,26,46,0.12);
    }}
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
    -webkit-font-smoothing: antialiased;
  }}
  .card {{
    max-width: 420px; width: 100%;
    text-align: center;
  }}
  .logo {{
    font-size: 1.75rem;
    font-weight: 700;
    margin-bottom: 8px;
    letter-spacing: 0.01em;
  }}
  .logo span {{ color: var(--accent); }}
  .project {{
    font-size: 1rem;
    color: var(--muted);
    margin-bottom: 40px;
    word-break: break-word;
  }}
  .btn {{
    display: block;
    width: 100%;
    padding: 16px 24px;
    border-radius: 12px;
    font-size: 1rem;
    font-weight: 500;
    text-decoration: none;
    border: 1px solid var(--border);
    cursor: pointer;
    margin-bottom: 12px;
    font-family: inherit;
    transition: background 0.15s;
    color: var(--text);
    background: var(--surface);
  }}
  .btn:hover {{ border-color: var(--accent); }}
  .btn-primary {{
    background: var(--accent); color: #fff; border-color: var(--accent);
  }}
  .btn-primary:hover {{ background: var(--accent-hover); }}
  .btn-primary:active {{ opacity: 0.85; }}
  .btn .sub {{
    display: block;
    font-size: 0.8rem;
    font-weight: 400;
    opacity: 0.75;
    margin-top: 2px;
  }}
  .footer {{
    margin-top: 24px;
    font-size: 0.9rem;
    color: var(--muted);
    line-height: 1.5;
  }}
  .footer a {{
    color: var(--accent);
    text-decoration: none;
    font-weight: 500;
  }}
  .footer a:hover {{ text-decoration: underline; }}
  .version {{
    margin-top: 32px;
    font-size: 0.75rem;
    color: var(--muted);
    opacity: 0.7;
  }}
</style>
</head>
<body>
<main class="card">
  <div class="logo">Open<span>AVC</span></div>
  <div class="project">{project_name}</div>
  <a class="btn btn-primary" href="/panel">Open Touch Panel<span class="sub">Control this space</span></a>
  <a class="btn" href="/programmer">Open Programmer<span class="sub">Configure devices, UI, and automation</span></a>
  <p class="footer">
    Setting up this controller? <a href="/setup">Device setup</a>
  </p>
  <div class="version">v{version}</div>
</main>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def root_landing(request: Request):
    """Landing hub for the bare root path.

    On a panel-only deployment (kiosk / appliance) redirect straight to the
    touch panel. Otherwise render a small hub linking to the panel and the
    Programmer. The page is static and shows only the project name + version,
    which the instance already discloses via /pair and /setup.
    """
    if _is_panel_only_deployment():
        return RedirectResponse(url="/panel", status_code=302)

    project_name = "OpenAVC"
    version = __version__
    try:
        status = _get_engine().get_status(include_sensitive=False)
        project_name = str(status.get("project_name") or "OpenAVC")
        version = str(status.get("version") or __version__)
    except Exception:
        # The hub must never 500 — it is the friendliest possible entry
        # point. Fall back to the bare brand if the engine is unavailable.
        pass

    page = _PAGE_TEMPLATE.format(
        title=f"{html.escape(project_name)} | OpenAVC",
        project_name=html.escape(project_name),
        version=html.escape(version),
    )
    return HTMLResponse(page)
