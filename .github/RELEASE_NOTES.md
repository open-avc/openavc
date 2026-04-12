## Device Discovery

Fixed a performance regression in v0.5.10 that caused discovery scans to run slowly and miss device identification. Port scanning now uses a staggered connection approach that reliably detects PJLink projectors and other embedded AV devices without slowing down the overall scan.
