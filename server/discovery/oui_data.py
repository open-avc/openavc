"""Curated AV manufacturer OUI database.

Source: IEEE OUI public database, filtered to AV equipment manufacturers.
Format: { "OUI_PREFIX": ("Manufacturer Name", "Device Category Hint") }

Categories: projector, display, audio, camera, switcher, control, network, other
"""

AV_OUI_TABLE: dict[str, tuple[str, str]] = {
    # --- Control Systems ---
    "00:10:7f": ("Crestron", "control"),
    "00:60:9f": ("AMX", "control"),

    # --- Switchers / Signal Processing ---
    "00:05:a6": ("Extron", "switcher"),
    "00:0a:2d": ("Kramer", "switcher"),

    # --- Audio DSPs / Mixers ---
    "00:90:5e": ("Biamp", "audio"),
    "00:0c:4d": ("QSC", "audio"),
    "7c:2e:0d": ("QSC", "audio"),
    "00:0e:dd": ("Shure", "audio"),
    "00:01:e3": ("Yamaha", "audio"),
    "00:0e:58": ("Yamaha", "audio"),
    "00:a0:de": ("Yamaha", "audio"),
    "00:05:cd": ("Denon", "audio"),
    "00:1d:c1": ("Audinate/Dante", "audio"),
    "04:52:c7": ("Bose", "audio"),
    "00:09:e5": ("Crown/Harman", "audio"),
    "00:1c:ab": ("AtlasIED", "audio"),
    "80:1f:12": ("Allen & Heath", "audio"),
    "54:2a:1b": ("Sonos", "audio"),
    "78:28:ca": ("Sonos", "audio"),
    "b8:e9:37": ("Sonos", "audio"),
    "34:7e:5c": ("Sonos", "audio"),
    "48:a6:b8": ("Sonos", "audio"),
    "94:9f:3e": ("Sonos", "audio"),

    # --- Projectors ---
    # NEC
    "00:00:73": ("NEC", "projector"),
    "00:a0:b0": ("NEC", "projector"),
    "04:fe:31": ("NEC", "projector"),
    # Epson
    "00:26:ab": ("Epson", "projector"),
    "64:eb:8c": ("Epson", "projector"),
    # Panasonic (projector OUIs)
    "00:0d:4b": ("Panasonic", "projector"),
    "00:80:45": ("Panasonic", "projector"),
    "80:c5:48": ("Panasonic", "projector"),
    "d0:c1:b1": ("Panasonic", "projector"),
    # Barco
    "00:0e:d6": ("Barco", "projector"),
    "00:12:ae": ("Barco", "projector"),
    # Christie
    "00:12:3c": ("Christie", "projector"),
    "a8:b1:d4": ("Christie", "projector"),

    # --- Displays ---
    # Samsung
    "00:07:ab": ("Samsung", "display"),
    "00:12:fb": ("Samsung", "display"),
    "00:16:32": ("Samsung", "display"),
    "00:1e:e1": ("Samsung", "display"),
    "00:21:19": ("Samsung", "display"),
    "00:23:99": ("Samsung", "display"),
    "00:26:37": ("Samsung", "display"),
    "14:49:e0": ("Samsung", "display"),
    "34:c3:d2": ("Samsung", "display"),
    "58:c3:8b": ("Samsung", "display"),
    "8c:71:f8": ("Samsung", "display"),
    "ac:5a:14": ("Samsung", "display"),
    "f0:25:b7": ("Samsung", "display"),
    # LG
    "00:05:c9": ("LG", "display"),
    "00:1c:62": ("LG", "display"),
    "00:1e:75": ("LG", "display"),
    "00:22:a9": ("LG", "display"),
    "10:68:3f": ("LG", "display"),
    "20:3d:bd": ("LG", "display"),
    "2c:54:cf": ("LG", "display"),
    "34:fc:ef": ("LG", "display"),
    "58:a2:b5": ("LG", "display"),
    "a8:23:fe": ("LG", "display"),
    "bc:f5:ac": ("LG", "display"),
    # Sony (display OUIs)
    "00:01:4a": ("Sony", "display"),
    "00:0a:d9": ("Sony", "display"),
    "00:0e:07": ("Sony", "display"),
    "00:13:a9": ("Sony", "display"),
    "00:1a:80": ("Sony", "display"),
    "40:b8:9a": ("Sony", "display"),
    "54:42:49": ("Sony", "display"),
    "ac:9b:0a": ("Sony", "display"),
    "fc:f1:52": ("Sony", "display"),
    # Panasonic (display OUIs)
    "70:5a:0f": ("Panasonic", "display"),
    # Sharp / NEC (Sharp NEC Display Solutions)
    "44:a6:e5": ("Sharp/NEC", "display"),

    # --- Cameras ---
    # Sony (camera OUIs)
    "00:1d:ba": ("Sony", "camera"),
    "04:5d:4b": ("Sony", "camera"),
    # Panasonic (camera OUIs)
    "00:13:c4": ("Panasonic", "camera"),
    "00:1b:58": ("Panasonic", "camera"),
    "04:20:9a": ("Panasonic", "camera"),
    # Axis
    "00:40:8c": ("Axis", "camera"),
    "ac:cc:8e": ("Axis", "camera"),
    "b8:a4:4f": ("Axis", "camera"),
    # Vaddio
    "00:04:a5": ("Vaddio", "camera"),
    # Polycom / Poly
    "00:04:f2": ("Polycom", "camera"),
    "00:e0:db": ("Polycom", "camera"),
    "64:16:7f": ("Poly", "camera"),
    # Cisco / Tandberg
    "00:0d:ec": ("Cisco/Tandberg", "camera"),
    "00:1b:d5": ("Cisco/Tandberg", "camera"),
    # Logitech
    "00:04:5e": ("Logitech", "camera"),

    # --- Non-AV (used for filtering) ---
    # These are common network infrastructure OUIs — helps classify non-AV devices.
    # We include them so the filter can deprioritize routers/switches/etc.
    "00:17:c5": ("Cisco", "network"),
    "00:1a:a1": ("Cisco", "network"),
    "00:1b:54": ("Cisco", "network"),
    "00:18:0a": ("Cisco", "network"),
    "24:a4:3c": ("Ubiquiti", "network"),
    "44:d9:e7": ("Ubiquiti", "network"),
    "78:8a:20": ("Ubiquiti", "network"),
    "b4:fb:e4": ("Ubiquiti", "network"),
    "dc:9f:db": ("Ubiquiti", "network"),
    "f0:9f:c2": ("Ubiquiti", "network"),
    "30:b5:c2": ("TP-Link", "network"),
    "50:c7:bf": ("TP-Link", "network"),
    "a4:2b:b0": ("TP-Link", "network"),
    "28:80:88": ("Netgear", "network"),
    "a4:2b:8c": ("Netgear", "network"),
}

# Non-AV categories (used by the UI to deprioritize)
NON_AV_CATEGORIES = {"network"}
