import queue
import time
from datetime import datetime
import traceback
import os
import cv2
import base64
import numpy as np
import threading
import logging
import gc
from typing import Union
from functools import partial
from vidgear.gears import CamGear

from .validations import validate_engine_args, warn_config_consistency
from .args import EngineArgs
from ..utils import delete_stream_call, check_url_reachable
from .yunet import YuNet, YuNetONNX, ObjectTracker
from config import config as cfg

from .executors import RealTimeThreadPoolExecutor
from .tracker import FaceTracker
from app.services import BroadcastClient, RetryPolicy


class Engine:
    """
    Refactored Engine with structured models & tracker.
    External behavior preserved:
      - same POST payload structure (with 'multipart:*' placeholders)
      - same logging
      - same threading and FPS cadence
    """

    # def __init__(self, args: dict, stream_id: str, stop_event, logger: logging.Logger):
    def __init__(self, args: Union[EngineArgs, dict], stream_id: str, stop_event, logger: logging.Logger):
        # Accept dicts for backward-compat
        self.args = args if isinstance(args, EngineArgs) else EngineArgs.model_validate(args)

        self.stream_id = stream_id
        self.stop_event = stop_event
        self.logger = logger

        # Pull fields (single source of truth)
        self.stream_url = self.args.stream_url
        self.broadcast_url = self.args.broadcast_url
        self.results_interval = self.args.results_interval
        self.max_cropped_face_resolution = self.args.max_cropped_face_resolution
        self.streaming_fps = self.args.streaming_fps
        self.camera_reconnect_interval = self.args.camera_reconnect_interval
        self.camera_reconnect_attempts = self.args.camera_reconnect_attempts
        self.face_detection_confidence = self.args.face_detection_confidence
        self.max_allowed_detections = self.args.max_allowed_detections
        self.frame_to_return_flag = self.args.frame_to_return
        self.max_frame_resolution = self.args.max_frame_resolution
        self.face_detector_resolution = self.args.face_detector_resolution
        self.date_format = self.args.date_format
        self.cropped_face_margin = self.args.cropped_face_margin

        self.frame_width = None
        self.frame_height = None
        self.target_fps = None
        self.do_resize = False
        self.ready = True
        self.frames_buffer = None
        self.backgroundTaskExecuter = None
        self.cameragear_stream = None
        self.YuNetModel = None

        # typed FaceTracker state (replaces dict)
        self.tracker = FaceTracker(self.date_format)

        # get public key from PUBLIC_KEY_PEM_PATH
        self.server_public_key_pem = None
        if os.path.exists(cfg.PUBLIC_KEY_PEM_PATH):
            with open(cfg.PUBLIC_KEY_PEM_PATH, "r") as f:
                self.server_public_key_pem = f.read()
        
        encrypt = True
        if cfg.ENCRYPT_RESULTS is False or self.server_public_key_pem is None:
            self.logger.warning("Public key not found or encryption is disabled. Defaulting to MultiPart Broadcasting.")
            encrypt = False
        else:
            self.logger.info("Public key found. Encryption is enabled.")

        # broadcast client
        self.broadcast_client = BroadcastClient(
            retry_policy=RetryPolicy(max_attempts=3, base_backoff=0.25, max_backoff=2.0),
            encrypt=encrypt,
            server_public_key_pem=self.server_public_key_pem,
            watchtower_token=cfg.WATCHTOWER_TOKEN,
            logger=self.logger
        )

    # ------------------ lifecycle ------------------
    def run(self):
        self.initializeCamera()
        if self.ready:
            self.stream_thread = threading.Thread(target=self.getFrames, daemon=True)
            self.process_thread = threading.Thread(target=self.startStreaming, daemon=True)
            self.stream_thread.start()
            self.process_thread.start()
            self.stream_thread.join()
            self.process_thread.join()
            self.logger.info(f"[{self.stream_id}] Everything stopped successfully. Ready to kill the process.")
        else:
            raise RuntimeError("Camera is not ready")

    def initializeCamera(self):
        try:
            # normalize incoming args the Engine was constructed with
            normalized = validate_engine_args({
                "stream_url": self.stream_url,
                "broadcast_url": self.broadcast_url,
                "results_interval": self.results_interval,
                "streaming_fps": self.streaming_fps,
                "camera_reconnect_interval": self.camera_reconnect_interval,
                "camera_reconnect_attempts": self.camera_reconnect_attempts,
                "face_detection_confidence": self.face_detection_confidence,
                "max_allowed_detections": self.max_allowed_detections,
                "frame_to_return": self.frame_to_return_flag,
                "max_frame_resolution": self.max_frame_resolution,
                "face_detector_resolution": self.face_detector_resolution,
                "cropped_face_margin": self.cropped_face_margin,
                "date_format": self.date_format,
            }, self.logger)

            # assign normalized values
            self.stream_url                  = normalized["stream_url"]
            self.broadcast_url               = normalized["broadcast_url"]
            self.results_interval            = normalized["results_interval"]
            self.streaming_fps               = normalized["streaming_fps"]
            self.camera_reconnect_interval   = normalized["camera_reconnect_interval"]
            self.camera_reconnect_attempts   = normalized["camera_reconnect_attempts"]
            self.face_detection_confidence   = normalized["face_detection_confidence"]
            self.max_allowed_detections      = normalized["max_allowed_detections"]
            self.frame_to_return_flag        = normalized["frame_to_return"]
            self.max_frame_resolution        = normalized["max_frame_resolution"]
            self.face_detector_resolution    = normalized["face_detector_resolution"]
            self.cropped_face_margin         = normalized["cropped_face_margin"]
            self.date_format                 = normalized["date_format"]

            # check broadcast_url
            self.broadcast_results = True
            if not self.broadcast_url or not check_url_reachable(self.broadcast_url):
                self.logger.warning(f"[{self.stream_id}] broadcast_url is not reachable or not provided")
                self.broadcast_results = False

            # optional: cross-check config
            warn_config_consistency(self.logger)

            self.frames_buffer = queue.Queue(maxsize=2)
            self.backgroundTaskExecuter = RealTimeThreadPoolExecutor(max_workers=2)

            if cfg.YUNET_CHOICE not in ["cv", "onnx"]:
                self.logger.warning("Invalid YUNET_CHOICE. Defaulting to 'onnx'")
                cfg.YUNET_CHOICE = "onnx"

            if cfg.YUNET_CHOICE == "cv":
                self.YuNetModel = YuNet(
                    modelPath=cfg.YUNET_MODEL_PATH,
                    inputSize=self.face_detector_resolution,
                    confThreshold=self.face_detection_confidence,
                    nmsThreshold=cfg.YUNET_NMS_THRESHOLD,
                    topK=self.max_allowed_detections,
                    backendTraget=cfg.YUNET_BACKEND_TARGET,
                    track=True,
                    track_patience=cfg.DEEPSORT_N_INIT,
                    track_max_age=cfg.DEEPSORT_MAX_AGE,
                )
            else:
                self.YuNetModel = YuNetONNX(
                    modelPath=cfg.YUNET_MODEL_PATH,
                    inputSize=self.face_detector_resolution,
                    confThreshold=self.face_detection_confidence,
                    topK=self.max_allowed_detections,
                    logger=self.logger,
                    track=True,
                    track_patience=cfg.DEEPSORT_N_INIT,
                    track_max_age=cfg.DEEPSORT_MAX_AGE,
                    verbose=1,
                )

            self.initializeCamGear__()
        except Exception as e:
            self.logger.error(f"[ERROR] Initializing stream for {self.stream_id} failed: {e}\n {traceback.format_exc()}")
            self.ready = False

    def initializeCamGear__(self):
        # tear down any previous
        try:
            if self.cameragear_stream is not None:
                self.cameragear_stream.stop()
                time.sleep(1)
                self.cameragear_stream = None
                gc.collect()
        except Exception:
            pass

        try:
            self.cameragear_stream = CamGear(
                source=self.stream_url,
                logging=True,
                time_delay=0,
                **{"THREADED_QUEUE_MODE": True},
            ).start()
            time.sleep(0.2)

            if self.frame_width is None:  # only first time
                self.frame_width = self.cameragear_stream.stream.get(cv2.CAP_PROP_FRAME_WIDTH)
                self.frame_height = self.cameragear_stream.stream.get(cv2.CAP_PROP_FRAME_HEIGHT)
                self.target_fps = self.streaming_fps or self.cameragear_stream.stream.get(cv2.CAP_PROP_FPS) or 30
                self.logger.info(f"[{self.stream_id}] Stream Info: ({self.frame_width}x{self.frame_height}) {self.target_fps}FPS")
                try:
                    self.target_fps = int(self.target_fps)
                except Exception:
                    pass

                if self.max_frame_resolution is not None and self.frame_width and (self.frame_width > self.max_frame_resolution):
                    self.frame_height = int(self.frame_height * self.max_frame_resolution / float(self.frame_width))
                    self.frame_width = self.max_frame_resolution
                    self.do_resize = True

                if getattr(cfg, "YUNET_CHOICE", "onnx") == "cv":
                    YW = self.face_detector_resolution
                    YH = int(self.frame_height * self.face_detector_resolution / float(self.frame_width))
                    self.YuNetModel.setInputSize([YW, YH])

        except Exception as e:
            self.logger.error(f"[ERROR] Initializing stream for {self.stream_id} failed: {e}\n {traceback.format_exc()}")
            raise RuntimeError(f"Initializing stream for {self.stream_id} failed") from e
        finally:
            gc.collect()

    # ------------------ frame I/O ------------------
    def getFrames(self):
        self.logger.info(f"[{self.stream_id}] Retrieving frames started...")
        st = None
        attempt = 0
        while self.ready and not self.stop_event.is_set():
            frame = None
            if self.cameragear_stream is not None:
                frame = self.cameragear_stream.read()

            if frame is not None:
                try:
                    self.frames_buffer.put(frame, block=False)
                except queue.Full:
                    pass
                except Exception:
                    pass
            else:
                if st is None:
                    st = time.time()
                    self.logger.info(f"[{self.stream_id}] Retrying to connect in {self.camera_reconnect_interval} seconds...")
                if (time.time() - st) >= self.camera_reconnect_interval:
                    try:
                        self.initializeCamGear__()
                    except Exception:
                        self.logger.error(f"[{self.stream_id}] Initializing stream failed. Retrying...")
                    st = None
                    attempt += 1
                    if attempt >= self.camera_reconnect_attempts:
                        self.logger.info(f"[{self.stream_id}] Max attempts reached. Stopping the stream.")
                        self.graceful_shutdown()
                time.sleep(0.5)
        self.logger.info(f"[{self.stream_id}] Retrieving frames stopped...")

    # ------------------ processing ------------------
    def startStreaming(self):
        try:
            request_st_time = time.time()
            request_st_count = 0
            fps_log_interval = 10

            while self.ready and not self.stop_event.is_set():
                try:
                    frame = self.frames_buffer.get(timeout=0.5)
                except queue.Empty:
                    continue
                except Exception:
                    continue

                # Inference. YuNet returns: {face_id: {"bbox": [x1,y1,x2,y2], "confidence": float}, ...}
                active = self.YuNetModel(frame)

                # Update engine-side typed tracks (first/last/best, history, avg, frame snapshot, timestamps)
                now_str = datetime.now().strftime(self.date_format)
                self.tracker.update_from_active(
                    active_dict=active,
                    frame=frame,
                    frame_flag=self.frame_to_return_flag,  # "first" | "best" | "last"
                    now_str=now_str,
                    max_cropped_face_resolution=self.max_cropped_face_resolution,
                    cropped_face_margin=self.cropped_face_margin,
                )

                # Get explicit end-of-track IDs directly from the model tracker (if available)
                model_ended_ids = set()
                model_tracker: ObjectTracker = getattr(self.YuNetModel, "tracker", None)
                if model_tracker and hasattr(model_tracker, "pop_ended_ids"):
                    model_ended_ids = {str(tid) for tid in model_tracker.pop_ended_ids()}

                # Prepare the active-id set for the current frame (fallback for inferred ends)
                active_ids: set[str] = {str(fid) for fid in active.keys()}

                # Emit any records that are due by interval OR explicitly ended by the model
                for (face_id, track, end_of_track) in self.tracker.iter_due_or_ended(
                    active_ids_set=active_ids,
                    results_interval_ms=float(self.results_interval),  # per-ID interval
                    model_ended_ids=model_ended_ids,
                    now_str=now_str,
                ):
                    # self.logger.info(f"[{self.stream_id}] Emitting record: face_id: {face_id}, end_of_track: {end_of_track}, did_frame_update: {track.did_frame_update}")
                    # # test payload
                    # received = self.broadcast_client.send_detection(
                    #     url=self.broadcast_url,
                    #     record=track.to_record(
                    #         stream_id=str(self.stream_id),
                    #         end_of_track=end_of_track,
                    #         frame_flag=self.frame_to_return_flag,
                    #         date_format=self.date_format,
                    #     ),
                    #     frame=track.frame,
                    #     face_crop=track.face_crop,
                    #     frame_to_return_flag=self.frame_to_return_flag,
                    #     frame_width=self.frame_width,
                    #     frame_height=self.frame_height,
                    #     fmt=".jpg",
                    # )
                    # b64 = received["normalized"]
                    # self.logger.info(f"\n\n{b64}\n\n")
                    # img = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
                    # cv2.imshow(face_id, img)
                    # cv2.waitKey(1)
                    # if end_of_track: cv2.destroyWindow(face_id)

                    if self.broadcast_results:
                        # Build the record (typed -> wire format)
                        record = track.to_record(
                            stream_id=str(self.stream_id),
                            end_of_track=end_of_track,
                            frame_flag=self.frame_to_return_flag,
                            date_format=self.date_format,
                        )
                        # Broadcast (sync client via ThreadPoolExecutor)
                        try:
                            # enqueue post (sync client)
                            self.backgroundTaskExecuter.submit(
                                lambda: self.broadcast_client.send_detection(
                                    url=self.broadcast_url,
                                    record=record,
                                    frame=track.frame,
                                    face_crop=track.face_crop,
                                    frame_to_return_flag=self.frame_to_return_flag,
                                    frame_width=self.frame_width,
                                    frame_height=self.frame_height,
                                    fmt=".jpg",
                                )
                            )
                        except Exception:
                            self.logger.error(f"[ERROR] posting event failed: {traceback.format_exc()}")

                    if end_of_track:
                        # final emit for this ID; discard the track
                        self.tracker.remove(face_id)
                    else:
                        # remember we just emitted; future emits will wait for interval
                        track.mark_emitted(now_str)
                        # (optional) track.reset_window(now_str) if we want rolling-window stats
                        
                # FPS logging
                request_st_count += 1
                if time.time() - request_st_time >= fps_log_interval:
                    fps = round(request_st_count / fps_log_interval, 2)
                    request_st_count = 0
                    request_st_time = time.time()
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    self.logger.info(f"[{self.stream_id} Running] FPS/TIME: {fps}, {ts}")

            self.logger.info(f"[{self.stream_id}] Processing frames stopped...")

        except Exception as e:
            self.logger.error(f"[ERROR] Processing camera {self.stream_id} failed: {e}\n {traceback.format_exc()}")
        finally:
            self.graceful_shutdown()

    # ------------------ shutdown ------------------
    def graceful_shutdown(self):
        try: self.broadcast_client.close()
        except Exception: pass
        try:
            self.stop_event.set()
            self.ready = False
            if self.backgroundTaskExecuter:
                self.backgroundTaskExecuter.shutdown(wait=True)
            if self.cameragear_stream is not None:
                try:
                    self.cameragear_stream.stop()
                except Exception:
                    pass
            self.logger.info(f"Deleting stream call for stream_id: {self.stream_id}")
            delete_stream_call(self.stream_id)
        except Exception:
            self.logger.error(f"[{self.stream_id}] graceful_shutdown encountered an error:\n{traceback.format_exc()}")

    def setStopEvent(self, stop_event):
        self.stop_event = stop_event
