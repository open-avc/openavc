"""The services that run beside the project: the ISC mesh, the mDNS
advertiser, panel software updates and the cloud agent. Each is started once
in a start (core/engine_lifecycle.py names the order), and the ISC mesh and
the advertiser follow a settings change without a restart
(``reconcile_runtime_services``, which ``PATCH /system/config`` calls; a
project edit that touches ISC reloads it through the reconcile).

Every one isolates its own failure: a service that cannot start is logged and
left as ``None``, and the engine runs on without it. The ISC and advertise
switches are read from the system config live, never an import-time copy, so a
toggle is honored on the next reconcile; the cloud agent's pairing, and the
advertiser's ports and TLS, come from the ``openavc.config`` mirror. ISC runs
behind two switches, the system's and the project's, and ``isc_gates`` is the
one place both are read (``/api/isc/status`` asks it which one is down).

Mixed into Engine (core/engine.py). Logs under the engine's name.
"""

from __future__ import annotations

from openavc import config
from openavc.core.device_logins import saved_isc_key
from openavc.utils.logger import get_logger
from openavc.version import __version__

log = get_logger("openavc.core.engine")


def _plug_in_isc(manager) -> None:
    """Hand ``manager`` (or ``None``) to the two places that reach the mesh:
    the ISC WebSocket endpoint and the script API's ``isc`` proxy."""
    from openavc.api.isc_ws import set_isc_manager
    from openavc.core.script_api import isc as isc_proxy

    set_isc_manager(manager)
    isc_proxy._bind(manager)


class RuntimeServicesMixin:
    """Starting, reconciling and stopping the services beside the project."""

    def isc_gates(self) -> tuple[bool, bool]:
        """The two switches ISC runs behind: (system config, this project).

        Two gates for one feature is an afternoon lost unless something can say
        WHICH one is down — `/api/isc/status` asks here for exactly that. The
        system-config value is read live rather than from an import-time
        constant, so a `PATCH /system/config` toggle is honored on a reconcile
        without a restart.
        """
        from openavc.system_config import get_system_config
        system_enabled = bool(get_system_config().get("isc", "enabled", True))
        project_enabled = bool(self.project and self.project.isc.enabled)
        return system_enabled, project_enabled

    async def _start_isc(self) -> None:
        """Initialize ISC if enabled in both system config and project."""
        system_enabled, project_enabled = self.isc_gates()
        # project_enabled is False with no project loaded (isc_gates).
        if not (system_enabled and project_enabled):
            return
        try:
            from openavc.core.isc import ISCManager
            instance_id = self.instance_id
            instance_name = self.project.project.name
            self.isc = ISCManager(
                state=self.state,
                events=self.events,
                devices=self.devices,
                shared_state_patterns=self.project.isc.shared_state,
                auth_key=saved_isc_key(self.project, self.device_logins),
                instance_id=instance_id,
                instance_name=instance_name,
                http_port=config.HTTP_PORT,
                manual_peers=self.project.isc.peers,
                allowed_remote_commands=self.project.isc.allowed_remote_commands,
            )
            await self.isc.start()
            _plug_in_isc(self.isc)
        except Exception:  # Catch-all: isolates ISC subsystem errors from core startup
            log.exception("ISC: Failed to start — continuing without ISC")
            self.isc = None

    async def _reload_isc(self) -> None:
        """Reload ISC configuration after project change."""
        if not self.project:
            return
        system_enabled, project_enabled = self.isc_gates()
        isc_should_run = system_enabled and project_enabled
        if self.isc and isc_should_run:
            await self._hand_isc_settings()
        elif self.isc:
            # ISC was running but project disabled it
            await self._stop_isc()
        elif isc_should_run:
            # ISC was off but project enabled it
            await self._start_isc()

    async def _hand_isc_settings(self) -> None:
        """The running mesh, handed the project's edited settings."""
        await self.isc.reload(
            shared_state_patterns=self.project.isc.shared_state,
            auth_key=saved_isc_key(self.project, self.device_logins),
            manual_peers=self.project.isc.peers,
            allowed_remote_commands=self.project.isc.allowed_remote_commands,
        )

    async def _stop_isc(self) -> None:
        await self.isc.stop()
        self.isc = None
        # The script API's proxy is unplugged too. Otherwise scripts calling
        # isc.send_to() / isc.broadcast() reach the stopped manager and
        # surface a misleading ConnectionError instead of the intended
        # "ISC not enabled" RuntimeError.
        _plug_in_isc(None)

    async def _start_mdns_advertiser(self) -> None:
        """Start mDNS service advertisement if enabled in system config."""
        # Live system-config read (not an import-time constant) so a PATCH
        # /system/config toggle is honored on reconcile.
        if not self._mdns_wanted():
            return
        try:
            from openavc.discovery.mdns_advertiser import MDNSAdvertiser

            # Announce only where panels can connect: on the chosen adapter
            # alone, or every adapter for any network.
            plan = config.listen_plan()
            instance_id = self.instance_id
            self.mdns_advertiser = MDNSAdvertiser(
                instance_name=self.project.project.name,
                instance_id=instance_id,
                http_port=config.HTTP_PORT,
                version=__version__,
                tls_enabled=config.TLS_ENABLED,
                tls_port=config.TLS_PORT,
                interface_ips=(
                    list(plan.network_addresses) if plan.mode == "adapter" else None
                ),
            )
            await self.mdns_advertiser.start()
        except Exception:
            log.exception("mDNS advertiser: failed to start — continuing without advertisement")
            self.mdns_advertiser = None

    def _mdns_wanted(self) -> bool:
        """Advertise is on, and something on the network can connect: with
        this computer only (or a chosen adapter that is missing) there is
        nothing to announce."""
        from openavc.system_config import get_system_config

        if not get_system_config().get("discovery", "advertise", True):
            return False
        return not config.listen_plan().local_only

    async def _reconcile_mdns(self) -> None:
        """Start or stop the mDNS advertiser to match the live system config."""
        should_run = self._mdns_wanted()
        if should_run and not self.mdns_advertiser:
            await self._start_mdns_advertiser()
        elif not should_run and self.mdns_advertiser:
            await self.mdns_advertiser.stop()
            self.mdns_advertiser = None

    async def reconcile_runtime_services(self) -> None:
        """Re-evaluate ISC and mDNS advertisement against the live system
        config so a ``PATCH /system/config`` toggle takes effect without a
        restart. Safe to call when nothing changed (each branch is a no-op if
        the subsystem already matches the desired state)."""
        await self._reload_isc()
        await self._reconcile_mdns()

    async def _start_panel_software(self) -> None:
        """Panel software updates on appliance hardware, once the update
        manager is up: they download through it. Says and does nothing on a
        system whose device supervisor has not declared itself."""
        if self.update_manager is None:
            return
        try:
            from openavc.updater.panel_software import PanelSoftwareUpdates
            self.panel_software = PanelSoftwareUpdates(
                self.state, self.update_manager,
                display_settings=lambda: getattr(
                    getattr(self.project, "settings", None), "display", None
                ),
            )
            await self.panel_software.start()
            if self.cloud_agent and getattr(self.cloud_agent, "_command_handler", None):
                self.cloud_agent._command_handler._panel_software = self.panel_software
        except Exception:
            log.exception("Panel software updates failed to start — continuing without them")
            self.panel_software = None

    async def _stop_panel_software(self) -> None:
        if self.panel_software:
            await self.panel_software.stop()

    async def _start_cloud_agent(self) -> None:
        """Initialize the cloud agent if enabled in system config."""
        if not config.CLOUD_ENABLED:
            return
        if not config.CLOUD_SYSTEM_KEY or not config.CLOUD_SYSTEM_ID:
            log.warning("Cloud agent: enabled but missing system_key or system_id, skipping")
            return
        try:
            from openavc.cloud.agent import CloudAgent
            from openavc.cloud.heartbeat import HeartbeatCollector
            from openavc.cloud.state_relay import StateRelay
            from openavc.cloud.command_handler import CommandHandler
            from openavc.cloud.alert_monitor import AlertMonitor
            from openavc.cloud.tunnel import TunnelHandler

            cloud_config = {
                "endpoint": config.CLOUD_ENDPOINT,
                "system_key": config.CLOUD_SYSTEM_KEY,
                "system_id": config.CLOUD_SYSTEM_ID,
                "heartbeat_interval": config.CLOUD_HEARTBEAT_INTERVAL,
                "state_batch_interval": config.CLOUD_STATE_BATCH_INTERVAL,
            }

            self.cloud_agent = CloudAgent(self.state, self.events, self.devices, cloud_config)

            # Wire subsystems
            heartbeat = HeartbeatCollector(
                self.state, self.devices,
                ws_client_count_fn=lambda: self.ws.client_count,
                # The same clock `/api/status` reports uptime from, so the
                # portal and the instance cannot disagree about how long this
                # room has been up.
                start_time=self._start_time,
            )
            self.cloud_agent.set_heartbeat_collector(heartbeat)

            relay = StateRelay(self.cloud_agent, self.state)
            self.cloud_agent.set_state_relay(relay)

            # At startup both updaters are still None here and are handed over
            # when they start; a system paired while running has them already.
            handler = CommandHandler(
                self.cloud_agent, self.devices, self.events,
                reload_fn=self.reload_project,
                apply_fn=self.apply_project,
                update_manager=self.update_manager,
                project_path=self.project_path,
                panel_software=self.panel_software,
            )
            self.cloud_agent.set_command_handler(handler)

            from openavc.cloud.ai_tool_handler import AIToolHandler
            ai_tool_handler = AIToolHandler(
                self.cloud_agent, self.devices, self.events,
                project_path=self.project_path,
            )
            self.cloud_agent.set_ai_tool_handler(ai_tool_handler)

            alert_monitor = AlertMonitor(
                self.cloud_agent, self.state, self.events,
                monitors_provider=self.monitors,
                away_on_purpose=self.devices.away_on_purpose,
            )
            self.cloud_agent.set_alert_monitor(alert_monitor)

            tunnel_handler = TunnelHandler(self.cloud_agent)
            self.cloud_agent.set_tunnel_handler(tunnel_handler)

            from openavc.cloud.cert_manager import CertificateManager
            from openavc.system_config import get_system_config
            cert_manager = CertificateManager(self.cloud_agent, get_system_config())
            self.cloud_agent.set_cert_manager(cert_manager)

            # So an acknowledgement lands on the panel that asked.
            self.cloud_agent.set_help_requests(self.help)

            # Connect (runs in background)
            await self.cloud_agent.connect()
            log.info("Cloud agent: initialized and connecting")
        except Exception:  # Catch-all: isolates cloud subsystem errors from core startup
            log.exception("Cloud agent: failed to start — continuing without cloud")
            self.cloud_agent = None
