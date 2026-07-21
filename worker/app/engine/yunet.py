# This file is part of OpenCV Zoo project.
# It is subject to the license terms in the LICENSE file found in the same directory.
#
# Copyright (C) 2021, Shenzhen Institute of Artificial Intelligence and Robotics for Society, all rights reserved.
# Third party copyrights are property of their respective owners.

import os
import numpy as np
import logging
from collections import defaultdict
import cv2

# Check OpenCV version
opencv_python_version = lambda str_version: tuple(map(int, (str_version.split("."))))
assert opencv_python_version(cv2.__version__) >= opencv_python_version("4.10.0"), "Please install latest opencv-python for benchmark: python3 -m pip install --upgrade opencv-python"


class YuNet:
    def __init__(
        self,
        modelPath,
        inputSize=320,
        inputSizeRatio=4/3,
        confThreshold=0.6, 
        nmsThreshold=0.3, 
        topK=1000, 
        backendTraget=0,
        track=False,
        track_patience=5,
        track_max_age=5
    ):
        self._modelPath = str(modelPath)
        self.inputSizeRatio = inputSizeRatio
        w = inputSize
        h = int(w / self.inputSizeRatio)
        self._inputSize = tuple([w, h]) # [w, h]
        self._confThreshold = float(confThreshold)
        self._nmsThreshold = float(nmsThreshold)
        self._topK = int(topK)
        # Valid combinations of backends and targets
        self.backend_target_pairs = [
            [cv2.dnn.DNN_BACKEND_OPENCV,   cv2.dnn.DNN_TARGET_CPU],
            [cv2.dnn.DNN_BACKEND_CUDA,   cv2.dnn.DNN_TARGET_CUDA],
            [cv2.dnn.DNN_BACKEND_CUDA,   cv2.dnn.DNN_TARGET_CUDA_FP16],
            [cv2.dnn.DNN_BACKEND_TIMVX,  cv2.dnn.DNN_TARGET_NPU],
            [cv2.dnn.DNN_BACKEND_CANN,   cv2.dnn.DNN_TARGET_NPU]
        ]
        self._backendId = self.backend_target_pairs[backendTraget][0]
        self._targetId = self.backend_target_pairs[backendTraget][1]
        self._model = cv2.FaceDetectorYN.create(
            model=self._modelPath,
            config="",
            input_size=self._inputSize,
            score_threshold=self._confThreshold,
            nms_threshold=self._nmsThreshold,
            top_k=self._topK,
            backend_id=self._backendId,
            target_id=self._targetId
        )
        self.tracker = self.tracker = ObjectTracker(
            patience=track_patience, 
            max_age=track_max_age
        ) if track else None

    @property
    def name(self):
        return self.__class__.__name__

    def setInputSize(self, input_size):
        self._inputSize = input_size
        self._model.setInputSize(self._inputSize)

    def __call__(self, image: np.ndarray):
        YW, YH = self._inputSize
        det = self._model.detect(cv2.resize(image, (YW, YH), interpolation=cv2.INTER_AREA))[1]
        det = np.empty((0, 5)) if det is None else det
        h, w = image.shape[:2]
        sx, sy = (w / YW), (h / YH)

        scaled = []
        for d in det:
            x1, y1, x2, y2 = [int(v) for v in d[0:4]]
            x1 = int(x1 * sx); y1 = int(y1 * sy)
            x2 = int(x2 * sx); y2 = int(y2 * sy)
            conf = float(d[-1])
            scaled.append([x1, y1, x2, y2, conf])

        if self.tracker is None:
            # return list if you prefer, but your engine uses dict(face_id -> data)
            return {i+1: {"bbox": s[:4], "confidence": s[4]} for i, s in enumerate(scaled)}

        tracks = self.tracker.update(scaled, xyxy=True)
        # adapt to dict format your engine uses:
        return {tid: {"bbox": t["bbox"], "confidence": t["confidence"]} for tid, t in tracks.items()}


class YuNetONNX():
    def __init__(
        self, 
        modelPath: str,
        postProcessModelPath: str=None,
        inputSize=320,
        inputSizeRatio=4/3,
        confThreshold=0.7,
        topK=1000, 
        logger=logging.getLogger(),
        track=False,
        track_patience=5,
        track_max_age=10,
        providers=["CPUExecutionProvider"],
        verbose=1
    ):
        import onnxruntime as ort
        self.logger = logger
        self.W = inputSize
        self.H = int(self.W / inputSizeRatio)
        self.conf_threshold = confThreshold
        self.topK = topK
        
        if postProcessModelPath is None:
            basepath, model_name = os.path.split(modelPath)
            postProcessModelPath = os.path.join(basepath, f"postproc_yunet_top20_th70_{self.W}x{self.H}.onnx")
            if not os.path.exists(modelPath) or not os.path.exists(postProcessModelPath):
                raise FileNotFoundError(f"Detector model OR postprocessing model not found. Please check the paths. {modelPath}, {postProcessModelPath}")
        try:
            opts = ort.SessionOptions()
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
            opts.intra_op_num_threads = 1
            # opts.inter_op_num_threads = 1
            self.providers = providers
            self.ort_detector = ort.InferenceSession(modelPath, opts, providers=self.providers)
            self.detector_input_name = self.ort_detector.get_inputs()[0].name
            self.ort_postprocessor = ort.InferenceSession(postProcessModelPath, opts, providers=self.providers)
            self.postprocessor_input_names = [input.name for input in self.ort_postprocessor.get_inputs()]
        except Exception as e:
            raise Exception("Error while initializing onnx models...")
        
        self.verbose = verbose
        self.tracker = ObjectTracker(
            patience=track_patience, 
            max_age=track_max_age
        ) if track else None
        
    
    def preprocess(self, image: np.ndarray):
        """
        Preprocess the input image to match the model requirements.
        - Resize the image while maintaining aspect ratio.
        - Pad with black if necessary.
        - Normalize the image.
        """
        original_h, original_w = image.shape[:2]
        scale = min(self.H / original_h, self.W / original_w)
        resized_h, resized_w = int(original_h * scale), int(original_w * scale)
        resized_image = cv2.resize(image, (resized_w, resized_h))
        
        padded_image = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        # padded_image[self.H - resized_h:, self.W - resized_w:] = resized_image
        padded_image[:resized_h, :resized_w, :] = resized_image
        
        # Normalize
        # input_image = padded_image.astype(np.float32) / 255.0
        input_image = padded_image.astype(np.float32)
        input_image = input_image.transpose(2, 0, 1)  # HWC to CHW
        input_image = np.expand_dims(input_image, axis=0)  # Add batch dimension
        return input_image, ((self.W / resized_w), (self.H / resized_h))


    def __call__(self, image: np.ndarray, *args, **kwds):
        h, w = image.shape[:2]
        inp, (sW, sH) = self.preprocess(image)
        raw = self.ort_detector.run(None, {self.detector_input_name: inp})
        post = self.ort_postprocessor.run(None, {
            self.postprocessor_input_names[0]: raw[0],
            self.postprocessor_input_names[1]: raw[1],
            self.postprocessor_input_names[2]: raw[2],
        })[0]

        scaled = []
        for d in post:
            conf = float(d[-1])
            if conf < self.conf_threshold:
                continue
            x1, y1, x2, y2 = d[0:4]
            # map from padded/resize space back to original
            X1 = int(max(0, min(w, x1 * sW * w)))
            Y1 = int(max(0, min(h, y1 * sH * h)))
            X2 = int(max(0, min(w, x2 * sW * w)))
            Y2 = int(max(0, min(h, y2 * sH * h)))
            if X2 <= X1 or Y2 <= Y1:
                continue
            scaled.append([X1, Y1, X2, Y2, conf])

        # topK on scaled
        scaled.sort(key=lambda x: x[-1], reverse=True)
        scaled = scaled[: self.topK]

        if self.tracker is None:
            return {i+1: {"bbox": s[:4], "confidence": s[4]} for i, s in enumerate(scaled)}

        tracks = self.tracker.update(scaled, xyxy=True)
        return {tid: {"bbox": t["bbox"], "confidence": t["confidence"]} for tid, t in tracks.items()}


    def setInputSize(self, input_size):
        pass
        # raise NotImplementedError()


class ObjectTracker:
    def __init__(
        self, 
        patience=3, 
        max_age=10, 
        iou_threshold=0.3,
        *,
        max_id: int = 9999,          # threshold before reseeding
        reseed_on_idle: bool = True,
    ):
        """
        A minimal, IOU-based tracker with explicit end-of-track events.
        - patience: frames a new detection must persist before confirming a new track
        - max_age: frames allowed without an associated detection before the track ends
        - iou_threshold: minimum IoU for association
        """
        self.patience = int(max(1, patience))
        self.max_age = int(max(1, max_age))
        self.iou_threshold = float(iou_threshold)

        self.tracks = {}            # id -> {bbox(x1,y1,x2,y2), confidence, age, time_since_update, hits, confirmed}
        self._next_id = 1
        self._pending = {}          # det_idx -> count of consecutive frames seen (for "patience")
        self._ended_ids = set()     # ids that ended on the last update()

        self.max_id = int(max(1, max_id))
        self.reseed_on_idle = bool(reseed_on_idle)


    @staticmethod
    def _iou(box1, box2):
        x1, y1, x2, y2 = box1
        X1, Y1, X2, Y2 = box2
        inter_x1 = max(x1, X1); inter_y1 = max(y1, Y1)
        inter_x2 = min(x2, X2); inter_y2 = min(y2, Y2)
        inter_w = max(0, inter_x2 - inter_x1)
        inter_h = max(0, inter_y2 - inter_y1)
        inter = inter_w * inter_h
        a1 = max(0, x2 - x1) * max(0, y2 - y1)
        a2 = max(0, X2 - X1) * max(0, Y2 - Y1)
        union = a1 + a2 - inter
        return 0.0 if union <= 0 else inter / union

    def _to_xyxy(self, det, xyxy):
        # det is [x1,y1,x2,y2,conf] if xyxy else [x,y,w,h,conf]
        if xyxy:
            x1, y1, x2, y2, conf = det
            return [int(x1), int(y1), int(x2), int(y2), float(conf)]
        x, y, w, h, conf = det
        return [int(x), int(y), int(x + w), int(y + h), float(conf)]

    def pop_ended_ids(self):
        """Return and clear the set of track IDs that ended in the last update()."""
        ended = self._ended_ids
        self._ended_ids = set()
        return ended

    def update(self, detections_raw, xyxy=False):
        """
        Update the tracker with new detections (list of [x1,y1,x2,y2,conf] or [x,y,w,h,conf]).
        Returns a dict: id -> track_state (bbox, confidence, age, time_since_update, hits, confirmed).
        Also populates self._ended_ids with track IDs that ended on this frame.
        """
        # Normalize detections to xyxy
        detections = []
        for d in detections_raw:
            if len(d) < 5:  # guard
                continue
            x1, y1, x2, y2, conf = self._to_xyxy(d, xyxy)
            # enforce bbox ordering
            if x2 < x1: x1, x2 = x2, x1
            if y2 < y1: y1, y2 = y2, y1
            detections.append([x1, y1, x2, y2, conf])

        # Prepare bookkeeping
        self._ended_ids = set()  # reset for this call
        unmatched_tracks = set(self.tracks.keys())
        unmatched_dets = set(range(len(detections)))
        matches = []

        # Greedy matching by IoU
        for tid, t in self.tracks.items():
            best = (-1, 0.0)  # (det_idx, iou)
            for di in list(unmatched_dets):
                iou = self._iou(t["bbox"], detections[di][:4])
                if iou >= self.iou_threshold and iou > best[1]:
                    best = (di, iou)
            if best[0] != -1:
                matches.append((tid, best[0]))
                unmatched_tracks.discard(tid)
                unmatched_dets.discard(best[0])

        # Update matched tracks
        for tid, di in matches:
            x1, y1, x2, y2, conf = detections[di]
            tr = self.tracks[tid]
            tr["bbox"] = [x1, y1, x2, y2]
            tr["confidence"] = conf
            tr["age"] = 0
            tr["time_since_update"] = 0
            tr["hits"] += 1
            if not tr["confirmed"] and tr["hits"] >= self.patience:
                tr["confirmed"] = True
            # this detection is not pending anymore
            self._pending.pop(di, None)

        # Handle unmatched detections: accumulate patience before creating a track
        for di in unmatched_dets:
            self._pending[di] = self._pending.get(di, 0) + 1
            if self._pending[di] >= self.patience:
                x1, y1, x2, y2, conf = detections[di]
                self.tracks[self._next_id] = {
                    "bbox": [x1, y1, x2, y2],
                    "confidence": conf,
                    "age": 0,
                    "time_since_update": 0,
                    "hits": 1,
                    "confirmed": False,  # becomes True once hits >= patience
                }
                self._next_id += 1
                # remove this det from pending; it became a track
                self._pending.pop(di, None)

        # Unmatched tracks: age and possibly end
        ended = []
        for tid in list(unmatched_tracks):
            tr = self.tracks.get(tid)
            if tr is None:
                continue
            tr["age"] += 1
            tr["time_since_update"] += 1
            if tr["age"] > self.max_age:
                ended.append(tid)

        # Remove ended tracks and record them for this frame
        for tid in ended:
            if tid in self.tracks:
                del self.tracks[tid]
            self._ended_ids.add(tid)

        self._maybe_reseed_ids()
        return self.tracks

    # ---------------- NEW: reseed logic ----------------
    def _maybe_reseed_ids(self):
        """
        If ID space got large and the tracker is idle (no active tracks and no pending),
        reset ID counter safely to 1.
        """
        if not self.reseed_on_idle: return
        if self._next_id <= self.max_id: return
        # must be idle to reseed safely (no collisions)
        if self.tracks or self._pending: return
        self._next_id = 1

    # Optional: if you want a manual hook too
    def force_reseed_if_idle(self) -> bool:
        """
        Force a reseed now if idle; return True if reseeded.
        """
        before = self._next_id
        self._maybe_reseed_ids()
        return self._next_id != before