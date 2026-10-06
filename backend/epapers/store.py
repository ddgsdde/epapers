import json

import aiosqlite


class Store:
    def __init__(self, path):
        self.path = path
        self.db = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        await self.db.executescript(
            "PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind,id)); CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, level TEXT, message TEXT, device_id TEXT);"
        )
        await self.db.commit()

    async def put(self, kind, key, value):
        await self.db.execute(
            "INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value",
            (kind, key, json.dumps(value, ensure_ascii=False)),
        )
        await self.db.commit()

    async def get(self, kind, key):
        async with self.db.execute(
            "SELECT value FROM records WHERE kind=? AND id=?", (kind, key)
        ) as c:
            row = await c.fetchone()
            return json.loads(row[0]) if row else None

    async def all(self, kind):
        async with self.db.execute("SELECT id,value FROM records WHERE kind=?", (kind,)) as c:
            return {row[0]: json.loads(row[1]) for row in await c.fetchall()}

    async def delete(self, kind, key):
        await self.db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, key))
        await self.db.commit()

    async def event(self, ts, message, device_id=None, level="info"):
        await self.db.execute(
            "INSERT INTO events(ts,level,message,device_id) VALUES(?,?,?,?)",
            (ts, level, message, device_id),
        )
        await self.db.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)"
        )
        await self.db.commit()

    async def events(self):
        async with self.db.execute(
            "SELECT ts,level,message,device_id FROM events ORDER BY id DESC LIMIT 100"
        ) as c:
            return [
                dict(zip(["ts", "level", "message", "device_id"], row))
                for row in await c.fetchall()
            ]

    async def close(self):
        if self.db:
            await self.db.close()
