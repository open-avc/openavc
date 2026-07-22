"""Tests for the IR code-database parse/render glue (server/core/ir_database.py).

Only the pure, offline helpers are exercised here (index/CSV parsing and the
per-function render annotation). The network fetch and cache are not — those hit
an external service and are covered by live validation, not the core suite. All
inputs are synthetic. The /ir-db route tests below stub the database client so
they stay offline too.
"""

import pytest
from fastapi import HTTPException

import server.api.routes.ir_db as ir_db_routes
from server.core.ir_database import parse_csv, parse_index, render_function


def test_parse_index_extracts_fields_and_skips_junk():
    text = "\n".join(
        [
            "Acme/TV/1,-1.csv",
            "Acme/Set Top Box/8,0.csv",
            "",  # blank line
            "not-a-csv-line",
            "Widgetco/Projector/134,-1.csv",
            "Broken/Type/notanumber,-1.csv",  # unparseable numbers -> skipped
        ]
    )
    entries = parse_index(text)
    assert len(entries) == 3
    assert entries[0] == {
        "brand": "Acme",
        "type": "TV",
        "device": 1,
        "subdevice": -1,
        "path": "Acme/TV/1,-1.csv",
    }
    # A device type containing a space survives intact.
    assert entries[1]["type"] == "Set Top Box"
    assert entries[2]["brand"] == "Widgetco"


def test_parse_csv_skips_header_and_bad_rows():
    text = "\n".join(
        [
            "functionname,protocol,device,subdevice,function",
            "POWER,NECx2,7,7,2",
            "VOLUME UP,Sony12,1,-1,18",
            "BADROW,MissingColumns",  # too few columns -> skipped
            "NONUMS,NEC1,x,y,z",  # unparseable -> skipped
        ]
    )
    rows = parse_csv(text)
    assert len(rows) == 2
    assert rows[0] == {
        "name": "POWER",
        "protocol": "NECx2",
        "device": 7,
        "subdevice": 7,
        "function": 2,
    }
    assert rows[1]["protocol"] == "Sony12"


def test_render_function_supported():
    row = {"name": "POWER", "protocol": "Sony12", "device": 1, "subdevice": -1, "function": 21}
    out = render_function(row)
    assert out["supported"] is True
    assert out["error"] is None
    assert out["pronto"].startswith("0000 ")  # canonical learned-format Pronto


def test_render_function_unsupported_protocol():
    row = {"name": "POWER", "protocol": "RCA-38", "device": 1, "subdevice": -1, "function": 1}
    out = render_function(row)
    assert out["supported"] is False
    assert out["pronto"] is None
    assert "RCA-38" in out["error"]


# --- /ir-db/devices reachability ------------------------------------------


async def test_ir_db_devices_503_when_index_unreachable(monkeypatch):
    """An empty device list with an UNREACHABLE index is a connectivity
    failure, not 'brand has no devices'. It must 503 like /ir-db/brands, not
    return an empty 200 the UI reads as a real empty result."""
    async def _no_devices(brand):
        return []

    async def _unavailable():
        return False

    monkeypatch.setattr(ir_db_routes._db, "devices", _no_devices)
    monkeypatch.setattr(ir_db_routes._db, "available", _unavailable)

    with pytest.raises(HTTPException) as exc:
        await ir_db_routes.ir_db_devices(brand="Acme")
    assert exc.value.status_code == 503


async def test_ir_db_devices_empty_200_when_brand_has_no_codes(monkeypatch):
    """A REACHABLE index where the brand simply has no code sets returns a
    normal empty 200 — not a false 503."""
    async def _no_devices(brand):
        return []

    async def _available():
        return True

    monkeypatch.setattr(ir_db_routes._db, "devices", _no_devices)
    monkeypatch.setattr(ir_db_routes._db, "available", _available)

    result = await ir_db_routes.ir_db_devices(brand="Acme")
    assert result["brand"] == "Acme"
    assert result["devices"] == []
