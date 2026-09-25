"""Ждёт, пока PostgreSQL начнёт принимать соединения (хелпер для Docker entrypoint)."""

import asyncio
import os
import sys


async def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    host = "localhost"
    port = 5432
    if "@" in url and "//" in url:
        authority = url.split("//", 1)[1].split("/", 1)[0].split("@")[-1]
        if ":" in authority:
            host, port_s = authority.rsplit(":", 1)
            port = int(port_s)

    for _attempt in range(30):
        try:
            _reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return 0
        except OSError:
            await asyncio.sleep(1)
    print(f"database {host}:{port} is not reachable", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
