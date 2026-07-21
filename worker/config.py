from __future__ import annotations
from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
import re
import os
import uuid
from typing import Optional


class Config(BaseSettings):
    # ---------------- App basics ----------------
    APP_NAME: str = "Video Worker"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    HOST_PORT: int = os.getenv("HOST_PORT", 8081)
    LOGS_LEVEL: str = "info"

    # ---------------- CORS ----------------
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")

    # ---------------- Auth ----------------
    WORKER_API_KEY: str = os.getenv("WORKER_API_KEY", "")
    WATCHTOWER_TOKEN: str = os.getenv("WATCHTOWER_TOKEN", "")

    # ---------------- Encryption ----------------
    PUBLIC_KEY_PEM_PATH: str = "./app/keys/public_key.pem"
    ENCRYPT_RESULTS: bool = os.getenv("ENCRYPT_RESULTS", False)
    
    # ---------------- NETWORK URLS ----------------
    HOST_WORKER_URL: Optional[str] = os.getenv("HOST_WORKER_URL", None)
    HOST_MTX_WEBRTC_URL: Optional[str] = os.getenv("HOST_MTX_WEBRTC_URL", None)
    CONTROLLER_URL: Optional[str] = os.getenv("CONTROLLER_URL", None)   # url to announce to the controller
    BROADCAST_URL: Optional[str] = os.getenv("BROADCAST_URL", None)   # url where to send results
    
    WORKER_ID: Optional[str] = uuid.uuid4().hex
    WORKER_HEALTH_URL: str = "/"
    
    # ------------------------ MediaMTX PARAMS ------------------------
    # feature switch
    MTX_ENABLE: bool = Field(default=True)
    MTX_URL_INTERNAL: str = Field(default="http://0.0.0.0:9997/")
    MTX_WEBRTC_INTERNAL: str = Field(default=''.join(os.getenv("MTX_URL_INTERNAL", "http://0.0.0.0:9997/").split(":")[:2]) + ":8889")

    # controller base + endpoints
    MTX_TIMEOUT_SEC: int = Field(default=90)
    MTX_RETRY_COUNT: int = Field(default=2)
    MTX_ADD_PATH: str = Field(default="v3/config/paths/add/")
    MTX_DELETE_PATH: str = Field(default="v3/config/paths/delete/")
    MTX_LIST_PATH: str = Field(default="v3/config/paths/list/")

    # auth (choose one flow)
    MTX_USERNAME: Optional[str] = None
    MTX_PASSWORD: Optional[str] = None
    MTX_BEARER_TOKEN: Optional[str] = None
    MTX_AUTH_URL: Optional[str] = None
    MTX_AUTH_FETCH: bool = False

    # if you store encrypted creds: you can read them here and decrypt in code
    MTX_ENC_USERNAME: Optional[str] = None
    MTX_ENC_PASSWORD: Optional[str] = None


    # ---------------- Background Jobs: Announce and Heartbeat ----------------
    ANNOUNCE_TIMEOUT_SECS: float = 5.0
    ANNOUNCE_RETRIES: int = 2
    ANNOUNCE_RETRY_INTERVAL: int = 2     # seconds
    HEARTBEAT_INTERVAL: int = 30     # seconds
    HEARTBEAT_RETRY_INTERVAL: int = 2     # seconds
    HEARTBEAT_RETRY_ATTEMPTS: int = 3
    MAX_ALLOWED_STREAMS: int = os.getenv("MAX_ALLOWED_STREAMS", 2)

    # ---------------- DB ----------------
    SQLITE_PATH: str = os.getenv("SQLITE_PATH", "worker.sqlite3")

    # ---------------- OpenCV / Engine defaults ----------------
    OPENCV_THREADS: int = os.getenv("OPENCV_THREADS", 1)
    OPENCV_OPTIMIZATION: bool = os.getenv("OPENCV_OPTIMIZATION", True)

    # Stream defaults pulled from env instead of payload
    RESULTS_INTERVAL: float = os.getenv("RESULTS_INTERVAL", 1.0)
    FACE_DETECTION_CONFIDENCE: float = os.getenv("FACE_DETECTION_CONFIDENCE", 0.9)
    FACE_DETECTOR_RESOLUTION: int = os.getenv("FACE_DETECTOR_RESOLUTION", 320)
    MAX_ALLOWED_DETECTIONS: int = os.getenv("MAX_ALLOWED_DETECTIONS", 100)
    MAX_FRAME_RESOLUTION: int = os.getenv("MAX_FRAME_RESOLUTION", 1280)
    MAX_CROPPED_FACE_RESOLUTION: int = os.getenv("MAX_CROPPED_FACE_RESOLUTION", 224)
    CROPPED_FACE_MARGIN: float = os.getenv("CROPPED_FACE_MARGIN", 0.1)
    FRAME_TO_RETURN: str = os.getenv("FRAME_TO_RETURN", "none")   # "none" | "full" | "cropped"
    STREAMING_FPS: int = os.getenv("STREAMING_FPS", 10)
    CAMERA_RECONNECT_INTERVAL: int = os.getenv("CAMERA_RECONNECT_INTERVAL", 10)
    CAMERA_RECONNECT_ATTEMPTS: int = os.getenv("CAMERA_RECONNECT_ATTEMPTS", 5)

    # ------------------------ YUNET PARAMS ------------------------
    """
    YUNET_BACKEND_TARGET:
        0: (default) OpenCV implementation + CPU,
        1: CUDA + GPU (CUDA),
        2: CUDA + GPU (CUDA FP16),
        3: TIM-VX + NPU,
        4: CANN + NPU
    """
    YUNET_CHOICE: str = 'onnx'  # ['onnx', 'cv']
    YUNET_MODEL_PATH: str = "app/weights/face_detection_yunet.onnx" if YUNET_CHOICE == "onnx" else "app/weights/face_detection_yunet_2023mar.onnx"
    YUNET_MODEL_INPUT_SIZE: int = 320     # [320, 416, 512, 640]
    YUNET_MODEL_INPUT_SIZE_RATIO: float = 4/3
    YUNET_BACKEND_TARGET: int = 0
    YUNET_CONF_THRESHOLD: float = 0.7
    YUNET_NMS_THRESHOLD: float = 0.4
    YUNET_TOP_K: int = 20

    # ------------------------ DEEPSORT PARAMS ------------------------
    DEEPSORT_MAX_AGE: int = 5
    DEEPSORT_N_INIT: int = 10
    DEEPSORT_MAX_IOU_DISTANCE: float = 0.7
    DEEPSORT_NMX_MAX_OVERLAP: float = 1.0
    DEEPSORT_MAX_COSINE_DISTANCE: float = 0.2
    DEEPSORT_EMBEDDER_GPU: bool = False

    # ------------------------ INTERNAL PARAMS ------------------------
    INTERNAL_SHUTDOWN_KEY: str = uuid.uuid4().hex
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def resolved_worker_id(self) -> str:
        import socket
        return self.WORKER_ID or socket.gethostname()

# create keys directory if it doesn't exist
os.makedirs("./app/keys", exist_ok=True)
config = Config()