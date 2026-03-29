"""Device discovery module for OpenAVC.

Scans the local network for AV devices using multiple methods:
- Ping sweep + ARP table harvest (MAC OUI lookup)
- Async TCP port scanning + banner grab
- Protocol-specific probes (PJLink, Extron SIS, etc.)  [Chunk 2]
- mDNS / DNS-SD passive listening                       [Chunk 4]
- SSDP / UPnP discovery                                 [Chunk 4]
- SNMP device identification                             [Chunk 5]
"""
