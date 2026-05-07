import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from poller import LabPoller


STATIC_DIR = Path(__file__).parent / "static"
TOPOLOGY_PATH = Path(os.environ.get("LAB_TOPOLOGY", "/lab/topology.yml"))
LAB_PREFIX = os.environ.get("LAB_PREFIX", "clab-simple-lab")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "2"))

clients: set[WebSocket] = set()
poller: LabPoller | None = None


async def broadcast(message: dict):
    if not clients:
        return
    payload = json.dumps(message)
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global poller
    poller = LabPoller(
        topology_path=TOPOLOGY_PATH,
        lab_prefix=LAB_PREFIX,
        broadcast=broadcast,
        interval=POLL_INTERVAL,
    )
    task = asyncio.create_task(poller.run())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def state():
    if poller is None:
        return {"ready": False}
    return {"ready": True, "data": poller.last_state, "nodes": poller.nodes}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    if poller is not None:
        await websocket.send_text(json.dumps({
            "type": "snapshot",
            "nodes": poller.nodes,
            "data": poller.last_state,
        }))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(websocket)
