"""The name a Python driver file is imported under, and dropping it."""

import sys

from openavc.drivers.python_modules import (
    forget_module,
    is_imported,
    load_python_driver_file,
    module_name,
)


def test_a_driver_file_is_imported_under_its_stem(tmp_path):
    assert module_name(tmp_path / "acme_widget.py") == "openavc_driver_acme_widget"


def test_forgetting_a_file_drops_its_module(tmp_path):
    path = tmp_path / "pin_forget_me.py"
    path.write_text(
        "from openavc.drivers.base import BaseDriver\n"
        "class ForgetMe(BaseDriver):\n"
        "    DRIVER_INFO = {'id': 'pin_forget_me'}\n",
        encoding="utf-8",
    )
    try:
        assert load_python_driver_file(path) is not None
        assert is_imported(path)
        forget_module(path)
        assert not is_imported(path)
        assert "openavc_driver_pin_forget_me" not in sys.modules
        forget_module(path)  # a second time is harmless
    finally:
        sys.modules.pop("openavc_driver_pin_forget_me", None)
