# app/main.py
from __future__ import annotations

import os, signal, time, sys
import traceback
import logging
import multiprocessing
import asyncio
from contextlib import asynccontextmanager
from typing import Optional
import urllib.parse

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .db import Stream, initialize_db, get_db, crud as db_crud
from .schemas import AssignStreamIn, StreamOut, StreamDetailOut
from .utils.host import collect_host_info
from .utils import check_url_reachable, graceful_shutdown
from .services import announce_with_retries, announce_heartbeat, MediaMTXService
from .utils.logger import configure_logging

# NEW: class-based process manager
from .engine.runner import StreamEngineManager
from config import config

logger = configure_logging()

# NEW: one manager instance for this worker
STREAM_MANAGER = StreamEngineManager(logger=logger, join_timeout=5.0, log_level=getattr(logging, config.LOGS_LEVEL.upper(), logging.INFO))
# build with settings/env
MTX_CLIENT = MediaMTXService(options=None, logger=logger)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ===== startup =====
    initialize_db(db_path=config.SQLITE_PATH, wipe_db=True)

    # wipe out all existing streams to start clean
    db = get_db()
    try:
        db.query(Stream).delete()
        db.commit()
    finally: db.close()

    host_info = collect_host_info()
    # worker_url = worker_callback_url_auto()  # auto-detected; NOT from .env

    # optional broadcast reachability check
    if config.BROADCAST_URL: 
        # get base url using urllib
        # base_url = urllib.parse.urlparse(config.BROADCAST_URL).netloc
        if not check_url_reachable(config.BROADCAST_URL):
            logger.warning(f"[startup] broadcast_url={config.BROADCAST_URL[:10]}... is not reachable")

    if await announce_with_retries(host_info=host_info):
        logger.info("[startup] Announced to controller")
        # start heartbeat loop in background
        asyncio.create_task(
            announce_heartbeat(
                interval=config.HEARTBEAT_INTERVAL,
                max_retries=config.HEARTBEAT_RETRY_ATTEMPTS,
                backoff=config.HEARTBEAT_RETRY_INTERVAL,
            )
        )
    else:
        logger.error("[startup] Failed to announce to controller. Shutting down...")
        asyncio.create_task(graceful_shutdown())

    # ===== yield to application =====
    yield

    # ===== shutdown =====
    db = get_db()
    try:
        logger.info("[shutdown] stopping all streams")
        STREAM_MANAGER.stop_all(db, force=True)
    finally: db.close()

    # remove the db file
    if os.path.exists(config.SQLITE_PATH):
        try: os.remove(config.SQLITE_PATH)
        except Exception: logger.warning("[shutdown] failed to remove sqlite file", exc_info=True)


def create_app():
    app = FastAPI(title=config.APP_NAME, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    def index():
        return {"message": "Welcome to the Video Worker API"}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # -------- Streams API --------
    @app.post("/streams/assign", response_model=StreamOut, tags=["streams"])
    def assign_stream(payload: AssignStreamIn):
        """
        Create DB row, start engine via manager, attach WebRTC URL, and return summary.
        Mirrors your old behavior but uses StreamEngineManager under the hood.
        """
        db = get_db()
        row: Optional[Stream] = None
        existing = db.query(Stream).filter(Stream.source_url == payload.source_url).first()
        if existing:
            return JSONResponse(status_code=400, content={"detail": "Stream with this source already exists."})
        try:
            # create row with minimal inputs; other defaults come from settings/db model
            row = db_crud.create_stream(db=db, stream_id=payload.stream_id, source_url=payload.source_url)
            # spawn process (manager records PID in DB via set_stream_started)
            try:
                # 1) Add camera when you assign stream. If error, delete row and raise
                ok, webrtc_url, err = MTX_CLIENT.add_camera(name=row.stream_id, source=row.source_url)
                logger.info(f"[assign_stream] added camera {row.stream_id} to mtex: {ok}")
                if not ok:
                    db_crud.delete_stream_row(db, row.stream_id) # delete row 
                    db.commit()
                    raise HTTPException(status_code=400, detail=f"Failed to register stream to mtex: {err or 'unknown'}")
                # 2) Start engine
                logger.info(f"[assign_stream] starting engine for stream {row.stream_id}")
                STREAM_MANAGER.start(db, row.stream_id)
                # row.webrtc_url = webrtc_url
                row.webrtc_url = urllib.parse.urljoin(config.MTX_WEBRTC_INTERNAL, row.stream_id)
                db.commit()
            except Exception as e:
                logger.exception(f"Failed to start stream: {e}")
                raise
            logger.info(f"[assign_stream] started stream {row.stream_id}")        
            return StreamOut.model_validate(row)
        except Exception as e:
            # mark error and clean up row
            row: Stream = db_crud.get_stream(db, payload.stream_id)
            if row:
                logger.info(f"[assign_stream] deleting stream {row.stream_id} because it failed to start: {e}")
                delete_stream_api(row.stream_id)
            return JSONResponse(status_code=400, content={"detail": f"Failed to start stream: {e}"})
        finally:
            db.close()

    @app.get("/streams/list", response_model=list[StreamOut], tags=["streams"])
    def list_streams_api():
        db = get_db()
        try:
            rows = db_crud.list_streams(db)
            return [StreamOut.model_validate(r) for r in rows]
        finally:
            db.close()

    @app.get("/streams/info/{stream_id}", response_model=StreamDetailOut, tags=["streams"])
    def get_stream_api(stream_id: str):
        db = get_db()
        try:
            row: Stream = db_crud.get_stream(db, stream_id)
            if not row:
                raise HTTPException(status_code=404, detail="Stream Not Found")
            return StreamDetailOut.model_validate(row)
        finally:
            db.close()

    @app.delete("/streams/delete/{stream_id}", tags=["streams"])
    def delete_stream_api(stream_id: str):
        db = get_db()
        try:
            row: Stream = db_crud.get_stream(db, stream_id)
            if not row:
                return JSONResponse(status_code=200, content={"message": "Already deleted or not found"})
            # stop if running
            if STREAM_MANAGER.is_alive(stream_id):
                STREAM_MANAGER.stop(db, stream_id, force=True)
            # stop camera from mtex
            ok, err = MTX_CLIENT.delete_camera(name=stream_id)
            if not ok:
                logger.error(f"Failed to delete camera {stream_id} from mtex: {err}")
            ok = db_crud.delete_stream_row(db, stream_id)
            logger.info(f"[delete_stream] stream {stream_id} has been deleted from MediaMTX and DB. Returning...")
            return {"deleted": ok}
        finally:
            db.close()

    @app.post("/streams/stop/{stream_id}", tags=["streams"])
    def stop_stream_api(stream_id: str):
        db = get_db()
        try:
            row: Stream = db_crud.get_stream(db, stream_id)
            if not row:
                return JSONResponse(status_code=200, content={"message": "Already stopped or not found"})
            if STREAM_MANAGER.is_alive(stream_id):
                STREAM_MANAGER.stop(db, stream_id, force=True)
            else:
                # make sure DB reflects stopped
                db_crud.set_stream_stopped(db, stream_id)
            # stop camera from mtex
            ok, err = MTX_CLIENT.delete_camera(name=stream_id)
            if not ok:
                logger.error(f"Failed to delete camera {stream_id} from mtex: {err}")
            return {"stopped": True}
        finally:
            db.close()

    @app.post("/streams/start/{stream_id}", tags=["streams"])
    def start_stream_api(stream_id: str):
        db = get_db()
        try:
            row: Stream = db_crud.get_stream(db, stream_id)
            if not row:
                return JSONResponse(status_code=404, content={"message": "Stream Not Found"})
            if STREAM_MANAGER.is_alive(stream_id):
                return JSONResponse(status_code=200, content={"message": "Already running"})
            try:
                # add camera to mtex
                ok, webrtc_url, err = MTX_CLIENT.add_camera(name=row.stream_id, source=row.source_url)
                if not ok:
                    raise HTTPException(status_code=400, detail=f"Failed to add camera to mtex: {err or 'unknown'}")
                row.webrtc_url = webrtc_url
                db.commit()
                logger.info(f"[start_stream] stored webrtc url for stream {stream_id}")
                # start engine
                STREAM_MANAGER.start(db, row.stream_id)
            except Exception as e:
                db_crud.set_stream_error(db, stream_id, str(e))
                raise HTTPException(status_code=400, detail=f"Failed to start stream: {e}")
            return {"started": True}
        finally:
            db.close()

    @app.post("/streams/error/{stream_id}", tags=["streams"])
    def set_stream_error_api(stream_id: str):
        db = get_db()
        try:
            row = db_crud.get_stream(db, stream_id)
            if not row:
                return JSONResponse(status_code=200, content={"message": "Already stopped or not found"})
            if STREAM_MANAGER.is_alive(stream_id):
                STREAM_MANAGER.stop(db, stream_id, force=True)
            db_crud.set_stream_error(db, stream_id, "error")
            # delete camera from mtex
            ok, err = MTX_CLIENT.delete_camera(name=stream_id)
            if not ok:
                logger.error(f"Failed to delete camera {stream_id} from mtex: {err}")
            return {"stopped": True}
        except Exception as e:
            logger.error(f"Failed to stop stream {stream_id}: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to stop stream: {e}")
        finally:
            db.close()

    # Internal shutdown endpoint
    @app.post("/internal/shutdown/{internal_key}", tags=["internal"])
    async def shutdown(internal_key: str):
        if internal_key != config.INTERNAL_SHUTDOWN_KEY:
            raise HTTPException(status_code=403, detail="Forbidden")
        logger.warning("[worker] Shutdown requested via SIGINT")
        # stop and delete all streams
        db = get_db()
        try:
            rows = db_crud.list_streams(db)
            for row in rows:
                # stop if running
                if STREAM_MANAGER.is_alive(row.stream_id):
                    STREAM_MANAGER.stop(db, row.stream_id, force=True)
                # stop camera from mtex
                ok, err = MTX_CLIENT.delete_camera(name=row.stream_id)
                if not ok:
                    logger.error(f"Failed to delete camera {row.stream_id} from mtex: {err}")
                ok = db_crud.delete_stream_row(db, row.stream_id)
        finally:
            db.close()        
        # Portable shutdown trigger instead of sys.exit()
        if os.name == "nt": os._exit(1)  # Windows hard-exit
        else: os.kill(os.getpid(), signal.SIGINT)  # Linux / Docker
        _stop_process_tree()
        return {"shutting_down": True}
    return app


def _stop_process_tree():
    try:
        # If running under Gunicorn, the master is our parent.
        master_pid = os.getppid()
        # Prefer graceful Gunicorn shutdown:
        os.kill(master_pid, signal.SIGTERM)   # or SIGQUIT for graceful
    except Exception:
        # Fallback: kill ourselves (pid 1 if uvicorn is pid1)
        os.kill(1 if os.getpid() != 1 else os.getpid(), signal.SIGTERM)
    # If we're still alive after a moment, hard-exit
    time.sleep(1.5)
    os._exit(0)


if __name__ == "__main__":
    app = create_app()
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        # already set by parent
        pass

    uvicorn.run(
        app,
        host=config.HOST,
        port=config.HOST_PORT,
        reload=False,
        log_level=config.LOGS_LEVEL.lower(),
        workers=1,
    )
