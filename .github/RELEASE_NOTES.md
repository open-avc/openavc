## UDP Transport

UDP is now a first-class transport option for drivers. YAML drivers can use `transport: udp` with configurable local/remote ports, broadcast support, and hex encoding. The Driver Builder UI includes UDP configuration, and the simulator supports UDP device simulation. This enables drivers for protocols like Wake-on-LAN, Art-Net, and other datagram-based AV control.

## Cloud Tunnel Fixes

Fixed an issue where the Panel and Programmer UIs would fail to load through cloud remote access tunnels when the server runs on a non-default port. Images and other assets now load correctly through tunnels.
