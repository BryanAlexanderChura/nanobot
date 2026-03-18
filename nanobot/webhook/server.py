"""HTTP webhook server using aiohttp.

This module provides a lightweight HTTP server for receiving webhooks from
external services (Evolution API, CRM, etc.). It is designed to be replaceable
— migrating to FastAPI means swapping this file while keeping the same
start_webhook_server() function signature.

See nanobot/webhook/README.md for architecture details and migration guide.
"""

import asyncio

from aiohttp import web
from loguru import logger

from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import GatewayConfig
from nanobot.webhook.routes import setup_routes


async def start_webhook_server(
    config: GatewayConfig,
    bus: MessageBus,
    channels: dict[str, BaseChannel],
) -> None:
    """Start the webhook HTTP server.

    This function runs indefinitely. Add it to asyncio.gather() alongside
    other long-running tasks in the gateway command.

    Args:
        config: Gateway configuration (host, port).
        bus: Message bus for publishing inbound messages.
        channels: Dict of active channel instances (keyed by channel name).
    """
    app = web.Application()
    app["bus"] = bus
    app["channels"] = channels
    app["config"] = config

    setup_routes(app)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, config.host, config.port)
    await site.start()

    logger.info("Webhook server listening on http://{}:{}", config.host, config.port)

    # Run forever until cancelled
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
        logger.info("Webhook server stopped")
