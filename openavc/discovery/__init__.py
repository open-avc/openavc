"""Device discovery module for OpenAVC.

Scans the local network using a mix of passive and active methods:
- Ping sweep + ARP table harvest with OUI lookup
- Async TCP port scanning + banner grab
- mDNS / DNS-SD passive listening
- SSDP / UPnP discovery
- AMX-DDP beacon listening
- SNMP device identification
- Driver-declared TCP / UDP probes and Python companions

Core ships no manufacturer-specific identification logic. Every
fingerprint and hint comes from a driver's discovery declaration.
"""
