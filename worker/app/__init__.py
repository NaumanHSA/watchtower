import cv2
from config import config

# Global OpenCV perf knobs
cv2.setNumThreads(config.OPENCV_THREADS)
cv2.setUseOptimized(config.OPENCV_OPTIMIZATION)