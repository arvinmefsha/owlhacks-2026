"""Bare-bones AI dive coach prototype. Run with: streamlit run app.py"""
from __future__ import annotations

import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import streamlit as st
from streamlit_image_coordinates import streamlit_image_coordinates
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
MODEL_PATH = Path(__file__).parent / ".cache" / "pose_landmarker_lite.task"
POSE = {"nose": 0, "left_shoulder": 11, "right_shoulder": 12,
        "left_elbow": 13, "right_elbow": 14, "left_wrist": 15,
        "right_wrist": 16, "left_hip": 23, "right_hip": 24,
        "left_ankle": 27, "right_ankle": 28}
CONNECTIONS = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
               (11, 23), (12, 24), (23, 24), (23, 25), (25, 27),
               (24, 26), (26, 28)]


@dataclass
class FrameMetrics:
    frame: int
    time_s: float
    visibility: float
    body_angle: float | None
    arm_angle: float | None
    foot_y: float | None
    foot_x: float | None
    board_distance_px: float | None
    board_depression_px: float | None


def ensure_model() -> Path:
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    ba, bc = a - b, c - b
    denominator = np.linalg.norm(ba) * np.linalg.norm(bc)
    if denominator == 0:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(np.dot(ba, bc) / denominator, -1, 1))))


def draw_landmarks(frame: np.ndarray, landmarks, board_points=None) -> None:
    height, width = frame.shape[:2]
    points = [(int(p.x * width), int(p.y * height)) for p in landmarks]
    for start, end in CONNECTIONS:
        cv2.line(frame, points[start], points[end], (0, 220, 0), 2)
    for point in points:
        cv2.circle(frame, point, 4, (0, 120, 255), -1)
    if board_points is not None:
        p1, p2 = board_points.astype(int)
        cv2.line(frame, tuple(p1), tuple(p2), (255, 80, 0), 4)


def analyze_video(path: str, board_points: np.ndarray, sample_every: int = 2):
    capture = cv2.VideoCapture(path)
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    metrics, annotated_frames = [], []
    reference_gray = None
    tracked_board = board_points.astype(np.float32).reshape(-1, 1, 2)
    reference_board = tracked_board.reshape(-1, 2).copy()
    options = vision.PoseLandmarkerOptions(
        base_options=python.BaseOptions(
            model_asset_path=str(ensure_model()),
            delegate=python.BaseOptions.Delegate.CPU,
        ),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        frame_index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % sample_every:
                frame_index += 1
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if reference_gray is None:
                reference_gray = gray
            else:
                next_points, status, _ = cv2.calcOpticalFlowPyrLK(
                    previous_gray, gray, tracked_board, None,
                    winSize=(21, 21), maxLevel=3,
                    criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
                )
                if next_points is not None and status is not None and np.all(status):
                    tracked_board = next_points
            previous_gray = gray
            result = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb),
                int(frame_index * 1000 / fps),
            )
            if result.pose_landmarks:
                landmarks = result.pose_landmarks[0]

                def point(name):
                    p = landmarks[POSE[name]]
                    return np.array([p.x * width, p.y * height], dtype=float)

                required = [POSE[n] for n in ("left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_ankle", "right_ankle")]
                visibility = float(np.mean([landmarks[p].visibility for p in required]))
                shoulder = (point("left_shoulder") + point("right_shoulder")) / 2
                hip = (point("left_hip") + point("right_hip")) / 2
                ankle = (point("left_ankle") + point("right_ankle")) / 2
                elbow = (point("left_elbow") + point("right_elbow")) / 2
                wrist = (point("left_wrist") + point("right_wrist")) / 2
                current_board = tracked_board.reshape(-1, 2)
                board_vector = current_board[1] - current_board[0]
                board_y_at_foot = current_board[0][1] + (ankle[0] - current_board[0][0]) * board_vector[1] / board_vector[0] if abs(board_vector[0]) > 1e-6 else current_board[:, 1].mean()
                board_distance_px = float(ankle[1] - board_y_at_foot)
                board_depression_px = float(current_board[:, 1].mean() - reference_board[:, 1].mean())
                metrics.append(FrameMetrics(frame_index, frame_index / fps, visibility,
                                            angle(shoulder, hip, ankle), angle(shoulder, elbow, wrist), float(ankle[1] / height),
                                            float(ankle[0]), board_distance_px, board_depression_px))
                draw_landmarks(frame, landmarks, current_board)
            annotated_frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            frame_index += 1
    capture.release()
    return metrics, annotated_frames, fps, frame_count


def build_feedback(metrics: list[FrameMetrics]) -> list[str]:
    usable = [m for m in metrics if m.visibility >= 0.45]
    if not usable:
        return ["I could not confidently track the diver. Try a brighter side-view video with the full body visible."]
    feedback = []
    body_angles = [m.body_angle for m in usable if m.body_angle is not None]
    arm_angles = [m.arm_angle for m in usable if m.arm_angle is not None]
    if body_angles and float(np.percentile(body_angles, 25)) < 145:
        feedback.append("Body alignment: the torso and legs bend noticeably. Try holding a tighter, straighter line during entry.")
    else:
        feedback.append("Body alignment: the tracked body line stays relatively straight in this clip.")
    if arm_angles and float(np.percentile(arm_angles, 25)) < 125:
        feedback.append("Arm position: the arms bend during part of the dive. Reach longer overhead before entry.")
    else:
        feedback.append("Arm position: the arms stay fairly extended in the tracked frames.")
    vertical_motion = max(m.foot_y for m in usable) - min(m.foot_y for m in usable)
    if vertical_motion < 0.12:
        feedback.append("Takeoff/flight: there is not enough vertical movement to estimate takeoff reliably.")
    else:
        feedback.append("Takeoff/flight: vertical movement was detected; add a fixed board reference to measure takeoff distance accurately.")
    board_distances = [m.board_distance_px for m in usable if m.board_distance_px is not None]
    board_depressions = [m.board_depression_px for m in usable if m.board_depression_px is not None]
    if board_distances:
        closest = min(board_distances)
        feedback.append(f"Board reference: the closest tracked foot position was {abs(closest):.0f} pixels from the calibrated board line.")
    if board_depressions and max(board_depressions) - min(board_depressions) > 8:
        feedback.append(f"Board movement: the board reference moved about {max(board_depressions) - min(board_depressions):.0f} pixels vertically. Treat this as a visual estimate until the camera and board are calibrated.")
    elif board_depressions:
        feedback.append("Board movement: no clear board depression was detected in the tracked reference points.")
    return feedback


st.set_page_config(page_title="AI Dive Coach", page_icon="🏊", layout="wide")
st.title("🏊 AI Dive Coach — Prototype")
st.caption("Stationary-phone pose-tracking experiment. Prop the phone up, keep the full board and diver visible, and do not move it during the dive.")
uploaded = st.file_uploader("Upload one dive video", type=["mp4", "mov", "avi", "m4v"])
if uploaded:
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as temp:
        temp.write(uploaded.getbuffer())
        video_path = temp.name
    capture = cv2.VideoCapture(video_path)
    ok, calibration_frame = capture.read()
    capture.release()
    if ok:
        calibration_frame = cv2.cvtColor(calibration_frame, cv2.COLOR_BGR2RGB)
        st.subheader("1. Calibrate the board")
        st.write("Click one point at each end of the visible board. These points become the stationary reference for foot distance and board movement.")
        display_width = 900
        clicked = streamlit_image_coordinates(calibration_frame, key="board_calibration", width=display_width)
        if clicked:
            points = st.session_state.setdefault("board_clicks", [])
            scale = calibration_frame.shape[1] / display_width
            candidate = (int(clicked["x"] * scale), int(clicked["y"] * scale))
            if not points or points[-1] != candidate:
                st.session_state["board_clicks"] = (points + [candidate])[-2:]
        points = st.session_state.get("board_clicks", [])
        st.write(f"Board points selected: {len(points)}/2")
        if len(points) == 2:
            st.success("Board calibrated. Keep the phone stationary for the analysis.")
            if st.button("Analyze calibrated dive", type="primary"):
                with st.spinner("Tracking pose and board reference..."):
                    st.session_state["analysis"] = analyze_video(video_path, np.array(points, dtype=float))
        else:
            st.info("Select the first board endpoint, then the second endpoint.")

analysis = st.session_state.get("analysis")
if analysis:
    metrics, frames, fps, frame_count = analysis
    usable = [m for m in metrics if m.visibility >= 0.45]
    col1, col2, col3 = st.columns(3)
    col1.metric("Frames analyzed", len(metrics))
    col2.metric("Tracking confidence", f"{100 * np.mean([m.visibility for m in metrics]):.0f}%" if metrics else "—")
    col3.metric("Video length", f"{frame_count / fps:.1f}s")
    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Pose preview")
        if frames:
            st.image(frames[-1], caption="Last analyzed frame", use_container_width=True)
    with right:
        st.subheader("Coaching feedback")
        for item in build_feedback(metrics):
            st.info(item)
        st.caption("Validate these simple rules with a diver or coach before treating them as reliable.")
    if usable:
        st.subheader("Measured signals")
        chart_data = {"Body angle": {m.time_s: m.body_angle for m in usable},
                      "Arm angle": {m.time_s: m.arm_angle for m in usable},
                      "Foot-to-board distance": {m.time_s: m.board_distance_px for m in usable},
                      "Board movement": {m.time_s: m.board_depression_px for m in usable}}
        st.line_chart(chart_data, x_label="Time (seconds)", y_label="Pixels / degrees")
