"""Host-level system integration (network configuration, future host features).

Modules here manage the machine OpenAVC runs on, not the AV devices it
controls. Everything is backend-gated: on deployments where OpenAVC does not
own the OS (Windows, Docker, generic servers) the backends report unavailable
and the corresponding UI hides itself.
"""
