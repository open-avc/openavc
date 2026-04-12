## UI Builder and Panel Rendering Unified

The Programmer IDE builder and the touch panel now share a single CSS source of truth for element styling. Every element in the builder looks identical to the deployed panel, including theme colors, border radius, fonts, and spacing. No more visual drift between what you design and what you deploy.

## Slider Enhancements

Sliders now support vertical orientation, configurable thumb size, and an optional value display. Switching orientation automatically adjusts the element's grid dimensions. The track now shows a fill bar indicating the current position.

## Binding Incomplete Fix

Fixed an issue where action bindings on sliders, keypads, matrices, and other non-button elements always showed "Incomplete" even when fully configured.

## Panel Connection Status

The "Connected" indicator on the touch panel now fades out after a few seconds. "Disconnected" stays visible the entire time the panel is offline.

## Pi Kiosk Improvements

Fixed Chromium not filling the display on Raspberry Pi kiosk deployments. Added a reboot button to kiosk settings. Fixed touch scrolling and boot experience issues.

## Deployment Fixes

- Fixed driver and plugin installation failing on Linux deployments due to read-only filesystem paths
- Fixed device discovery ping sweep failing on multi-homed hosts (Docker, VPN)
- Docker image now includes ping and IP utilities for discovery
