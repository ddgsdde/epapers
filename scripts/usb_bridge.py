"""Local USB transport for the real gateway; no simulated device acknowledgements."""

import argparse
import asyncio
import json

import serial
import websockets
from epapers.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument("--port", default="/dev/cu.usbmodem201401")
parser.add_argument("--url", default="ws://127.0.0.1:8099/ws/gateway")
args = parser.parse_args()
settings = Settings()


async def main():
    port = serial.Serial(port=None, baudrate=115200, timeout=1, write_timeout=10)
    port.dtr = False
    port.rts = False
    port.port = args.port
    port.open()

    async def send(data):
        raw = (json.dumps(data, separators=(",", ":")) + "\n").encode()
        await asyncio.to_thread(port.write, raw)

    async def read():
        while True:
            line = await asyncio.to_thread(port.readline)
            if line.startswith(b"EP:"):
                try:
                    return json.loads(line[3:])
                except (ValueError, UnicodeDecodeError):
                    continue

    try:
        while True:
            try:
                await send({"type": "hello_request"})
                hello = None
                for _ in range(60):
                    message = await asyncio.wait_for(read(), 10)
                    if message.get("type") == "hello":
                        hello = message
                        break
                if not hello:
                    raise RuntimeError("网关未返回握手")
                async with websockets.connect(
                    args.url,
                    additional_headers={"Authorization": "Bearer " + settings.gateway_token},
                    max_size=65536,
                ) as ws:
                    await ws.send(json.dumps(hello))
                    print("USB 网关已连接：" + hello["gateway_id"], flush=True)

                    async def upstream():
                        while True:
                            message = await read()
                            if message.get("type") == "hello":
                                await ws.close()
                                return
                            if message.get("type") in {
                                "heartbeat",
                                "telemetry",
                                "ack",
                                "ota_status",
                                "time_request",
                            }:
                                await ws.send(json.dumps(message))

                    async def downstream():
                        async for raw in ws:
                            await send(json.loads(raw))

                    done, pending = await asyncio.wait(
                        [asyncio.create_task(upstream()), asyncio.create_task(downstream())],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        task.result()
            except (OSError, TimeoutError, websockets.WebSocketException, RuntimeError) as err:
                print("USB 连接重试：" + type(err).__name__, flush=True)
                await asyncio.sleep(3)
    finally:
        port.close()


asyncio.run(main())
