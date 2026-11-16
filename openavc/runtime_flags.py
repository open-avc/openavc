"""Process-wide runtime facts.

Set by the entrypoint (openavc.main): when listeners actually come up, and
when the server hears it is to stop. Read by API routes and the engine.
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

# True from the moment the main server hears a stop signal (SIGTERM from a
# service manager, Ctrl+C, Ctrl+Break) until the process ends; set by
# openavc.main's main server before uvicorn begins its graceful shutdown. That
# shutdown drains the open connections before the lifespan reaches
# Engine.stop(), so for a while the server is on its way down and nothing else
# knows it yet. Read it through Engine.stopping, which also counts the
# engine's own stop (a restart request stops the engine with no signal).
stopping: bool = False
