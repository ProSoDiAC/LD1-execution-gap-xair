#!/usr/bin/env python3
"""Emulated cell controller (PLC-style) on OPC UA, owner of the line state.

The controller holds the line state and a version that advances on every
change of it. Commands are queued and executed at the next scan tick, so the
check and the effect of a command happen in one scan, without interleaving any
state change:

  SetLineState(value) -> version      operator / HMI write (applied at once)
  Resume(intent_id) -> result         unconditional command
  ConditionalResume(intent_id, expected_version, deadline_epoch_ms) -> result
                                      executed only if the version is still
                                      ``expected_version`` and the deadline has
                                      not passed at the scan that executes it
  PredicateResume(intent_id, deadline_epoch_ms) -> result
                                      executed only if the line state is RUN
                                      (and the deadline has not passed) at the
                                      scan that executes it: a controller-local
                                      interlock on the state, without a version

``result`` is "executed:<line state at the effect>:<version>" or
"refused:<reason>:<version>". A bridge replicates every state change to the
XAIR snapshot asynchronously, as a subscription would, so XAIR's copy lags the
controller by the bridge latency.

Usage: python scripts/cell_controller.py [--port 4840] [--scan-ms 10] [--xair http://xair:8080]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import urllib.request

from asyncua import Server, ua
from asyncua.common.methods import uamethod

logging.getLogger("asyncua").setLevel(logging.ERROR)


class Cell:
    def __init__(self, xair: str | None) -> None:
        self.state, self.version = "RUN", 0
        self.pending: list[tuple[str, int | None, float | None, asyncio.Future, bool]] = []
        self.bridge_q: asyncio.Queue = asyncio.Queue()
        self.xair = xair.rstrip("/") if xair else None
        self.effects = 0

    def set_state(self, value: str) -> int:
        if value != self.state:
            self.state = value
            self.version += 1
            self.bridge_q.put_nowait((self.state, self.version))
        return self.version

    def scan(self) -> None:
        """One scan: execute queued commands against the state as it is now."""
        now = time.time() * 1000.0
        batch, self.pending = self.pending, []
        for _intent_id, expected, deadline, fut, needs_run in batch:
            if expected is not None and expected != self.version:
                fut.set_result(f"refused:version_changed:{self.version}")
            elif needs_run and self.state != "RUN":
                fut.set_result(f"refused:predicate_false:{self.version}")
            elif deadline is not None and now > deadline:
                fut.set_result(f"refused:deadline:{self.version}")
            else:
                self.effects += 1
                fut.set_result(f"executed:{self.state}:{self.version}")

    async def bridge(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            state, version = await self.bridge_q.get()
            if not self.xair:
                continue
            body = json.dumps({"line": {"state": state, "version": version}}).encode()
            req = urllib.request.Request(f"{self.xair}/v1/context/snapshot", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=5).read())
            except Exception:
                pass


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4840)
    ap.add_argument("--scan-ms", type=float, default=10.0)
    ap.add_argument("--xair", default=None)
    args = ap.parse_args()
    cell = Cell(args.xair)

    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://0.0.0.0:{args.port}/cell/")
    idx = await server.register_namespace("urn:xair:cell")
    obj = await server.nodes.objects.add_object(idx, "Cell")

    @uamethod
    async def set_line_state(parent, value: str):
        return cell.set_state(value)

    async def enqueue(intent_id, expected, deadline, needs_run=False):
        fut = asyncio.get_running_loop().create_future()
        cell.pending.append((intent_id, expected, deadline, fut, needs_run))
        return await fut

    @uamethod
    async def resume(parent, intent_id: str):
        return await enqueue(intent_id, None, None)

    @uamethod
    async def conditional_resume(parent, intent_id: str, expected_version: int, deadline_epoch_ms: float):
        return await enqueue(intent_id, int(expected_version),
                             float(deadline_epoch_ms) if deadline_epoch_ms > 0 else None)

    @uamethod
    async def predicate_resume(parent, intent_id: str, deadline_epoch_ms: float):
        return await enqueue(intent_id, None, float(deadline_epoch_ms) if deadline_epoch_ms > 0 else None, True)

    @uamethod
    async def get_status(parent):
        return f"{cell.state}:{cell.version}:{cell.effects}"

    S, I64, D = ua.VariantType.String, ua.VariantType.Int64, ua.VariantType.Double
    await obj.add_method(idx, "SetLineState", set_line_state, [S], [I64])
    await obj.add_method(idx, "Resume", resume, [S], [S])
    await obj.add_method(idx, "ConditionalResume", conditional_resume, [S, I64, D], [S])
    await obj.add_method(idx, "PredicateResume", predicate_resume, [S, D], [S])
    await obj.add_method(idx, "GetStatus", get_status, [], [S])
    cell.bridge_q.put_nowait((cell.state, cell.version))

    async def scanner():
        period = args.scan_ms / 1000.0
        nxt = time.monotonic()
        while True:
            nxt += period
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))
            cell.scan()

    async with server:
        await asyncio.gather(scanner(), cell.bridge())


if __name__ == "__main__":
    asyncio.run(main())
