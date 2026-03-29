"""
OpenAVC Cloud Agent — optional cloud connectivity for remote management.

This module implements the client side of the OpenAVC Cloud Protocol.
The agent connects to a cloud platform via WSS, authenticates using
challenge-response (HMAC-SHA256 + HKDF), and exchanges signed messages
for monitoring, alerting, remote commands, and fleet management.

The agent is fully optional. If cloud.enabled is false in the system
config, nothing in this module is loaded or executed.
"""
