from __future__ import annotations

import base64
import queue
import threading
import time

import cv2
import numpy as np
import pytest
from worker_helpers import FakeDetector, FakeSource, frame

from watchtower_contracts import DetectionEvent, ObservedStatus, Snapshot, StreamConfig
from watchtower_worker.engine.faces import FaceBook
from watchtower_worker.engine.process import EXIT_SOURCE_FAILED, EXIT_STOPPED, Engine, EngineSpec
from watchtower_worker.engine.source import FrameSource, SourceState
from watchtower_worker.engine.tracker import IoUTracker

FACE_A = [100, 100, 200, 220, 0.95]
FACE_B = [400, 120, 480, 230, 0.91]


def test_tracker_confirms_after_n_frames_and_drops_noise():
    tracker = IoUTracker(confirm_frames=3, max_missed=2)
    assert tracker.update([FACE_A]).visible == []
    assert tracker.update([FACE_A]).visible == []
    visible = tracker.update([FACE_A]).visible
    assert len(visible) == 1 and visible[0].confirmed
    # A one-frame blip never becomes a track.
    tracker.update([FACE_A, FACE_B])
    tracker.update([FACE_A])
    assert len(tracker.tracks) == 1


def test_tracker_ends_track_after_max_missed():
    tracker = IoUTracker(confirm_frames=1, max_missed=2)
    track_id = tracker.update([FACE_A]).visible[0].track_id
    assert tracker.update([]).ended == []
    assert tracker.update([]).ended == []
    assert tracker.update([]).ended == [track_id]
    assert tracker.tracks == {}


def test_tracker_matching_is_globally_greedy():
    tracker = IoUTracker(confirm_frames=1, max_missed=5)
    first = {t.track_id: t.bbox for t in tracker.update([FACE_A, FACE_B]).visible}
    moved_a = [110, 105, 210, 225, 0.9]
    moved_b = [405, 125, 485, 235, 0.9]
    second = {t.track_id: t.bbox for t in tracker.update([moved_b, moved_a]).visible}
    assert set(first) == set(second)  # same identities, despite the new order
    a_id = next(tid for tid, box in first.items() if box[0] == 100)
    assert second[a_id][:2] == [110, 105]


def test_facebook_emits_first_then_on_interval_then_end():
    tracker = IoUTracker(confirm_frames=1, max_missed=1)
    book = FaceBook(StreamConfig(results_interval_ms=200, snapshot=Snapshot.BEST))
    img = frame()
    update = tracker.update([FACE_A])
    first = book.update(update.visible, update.ended, img)
    assert len(first) == 1 and not first[0].end_of_track
    assert book.update(tracker.update([FACE_A]).visible, [], img) == []  # within the interval
    time.sleep(0.25)
    assert len(book.update(tracker.update([FACE_A]).visible, [], img)) == 1
    tracker.update([])
    final = tracker.update([])
    emissions = book.update(final.visible, final.ended, img)
    assert [e.end_of_track for e in emissions] == [True]
    assert book.faces == {}


def test_event_matches_contract_and_keeps_one_frame_per_face():
    tracker = IoUTracker(confirm_frames=1)
    book = FaceBook(StreamConfig(snapshot=Snapshot.BEST, face_crop_size=64))
    low = [100, 100, 200, 220, 0.80]
    high = [102, 101, 202, 221, 0.99]
    best_frame = frame()
    best_frame[0, 0] = (1, 2, 3)
    book.update(tracker.update([low]).visible, [], frame())
    emissions = book.update(tracker.update([high]).visible, [], best_frame)
    face = next(iter(book.faces.values()))
    assert face.best.frame is best_frame and face.first.frame is None and face.last.frame is None
    event = book.build_event(emissions[0] if emissions else _first(book), stream_id="s1", stream_name="Door",
                             worker_id="w1", generation=4)
    parsed = DetectionEvent.model_validate(event)
    assert parsed.camId == "s1" and parsed.generation == 4 and parsed.bbox.xyxy == [102, 101, 202, 221]
    assert parsed.timestamp.endswith("Z")
    crop = cv2.imdecode(np.frombuffer(base64.b64decode(parsed.normalized), np.uint8), cv2.IMREAD_COLOR)
    assert max(crop.shape[:2]) <= 64


def _first(book):
    from watchtower_worker.engine.faces import Emission

    return Emission(next(iter(book.faces.values())), end_of_track=False)


def test_event_without_full_frame():
    tracker = IoUTracker(confirm_frames=1)
    book = FaceBook(StreamConfig(include_frame=False))
    emission = book.update(tracker.update([FACE_A]).visible, [], frame())[0]
    assert book.build_event(emission, stream_id="s", stream_name="n", worker_id="w", generation=1)["photo"] is None


def _spec(**config) -> EngineSpec:
    return EngineSpec(stream_id="s1", stream_name="Door", generation=2, worker_id="w1", input_url="fake://",
                      config=StreamConfig(fps=None, track_confirm_frames=1, **config).model_dump(mode="json"))


def test_engine_reports_running_and_emits_results():
    stop, statuses, results = threading.Event(), queue.Queue(), queue.Queue()
    engine = Engine(_spec(), stop, statuses, results, detector=FakeDetector([[FACE_A]]), source=FakeSource())
    worker = threading.Thread(target=lambda: setattr(worker, "code", engine.run()))
    worker.start()
    first_result = results.get(timeout=5)
    stop.set()
    worker.join(5)
    assert worker.code == EXIT_STOPPED
    reported = [statuses.get_nowait()[3] for _ in range(statuses.qsize())]
    assert reported[0] == ObservedStatus.STARTING and ObservedStatus.RUNNING in reported
    assert first_result["stream_id"] == "s1" and first_result["generation"] == 2
    assert first_result["event"]["streamName"] == "Door"


def test_engine_exits_with_error_when_source_fails():
    stop, statuses, results = threading.Event(), queue.Queue(), queue.Queue()
    engine = Engine(_spec(), stop, statuses, results, detector=FakeDetector([[]]), source=FakeSource(fail_after=2))
    assert engine.run() == EXIT_SOURCE_FAILED
    last = None
    while not statuses.empty():
        last = statuses.get_nowait()
    assert last[3] == ObservedStatus.ERROR and last[5] == "camera gone"


def test_engine_reports_detector_load_failure():
    statuses = queue.Queue()

    class Boom:
        def __init__(self):
            raise RuntimeError("no model")

    engine = Engine(_spec(), threading.Event(), statuses, queue.Queue(), source=FakeSource())
    engine.spec = EngineSpec(**{**engine.spec.__dict__, "detector_backend": "onnx"})
    import watchtower_worker.engine.detector as detector_module

    original = detector_module.build_detector
    detector_module.build_detector = lambda *a, **k: Boom()
    try:
        assert engine.run() == 3
    finally:
        detector_module.build_detector = original
    messages = [statuses.get_nowait() for _ in range(statuses.qsize())]
    assert messages[-1][3] == ObservedStatus.ERROR and "no model" in messages[-1][5]


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 240))
    for i in range(20):
        writer.write(np.full((240, 320, 3), i * 10, dtype=np.uint8))
    writer.release()
    return path


def test_frame_source_reads_latest_frames(video_file):
    source = FrameSource(str(video_file), reconnect_interval=0.1, reconnect_attempts=100).start()
    try:
        img, seq = source.read(0, timeout=5)
        assert img is not None and img.shape == (240, 320, 3) and seq >= 1
        _, seq2 = source.read(seq, timeout=5)
        assert seq2 > seq
    finally:
        source.stop()


def test_frame_source_gives_up_on_missing_source(tmp_path):
    source = FrameSource(str(tmp_path / "missing.avi"), reconnect_interval=0.05, reconnect_attempts=2).start()
    deadline = time.monotonic() + 10
    while source.state != SourceState.FAILED and time.monotonic() < deadline:
        time.sleep(0.05)
    source.stop()
    assert source.state == SourceState.FAILED and source.error == "cannot open source"
