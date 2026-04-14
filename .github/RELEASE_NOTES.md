## Cloud Connectivity

Improved reliability of the cloud agent connection. The state relay and alert monitor now properly re-initialize when the cloud connection drops and reconnects, ensuring device state and alert rules are restored without requiring a service restart. Added reporting when tunnel connections fail on the agent side.

## Alert System

System resource alerts (disk, memory, CPU) are now managed by the cloud platform instead of being hardcoded in the agent. Thresholds, severity, and enable/disable are all configurable from the cloud portal. Threshold alerts now support hysteresis to prevent repeated notifications when a value fluctuates near the boundary.
