"""Panel pair landing page.

Served at GET /pair (no /api prefix). This is the target of the QR code
shown in the Programmer IDE Panel Access card. It gives scanners a choice
between opening the web panel in a browser and installing the native app.

Deep-link handoff to the native mobile app (custom URL scheme / Android
intent URL) is intentionally deferred — see openavc-backlog.md §15.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from server.api._engine import _get_engine

router = APIRouter()


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
    --border: rgba(255,255,255,0.12);
  }}
  @media (prefers-color-scheme: light) {{
    :root {{
      --bg: #f7f7fa;
      --text: #1a1a2e;
      --muted: rgba(26,26,46,0.6);
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
    border: none;
    cursor: pointer;
    margin-bottom: 12px;
    font-family: inherit;
    transition: background 0.15s;
  }}
  .btn-primary {{
    background: var(--accent); color: #fff;
  }}
  .btn-primary:hover {{ background: var(--accent-hover); }}
  .btn-primary:active {{ opacity: 0.85; }}
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
  <a class="btn btn-primary" href="/panel">Open Panel</a>
  <p class="footer">
    Setting up a dedicated touch panel?
    <a href="{app_url}" target="_blank" rel="noopener noreferrer">Get the OpenAVC app</a>
    <br>
    Setting up a dedicated panel?
    <a href="{dedicated_panel_guide_url}" target="_blank" rel="noopener noreferrer">Read the setup guide</a>
  </p>
  <div class="version">v{version}</div>
</main>
</body>
</html>
"""


# Marketing page that will redirect to the right app store / download
# once the native apps ship. See openavc-mobile-panel-plan.md Phase 1/2.
_APP_DOWNLOAD_URL = "https://openavc.com/panel-app"

# Docs site hosts the Android + iOS dedicated-panel walkthroughs; the
# overview page lets users pick the platform they're on.
_DEDICATED_PANEL_GUIDE_URL = "https://docs.openavc.com/panel-app"


@router.get("/pair", response_class=HTMLResponse)
async def pair_landing(request: Request) -> HTMLResponse:
    """Landing page for QR code scanners.

    Shows the project name and a primary 'Open Panel' button. A secondary
    link points to the app download page for users who want the native app.
    """
    status = _get_engine().get_status()
    project_name = str(status.get("project_name") or "OpenAVC")
    version = str(status.get("version") or "")

    page = _PAGE_TEMPLATE.format(
        title=f"{html.escape(project_name)} | OpenAVC",
        project_name=html.escape(project_name),
        version=html.escape(version),
        app_url=_APP_DOWNLOAD_URL,
        dedicated_panel_guide_url=_DEDICATED_PANEL_GUIDE_URL,
    )
    return HTMLResponse(page)
