"""Tests for the IR code-database parse/render glue (server/core/ir_database.py).

Only the pure, offline helpers are exercised here (index/CSV parsing and the
per-function render annotation). The network fetch and cache are not — those hit
an external service and are covered by live validation, not the core suite. All
inputs are synthetic.
"""

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
