"""OUI lookup data.

Core ships an empty table by design — the discovery rewrite removed
the curated AV-vendor list (principle 3: zero manufacturer knowledge
in core). At runtime the table is populated from each loaded driver's
``discovery.oui:`` hint via ``OUIDatabase.add_prefix``. The community
catalog feeds the same code path so un-installed drivers contribute
OUIs too — that's how scan results show a friendly vendor name on a
device whose driver hasn't been installed yet.
"""

# Empty by default. Populated at runtime from driver hints.
AV_OUI_TABLE: dict[str, tuple[str, str]] = {}
