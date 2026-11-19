"""Process-wide runtime facts.

Set by the entrypoint (openavc.main) when listeners actually come up; read by
API routes. Where the process is in its life (starting, ready, stopping) is
not here: the run-state record answers it (middleware/startup_splash.py).
Dependency-free on purpose so anything can import it without cycles, and one
module per process: under ``python -m openavc.main`` main.py runs twice, as
``__main__`` and as ``openavc.main``, but both copies import this one.
"""

# True when typed URLs can drop the port: either a listener owns port 80
# outright (HTTP or HTTPS bound to 80) or the best-effort port-80 convenience
# redirect bound successfully. Display surfaces must not offer port-less URLs
# unless this is set — the port-80 bind is best-effort and can lose the port
# to another application.
port80_active: bool = False
