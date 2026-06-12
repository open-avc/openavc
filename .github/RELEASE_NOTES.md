# OpenAVC v0.17.0

A reliability release focused on device discovery.

- Discovery no longer depends on system network tools. The server sends ICMP
  echo itself where the OS allows and falls back to the system ping command,
  so minimal and locked-down installs can run a full scan.
- The mDNS, SSDP, and AMX beacon listeners join multicast on every network
  interface, so multi-NIC systems hear every attached network. The pairing
  announcement the Panel app listens for does the same, and retries if the
  network comes up after the server starts.
- If the environment blocks part of a scan, the Discovery view says what
  could not run instead of showing an empty result.
- Wired IPv4 changes from Settings or the setup screen follow the platform's
  apply behavior, including platforms that restart to apply.
- Windows installer fix: the server bundle is always installed.
