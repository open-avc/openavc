"""Read-only diagnosis of installed equipment, including while project control is disabled."""

import asyncio
import json
import os
import ssl
import time

import httpx
from openavc.drivers.equipment_protocol import MAX_MESSAGE_BYTES, PROTOCOL


async def test_connection(manifest):
    started = time.monotonic()
    config = manifest.get("connection", {})

    def failed(message):
        return {"success": False, "error": message, "latency_ms": None}

    if (not isinstance(config, dict)
            or not isinstance(config.get("url"), str)
            or not config["url"].startswith("https://")
            or not isinstance(config.get("token"), str)
            or not config["token"]):
        return failed("The built-in equipment connection is not configured. Check the product firmware settings.")
    try:
        tls = ssl.create_default_context(cafile=config.get("ca_file", os.environ.get("SSL_CERT_FILE")))
        async with asyncio.timeout(5), httpx.AsyncClient(
            verify=tls, timeout=5, trust_env=False,
            headers={"Authorization": "Bearer " + config["token"]},
        ) as client:
            async def read(path):
                async with client.stream("GET", config["url"] + path) as response:
                    if response.status_code in (401, 403):
                        raise ValueError("The built-in equipment connection could not be authenticated. Check the product firmware settings.")
                    if response.status_code != 200:
                        raise ValueError("The built-in equipment connection is unavailable. Check the product's status.")
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(payload) + len(chunk) > MAX_MESSAGE_BYTES:
                            raise ValueError("The built-in equipment response is invalid. Check the product firmware version.")
                        payload.extend(chunk)
                    value = json.loads(payload)
                    if not isinstance(value, dict):
                        raise ValueError("The built-in equipment response is invalid. Check the product firmware version.")
                    return value

            description = await read("/v1/description")
            if description.get("identity") != manifest["identity"] or description.get("protocol") != PROTOCOL:
                return failed("The built-in equipment identity does not match the product firmware configuration.")
            capabilities = description.get("capabilities")
            if manifest.get("integration") and (
                not isinstance(capabilities, dict) or capabilities.get("id") != manifest["integration"]
            ):
                return failed("The built-in equipment model does not match the product firmware configuration.")
            snapshot = await read("/v1/snapshot")
            if snapshot.get("identity") != manifest["identity"]:
                return failed("The built-in equipment identity changed. Check the product firmware configuration.")
    except (TimeoutError, httpx.TimeoutException):
        return failed("The built-in equipment did not respond within five seconds. Check the product's status and try again.")
    except httpx.HTTPError:
        return failed("The built-in equipment connection could not be reached securely. Check the product's status and firmware settings.")
    except (OSError, json.JSONDecodeError):
        return failed("The built-in equipment connection could not be read. Check the product firmware settings.")
    except ValueError as error:
        return failed(str(error))
    return {"success": True, "error": None, "latency_ms": round((time.monotonic() - started) * 1000, 1)}
