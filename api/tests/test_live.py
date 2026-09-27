import contextlib
import json
import logging
import sys
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import cv2
import httpx
import live_pose as live_pose_module
import main
import numpy as np
import pytest
from analysis.pipeline import analyze_dive  # Historical v1 feedback compatibility fixtures.
from config import Settings, get_settings
from diving_tracker import backend as backend_module
from diving_tracker.backend import BackendConfig, UltralyticsTopDownBackend
from fastapi.testclient import TestClient
from feedback import speech as speech_module
from feedback.gemini import CUES, LIVE_TIP_INSTRUCTION, GeminiCoach, LiveTip, rule_based_tip
from feedback.speech import SpeechClient, SpeechError
from live_pose import LivePoseDetector, LivePoseUnavailable, parse_pose_result

from tests.fakes import FakeCoach, FakeDatabase
from tests.synthetic import HEIGHT, WIDTH, DiveSpec, generate
from tests.test_feedback import TUCK_103C, FakeInteractions, coach_with, sloppy_analysis

PERSON = {"person": True, "keypoints": [[0.5, 0.4, 0.9]] * 17, "box": [0.4, 0.2, 0.6, 0.9], "inference_ms": 12.5}
NOBODY = {"person": False, "keypoints": None, "box": None, "inference_ms": 11.0}
JPEG = cv2.imencode(".jpg", np.zeros((48, 64, 3), np.uint8))[1].tobytes()
GOOD_TIP = "Nice height on that one. Try to squeeze the tuck tighter right after takeoff."


def clean_analysis() -> dict:
    frames, truth = generate(DiveSpec())
    return analyze_dive(frames, WIDTH, HEIGHT, TUCK_103C, {"water_y": truth["water_y"]}, height_cm=170)


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "gemini_api_key": "test-key",
        "database_url": "postgres://user:pw@localhost/test",
        "upload_dir": tmp_path,
        "elevenlabs_api_key": None,
        "elevenlabs_voice_id": None,
    }
    return Settings(_env_file=None, **{**values, **overrides})


def words(text: str) -> int:
    return len(text.split())


class FakeDetector:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.frames = result or PERSON, error, []

    def detect(self, image_bytes: bytes) -> dict:
        self.frames.append(image_bytes)
        if self.error:
            raise self.error
        return self.result


class FakeSpeech:
    def __init__(self, audio=b"ID3-fake-mp3", error=None):
        self.configured, self.audio, self.error, self.texts = True, audio, error, []

    def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        if self.error:
            raise self.error
        return self.audio


@pytest.fixture
def client(tmp_path):
    main.app.state.settings = make_settings(tmp_path)
    main.app.state.db = FakeDatabase()
    main.app.state.coach = FakeCoach()
    main.app.state.live_pose = FakeDetector()
    main.app.state.speech = None
    return TestClient(main.app)


def post_frame(client, data=JPEG, mime="image/jpeg"):
    return client.post("/live/pose", files={"frame": ("frame.jpg", data, mime)})


def insert_dive(analysis: dict) -> str:
    db = main.app.state.db
    dive_id = uuid4()
    db.insert_dive(
        {
            "id": dive_id,
            "diver_id": db.get_or_create_default_diver()["id"],
            "recorded_at": datetime.now(UTC),
            "setup": TUCK_103C,
            "analysis": analysis,
            "video_path": None,
            "video_mime": None,
        },
        [],
        [],
    )
    return str(dive_id)


# Pose


def test_pose_returns_the_detected_person(client):
    response = post_frame(client)
    assert response.status_code == 200, response.text
    assert response.json() == PERSON
    assert main.app.state.live_pose.frames == [JPEG]


def test_pose_without_a_person(client):
    main.app.state.live_pose = FakeDetector(result=NOBODY)
    body = post_frame(client, mime="image/webp").json()
    assert body["person"] is False and body["keypoints"] is None and body["box"] is None


def test_pose_rejects_non_images_and_large_frames(client):
    assert post_frame(client, data=b"hello", mime="text/plain").status_code == 415
    assert post_frame(client, data=b"\xff" * (1024 * 1024 + 1)).status_code == 413
    assert main.app.state.live_pose.frames == []


def test_undecodable_frame_is_415_without_loading_the_model(client, tmp_path):
    detector = LivePoseDetector(make_settings(tmp_path))
    main.app.state.live_pose = detector
    assert post_frame(client, data=b"not an image", mime="image/png").status_code == 415
    assert detector._model is None


def test_pose_is_503_when_the_yolo_stack_is_missing(client):
    main.app.state.live_pose = FakeDetector(error=LivePoseUnavailable("Live pose detection needs the YOLO stack."))
    response = post_frame(client)
    assert response.status_code == 503
    assert response.json()["detail"] == "Live pose detection needs the YOLO stack."


def test_detector_without_ultralytics_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    with pytest.raises(LivePoseUnavailable, match="ultralytics"):
        LivePoseDetector(make_settings(tmp_path)).detect(JPEG)


def test_a_broken_torch_install_is_unavailable_not_a_crash(monkeypatch, tmp_path):
    class MissingDll:
        def find_spec(self, name, path=None, target=None):
            if name == "torch":
                raise OSError("[WinError 126] Error loading fbgemm.dll")
            return None

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr(sys, "meta_path", [MissingDll(), *sys.meta_path])
    with pytest.raises(LivePoseUnavailable, match="YOLO stack"):
        LivePoseDetector(make_settings(tmp_path)).detect(JPEG)


class ScriptedModel:
    """Returns a person, or raises, following the given script (one entry per predict call)."""

    def __init__(self, *script):
        self.script = list(script)

    def predict(self, **_):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return [fake_result([0.9], [[[0.5, 0.5]] * 17], [[0.8] * 17], [[0.1, 0.1, 0.9, 0.9]])]


def test_a_model_that_fails_on_its_first_frame_stops_the_session_with_the_reason(client, tmp_path):
    detector = LivePoseDetector(make_settings(tmp_path))
    detector._model, detector._device = ScriptedModel(ValueError("Invalid CUDA 'device=cuda:0' requested.")), "cuda:0"
    main.app.state.live_pose = detector
    response = post_frame(client)
    assert response.status_code == 503
    assert "cuda:0" in response.json()["detail"] and "Invalid CUDA" in response.json()["detail"]


def test_a_failure_after_the_model_has_worked_is_not_reported_as_a_setup_problem(tmp_path):
    detector = LivePoseDetector(make_settings(tmp_path))
    detector._model, detector._device = ScriptedModel("ok", RuntimeError("temporary glitch")), "mps"
    assert detector.detect(JPEG)["person"] is True
    with pytest.raises(RuntimeError, match="temporary glitch"):
        detector.detect(JPEG)


def fake_result(conf, points, point_conf, boxes):
    return SimpleNamespace(
        boxes=SimpleNamespace(conf=np.array(conf), xyxyn=np.array(boxes)),
        keypoints=SimpleNamespace(xyn=np.array(points), conf=None if point_conf is None else np.array(point_conf)),
    )


def test_parse_pose_result_picks_the_most_confident_person():
    points = [[[0.1, 0.2]] * 17, [[0.123456, 0.654321]] * 17]
    boxes = [[0.0, 0.0, 0.5, 0.5], [0.25, 0.1, 0.7512345, 0.9]]
    parsed = parse_pose_result(fake_result([0.3, 0.8], points, [[0.5] * 17, [0.912345] * 17], boxes))
    assert parsed["person"] is True
    assert len(parsed["keypoints"]) == 17
    assert parsed["keypoints"][0] == [0.1235, 0.6543, 0.9123]
    assert parsed["box"] == [0.25, 0.1, 0.7512, 0.9]

    without_conf = parse_pose_result(fake_result([0.9], points[:1], None, boxes[:1]))
    assert {c for _, _, c in without_conf["keypoints"]} == {1.0}

    empty = fake_result(np.zeros(0), np.zeros((0, 17, 2)), np.zeros((0, 17)), np.zeros((0, 4)))
    assert parse_pose_result(empty) == {"person": False, "keypoints": None, "box": None}
    assert parse_pose_result(SimpleNamespace(boxes=None, keypoints=None))["person"] is False


def test_parse_pose_result_treats_zeroed_keypoints_as_missing():
    points = [[[0.4, 0.5]] * 15 + [[0.0, 0.0], [0.42, 0.61]]]
    parsed = parse_pose_result(fake_result([0.9], points, [[0.8] * 17], [[0.1, 0.1, 0.6, 0.9]]))
    assert parsed["keypoints"][15] == [0.0, 0.0, 0.0]
    assert parsed["keypoints"][16] == [0.42, 0.61, 0.8]


def test_detector_loads_once_and_predicts_people_only(monkeypatch, tmp_path):
    loaded, calls = [], []

    class FakeYOLO:
        def __init__(self, name):
            loaded.append(name)

        def predict(self, **kwargs):
            calls.append(kwargs)
            return [fake_result([0.9], [[[0.5, 0.5]] * 17], [[0.8] * 17], [[0.1, 0.1, 0.9, 0.9]])]

    no_gpu = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=no_gpu, backends=SimpleNamespace(mps=no_gpu)))
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))

    detector = LivePoseDetector(make_settings(tmp_path))
    first = detector.detect(JPEG)
    detector.detect(JPEG)
    assert loaded == ["yolo11n-pose.pt"]
    assert first["person"] is True and first["inference_ms"] >= 0
    assert {k: v for k, v in calls[0].items() if k != "source"} == {
        "imgsz": 480, "classes": [0], "device": "cpu", "verbose": False
    }
    assert calls[0]["source"].shape == (48, 64, 3)

    pinned = LivePoseDetector(make_settings(tmp_path, yolo_device="cuda:1", live_pose_size=320))
    pinned.detect(JPEG)
    assert calls[-1]["device"] == "cuda:1" and calls[-1]["imgsz"] == 320


class FakeGpu:
    """Stands in for the GPU: flags any moment two threads use it at once, which is what crashes MPS."""

    def __init__(self):
        self.active, self.uses, self.overlapped = 0, 0, False
        self._guard = threading.Lock()

    def use(self, seconds: float) -> None:
        with self._guard:
            self.active += 1
            self.uses += 1
            self.overlapped |= self.active > 1
        time.sleep(seconds)
        with self._guard:
            self.active -= 1


class GpuTensor:
    def __init__(self, gpu: FakeGpu, values):
        self.gpu, self.values = gpu, np.asarray(values, dtype=float)

    def cpu(self):
        self.gpu.use(0.001)
        return self

    def numpy(self):
        return self.values

    def __len__(self):
        return len(self.values)


class GpuBoxes:
    def __init__(self, gpu: FakeGpu):
        self.xyxy = GpuTensor(gpu, [[10, 10, 60, 90]])
        self.xyxyn = GpuTensor(gpu, [[0.1, 0.1, 0.6, 0.9]])
        self.conf = GpuTensor(gpu, [0.9])

    def __len__(self):
        return 1


class GpuModel:
    def __init__(self, gpu: FakeGpu):
        self.gpu = gpu

    def predict(self, **_):
        self.gpu.use(0.004)
        keypoints = SimpleNamespace(
            xy=GpuTensor(self.gpu, [[[30, 50]] * 17]),
            xyn=GpuTensor(self.gpu, [[[0.3, 0.5]] * 17]),
            conf=GpuTensor(self.gpu, [[0.8] * 17]),
        )
        return [SimpleNamespace(boxes=GpuBoxes(self.gpu), keypoints=keypoints)]


def run_live_pose_beside_analysis(tmp_path) -> FakeGpu:
    gpu = FakeGpu()
    analysis = UltralyticsTopDownBackend.__new__(UltralyticsTopDownBackend)
    analysis.config = BackendConfig(device="mps")
    analysis.detector = analysis.pose = GpuModel(gpu)
    live = LivePoseDetector(make_settings(tmp_path))
    live._model, live._device = GpuModel(gpu), "mps"
    frame = np.zeros((100, 100, 3), np.uint8)
    errors: list[BaseException] = []

    def repeat(call):
        try:
            for _ in range(20):
                call()
        except BaseException as exc:  # a thread's exception would otherwise vanish
            errors.append(exc)

    workers = [
        threading.Thread(target=repeat, args=(lambda: analysis.detect(frame, (0, 0, 100, 100), None),)),
        threading.Thread(target=repeat, args=(lambda: analysis.estimate_pose(frame, np.array([20.0, 20.0, 70.0, 90.0]), 0.9),)),
        threading.Thread(target=repeat, args=(lambda: live.detect(JPEG),)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert errors == []
    return gpu


def test_live_pose_and_analysis_never_use_the_gpu_at_the_same_time(tmp_path):
    gpu = run_live_pose_beside_analysis(tmp_path)
    assert gpu.uses > 0
    assert not gpu.overlapped


def test_without_the_shared_lock_the_overlap_is_detected(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_module, "INFERENCE_LOCK", contextlib.nullcontext())
    monkeypatch.setattr(live_pose_module, "INFERENCE_LOCK", contextlib.nullcontext())
    assert run_live_pose_beside_analysis(tmp_path).overlapped


# Tip


def test_tip_for_a_missing_dive_is_404(client):
    response = client.post("/live/tip", json={"dive_id": str(uuid4()), "previous_dive_id": None, "dive_number": 1})
    assert response.status_code == 404


def test_tip_uses_the_saved_analyses(client):
    sloppy, clean = sloppy_analysis(), clean_analysis()
    previous_id, dive_id = insert_dive(clean), insert_dive(sloppy)

    response = client.post("/live/tip", json={"dive_id": dive_id, "previous_dive_id": previous_id, "dive_number": 3})
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "rules"
    assert "down 4.2 from your last dive" in response.json()["tip"]
    analysis, setup, previous, number = main.app.state.coach.tip_calls[-1]
    assert analysis == sloppy and previous == clean and setup == TUCK_103C and number == 3

    missing_previous = {"dive_id": dive_id, "previous_dive_id": str(uuid4()), "dive_number": 2}
    assert client.post("/live/tip", json=missing_previous).status_code == 200
    assert main.app.state.coach.tip_calls[-1][2] is None
    assert client.post("/live/tip", json={"dive_id": dive_id, "dive_number": 0}).status_code == 422


def test_tip_endpoint_with_gemini(client):
    main.app.state.coach = coach_with(FakeInteractions(output_text=json.dumps({"tip": GOOD_TIP})))
    dive_id = insert_dive(sloppy_analysis())
    response = client.post("/live/tip", json={"dive_id": dive_id, "previous_dive_id": None, "dive_number": 1})
    assert response.json() == {"tip": GOOD_TIP, "source": "gemini"}


def test_rule_tip_for_a_sloppy_dive():
    analysis = sloppy_analysis()
    top = analysis["faults"][0]
    tip = rule_based_tip(analysis)
    assert top["id"] == "under_rotation"
    assert CUES[top["id"]].lower() in tip.lower()
    assert tip.startswith("Strong takeoff")
    assert words(tip) <= 35
    assert "last dive" not in tip
    assert not any(mark in tip for mark in "*#`\n")


def test_rule_tip_compares_with_the_previous_dive():
    sloppy, clean = sloppy_analysis(), clean_analysis()
    better = rule_based_tip(clean, sloppy, dive_number=2)
    assert "up 4.2 from your last dive" in better and "No faults flagged" in better
    assert "down 4.2 from your last dive" in rule_based_tip(sloppy, clean, dive_number=2)

    nearly_same = {**sloppy, "scores": {**sloppy["scores"], "overall": sloppy["scores"]["overall"] - 0.2}}
    assert "last dive" not in rule_based_tip(sloppy, nearly_same, dive_number=2)


def test_rule_tip_stays_short_for_every_fault():
    low = {"overall": 2.0, "takeoff": 2.0, "flight": 2.0, "entry": 2.0}
    for fault_id in CUES:
        for phase in ("takeoff", "flight", "entry"):
            analysis = {"scores": low, "faults": [{"id": fault_id, "title": "x", "phase": phase, "impact": 5.0}]}
            for number in (1, 2):
                tip = rule_based_tip(analysis, {"scores": {"overall": 9.9}}, number)
                assert words(tip) <= 35, tip
                assert "last dive" in tip


def test_gemini_tip_sends_compact_context():
    analysis, previous = sloppy_analysis(), clean_analysis()
    interactions = FakeInteractions(output_text=json.dumps({"tip": GOOD_TIP}))
    tip, source = coach_with(interactions).quick_tip(analysis, TUCK_103C, previous, dive_number=2)

    assert (tip, source) == (GOOD_TIP, "gemini")
    kwargs = interactions.kwargs
    assert kwargs["store"] is False
    assert kwargs["generation_config"] == {"thinking_level": "low"}
    assert kwargs["system_instruction"] == LIVE_TIP_INSTRUCTION
    assert kwargs["response_format"]["schema"] == LiveTip.model_json_schema()
    assert 0 < kwargs["timeout"] <= 45
    sent = json.loads(kwargs["input"])
    assert sent["dive_number"] == 2 and sent["dive"] == TUCK_103C
    assert sent["scores"] == analysis["scores"]
    assert [f["id"] for f in sent["top_faults"]] == [f["id"] for f in analysis["faults"][:3]]
    assert sent["top_faults"][0]["cue"] == CUES[analysis["faults"][0]["id"]]
    assert sent["previous_dive"] == {"scores": previous["scores"], "top_faults": []}
    assert "warnings" in sent and "metrics" not in sent and "series" not in sent


def test_gemini_tip_errors_fall_back_without_logging_the_key(caplog):
    analysis = sloppy_analysis()
    interactions = FakeInteractions(error=RuntimeError("401 for key test-secret-key"))
    with caplog.at_level(logging.WARNING):
        tip, source = coach_with(interactions).quick_tip(analysis, TUCK_103C, None, 1)
    assert (tip, source) == (rule_based_tip(analysis, None, 1), "rules")
    assert "test-secret-key" not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("reply", [json.dumps({"tip": "word " * 36}), json.dumps({"tip": "   "}), "not json"])
def test_unusable_gemini_tips_fall_back_to_rules(reply, caplog):
    with caplog.at_level(logging.WARNING):
        tip, source = coach_with(FakeInteractions(output_text=reply)).quick_tip(sloppy_analysis(), TUCK_103C)
    assert source == "rules" and tip
    assert "Gemini live tip failed" in caplog.text


def test_tip_without_gemini_uses_rules():
    analysis = sloppy_analysis()
    assert GeminiCoach(None, "gemini-3.8-flash").quick_tip(analysis, TUCK_103C) == (rule_based_tip(analysis), "rules")


# Speech


def test_speech_is_204_when_elevenlabs_is_not_configured(client):
    response = client.post("/live/speech", json={"text": "Nice dive."})
    assert response.status_code == 204
    assert response.content == b""
    assert isinstance(main.app.state.speech, SpeechClient)


def test_speech_returns_mp3(client):
    main.app.state.speech = FakeSpeech()
    response = client.post("/live/speech", json={"text": "  Nice dive.  "})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"ID3-fake-mp3"
    assert main.app.state.speech.texts == ["Nice dive."]


def test_speech_upstream_failure_is_502(client):
    main.app.state.speech = FakeSpeech(error=SpeechError("ElevenLabs returned HTTP 401: invalid key"))
    response = client.post("/live/speech", json={"text": "Nice dive."})
    assert response.status_code == 502
    assert "401" not in response.json()["detail"]


def test_speech_text_must_be_1_to_400_characters(client):
    main.app.state.speech = FakeSpeech()
    for text in ("", "   ", "x" * 401):
        assert client.post("/live/speech", json={"text": text}).status_code == 422
    assert client.post("/live/speech", json={}).status_code == 422
    assert client.post("/live/speech", json={"text": "x" * 400}).status_code == 200


def elevenlabs_settings(tmp_path) -> Settings:
    return make_settings(tmp_path, elevenlabs_api_key="el-secret-key", elevenlabs_voice_id="voice123")


def test_speech_client_calls_elevenlabs(monkeypatch, tmp_path):
    requests = []

    def fake_post(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, content=b"mp3-bytes")

    monkeypatch.setattr(speech_module.httpx, "post", fake_post)
    client = SpeechClient(elevenlabs_settings(tmp_path))
    assert client.configured
    assert client.synthesize("Nice dive.") == b"mp3-bytes"
    url, kwargs = requests[0]
    assert url == "https://api.elevenlabs.io/v1/text-to-speech/voice123"
    assert kwargs["headers"] == {"xi-api-key": "el-secret-key", "Accept": "audio/mpeg"}
    assert kwargs["json"] == {"text": "Nice dive.", "model_id": "eleven_flash_v2_5"}
    assert kwargs["timeout"] == 20.0


def test_speech_client_errors_never_contain_the_key(monkeypatch, tmp_path):
    client = SpeechClient(elevenlabs_settings(tmp_path))
    monkeypatch.setattr(speech_module.httpx, "post", lambda url, **kw: httpx.Response(401, text="bad key el-secret-key"))
    with pytest.raises(SpeechError) as rejected:
        client.synthesize("Nice dive.")
    assert "401" in str(rejected.value) and "el-secret-key" not in str(rejected.value)

    def timeout(url, **kwargs):
        raise httpx.ConnectTimeout("timed out using el-secret-key")

    monkeypatch.setattr(speech_module.httpx, "post", timeout)
    with pytest.raises(SpeechError) as timed_out:
        client.synthesize("Nice dive.")
    assert "ConnectTimeout" in str(timed_out.value) and "el-secret-key" not in str(timed_out.value)


def test_speech_needs_both_key_and_voice(tmp_path):
    assert not SpeechClient(make_settings(tmp_path, elevenlabs_api_key="el-secret-key")).configured
    assert not SpeechClient(make_settings(tmp_path, elevenlabs_voice_id="voice123")).configured


# Settings


def test_blank_live_settings_are_none_or_defaults(monkeypatch, tmp_path):
    for name, value in {
        "ELEVENLABS_API_KEY": "",
        "ELEVENLABS_VOICE_ID": "  ",
        "ELEVENLABS_MODEL": "",
        "LIVE_POSE_MODEL": "",
        "LIVE_POSE_SIZE": "",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None, gemini_api_key="test-key", upload_dir=tmp_path)
    assert settings.elevenlabs_api_key is None and settings.elevenlabs_voice_id is None
    assert settings.elevenlabs_model == "eleven_flash_v2_5"
    assert (settings.live_pose_model, settings.live_pose_size) == ("yolo11n-pose.pt", 480)


def test_placeholder_elevenlabs_key_is_rejected_without_echoing_it(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "your-elevenlabs-api-key")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exc:
            get_settings()
    finally:
        get_settings.cache_clear()
    assert "ELEVENLABS_API_KEY" in str(exc.value)
    assert "your-elevenlabs" not in str(exc.value)
