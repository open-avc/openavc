"""Process-wide runtime facts.

Set by the entrypoint (server.main) when listeners actually come up, read by
API routes and the engine's status snapshot. Dependency-free on purpose so
anything can import it without cycles.
"""

# True when typed URLs can drop the port: either a listener owns port 80
# outright (HTTP or HTTPS bound to 80) or the best-effort port-80 convenience
# redirect bound successfully. Display surfaces must not offer port-less URLs
# unless this is set — the port-80 bind is best-effort and can lose the port
# to another application.
port80_active: bool = False
