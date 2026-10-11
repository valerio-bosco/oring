"""Messaging gateways (see base.py). `oring gateways` runs every [[gateways]] entry of the config."""

import asyncio
import logging
import os

from .. import config


def make_gateway(conf: dict):
    kind = conf.get("type")
    if kind == "telegram":
        from .telegram import TelegramGateway
        return TelegramGateway(conf)
    raise ValueError(f"unknown gateway type '{kind}' (available: telegram)")


async def serve():
    logging.basicConfig(level=os.environ.get("ORING_LOG", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore"):                      # one line per poll otherwise
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = config.load()
    entries = [g for g in cfg.get("gateways", []) if g.get("enable", True)]
    if not entries:
        raise SystemExit("no [[gateways]] configured")
    gateways = []
    for g in entries:
        g.setdefault("language", cfg["language"])
        gateways.append(make_gateway(g))
    await asyncio.gather(*(g.run() for g in gateways))
