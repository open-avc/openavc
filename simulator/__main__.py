"""Entry point for `python -m simulator` and the `openavc-simulator` CLI."""

import argparse
import json

import uvicorn


def main():
    parser = argparse.ArgumentParser(
        prog="openavc-simulator",
        description="Simulate AV devices on the network",
    )
    parser.add_argument(
        "--config",
        help="Path to simulation config JSON file",
    )
    parser.add_argument(
        "--driver-paths",
        nargs="+",
        help="Directories to scan for driver files",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=19500,
        help="HTTP port for the simulator UI and API (default: 19500)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--no-auto-shutdown",
        action="store_true",
        help=(
            "Don't stop the simulator process when the last UI WebSocket client "
            "disconnects. Used when openavc launches the simulator as a subprocess "
            "(drivers stay connected even with no browser open)."
        ),
    )
    args = parser.parse_args()

    # Load config from file if provided
    config = {}
    if args.config:
        with open(args.config) as f:
            config = json.load(f)

    # CLI args override config file
    if args.driver_paths:
        config["driver_paths"] = args.driver_paths
    if "ui_port" not in config:
        config["ui_port"] = args.port
    config["auto_shutdown"] = not args.no_auto_shutdown

    # Store config for the FastAPI app to pick up
    from simulator import _runtime
    _runtime.startup_config = config

    uvicorn.run(
        "simulator.server:app",
        host=args.host,
        port=config["ui_port"],
        log_level="info",
    )


if __name__ == "__main__":
    main()
