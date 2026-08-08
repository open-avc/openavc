"""Entry point for `python -m openavc.simulator` and the `openavc-simulator` CLI."""

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
        "--device-port-base",
        type=int,
        default=None,
        help=(
            "First port handed to simulated devices (default: 19000). Move it "
            "along with --port to run a second simulator on the same machine."
        ),
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
    if args.device_port_base is not None:
        config["device_port_base"] = args.device_port_base
    config["auto_shutdown"] = not args.no_auto_shutdown

    # Store config for the FastAPI app to pick up
    from openavc.simulator import _runtime
    _runtime.startup_config = config

    # Run through an explicit Server (not uvicorn.run) so the API's shutdown
    # endpoints can flip ``should_exit`` for a graceful, cross-platform exit
    # instead of a self-SIGTERM (which is a hard kill on Windows).
    server = uvicorn.Server(
        uvicorn.Config(
            "openavc.simulator.server:app",
            host=args.host,
            port=config["ui_port"],
            log_level="info",
        )
    )
    _runtime.uvicorn_server = server
    server.run()


if __name__ == "__main__":
    main()
