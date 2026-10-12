"""Shared runtime state — set by __main__, read by server.py at startup."""

startup_config: dict = {}

# The running uvicorn.Server instance, set by __main__ so the API's shutdown
# endpoints can trigger a graceful, cross-platform exit (server.should_exit)
# instead of a self-SIGTERM (a hard kill on Windows). None until __main__ runs.
uvicorn_server = None

# How long the simulator's web server holds an idle HTTP connection open. The
# main server's proxy pools its connections to this server and drops an idle
# one well before this (api/simulator_proxy.py): a pool that kept one as long
# as the server does reused it at the instant the server closed it, and the
# request failed.
HTTP_KEEP_ALIVE_S = 30
