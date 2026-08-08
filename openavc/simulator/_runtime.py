"""Shared runtime state — set by __main__, read by server.py at startup."""

startup_config: dict = {}

# The running uvicorn.Server instance, set by __main__ so the API's shutdown
# endpoints can trigger a graceful, cross-platform exit (server.should_exit)
# instead of a self-SIGTERM (a hard kill on Windows). None until __main__ runs.
uvicorn_server = None
