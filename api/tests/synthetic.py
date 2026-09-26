"""Synthetic MediaPipe-style frames for a side-on dive, with known ground truth.

The figure faces +x (right) and rotates forward (clockwise on screen). World units are metres
with y up and the water surface at y = 0; the board tip is at x = 0. As in a real dive, the
centre of mass (not the hips) follows the ballistic path once airborne.
"""

from dataclasses import dataclass

import numpy as np

from analysis.landmarks import center_of_mass

G = 9.81
WIDTH, HEIGHT = 1280, 720
PX_PER_M = 160.0
ORIGIN_PX = (400.0, 690.0)  # board tip x, water line y

# Segment lengths as fractions of standing height (match analysis.pipeline's proportions).
TORSO, THIGH, SHANK, FOOT, ARM, HEAD = 0.288, 0.245, 0.246, 0.11, 0.33, 0.13


def u(deg: float) -> np.ndarray:
    """Unit vector for a direction measured clockwise from up, in y-up world coordinates."""
    r = np.radians(deg)
    return np.array([np.sin(r), np.cos(r)])


def to_normalized(p: np.ndarray) -> tuple[float, float]:
    return (ORIGIN_PX[0] + p[0] * PX_PER_M) / WIDTH, (ORIGIN_PX[1] - p[1] * PX_PER_M) / HEIGHT


def keyframes(tau: float, points: list[tuple[float, float]]) -> float:
    xs, ys = zip(*points)
    return float(np.interp(tau, xs, ys))


def smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


@dataclass
class DiveSpec:
    height_m: float = 1.70
    board_height_m: float = 1.0
    v0: float = 4.5  # vertical takeoff speed of the centre of mass, m/s
    vx: float = 1.0  # horizontal speed, m/s
    rotation_deg: float = 540.0  # torso direction at entry (540 = 1.5 somersaults, head down)
    position_hip: float = 55.0
    position_knee: float = 50.0
    toe: float = 170.0
    entry_arm_lift: float = 178.0
    leg_gap_m: float = 0.02
    takeoff_s: float = 0.5
    fps: float = 60.0
    noise_px: float = 1.0
    floor_jump: bool = False  # jump from and land on the same floor (no water)
    seed: int = 7


@dataclass
class Pose:
    theta: float
    hip_angle: float
    knee_angle: float
    arm_lift: float
    toe: float


def _joints(hip: np.ndarray, pose: Pose, h: float) -> dict[str, np.ndarray]:
    shoulder = hip + TORSO * h * u(pose.theta)
    thigh_dir = pose.theta + pose.hip_angle
    knee = hip + THIGH * h * u(thigh_dir)
    shank_dir = thigh_dir + (180 - pose.knee_angle)
    ankle = knee + SHANK * h * u(shank_dir)
    arm_dir = pose.theta + 180 - pose.arm_lift
    return {
        "nose": shoulder + HEAD * h * u(pose.theta) + 0.05 * h * u(pose.theta + 90),
        "shoulder": shoulder,
        "elbow": shoulder + 0.5 * ARM * h * u(arm_dir),
        "wrist": shoulder + ARM * h * u(arm_dir),
        "hip": hip,
        "knee": knee,
        "ankle": ankle,
        "heel": ankle + 0.04 * h * u(shank_dir + 90),
        "foot": ankle + FOOT * h * u(shank_dir - (180 - pose.toe)),
    }


def _com_offset(pose: Pose, h: float) -> np.ndarray:
    """Centre of mass relative to the hip for this pose."""
    j = _joints(np.zeros(2), pose, h)
    return center_of_mass(j["shoulder"], j["hip"], j["knee"], j["ankle"], j["wrist"], j["foot"])


def _landmarks(j: dict[str, np.ndarray], spec: DiveSpec, rng) -> list[list[float]]:
    side = np.array([spec.leg_gap_m / 2, 0.0])
    lm = [[0.0, 0.0, 0.0, 0.0] for _ in range(33)]

    def put(idx: int, p: np.ndarray, vis: float) -> None:
        x, y = to_normalized(p + rng.normal(0, spec.noise_px / PX_PER_M, 2))
        lm[idx] = [x, y, 0.0, vis]

    put(0, j["nose"], 0.99)
    for near, far, name, offset in [
        (11, 12, "shoulder", 0.0), (13, 14, "elbow", 0.0), (15, 16, "wrist", 0.0), (23, 24, "hip", 0.0),
        (25, 26, "knee", 0.5), (27, 28, "ankle", 1.0), (29, 30, "heel", 1.0), (31, 32, "foot", 1.0),
    ]:
        put(near, j[name] + side * offset, 0.95)
        put(far, j[name] - side * offset, 0.6)
    return lm


def _pose_at(t: float, spec: DiveSpec, contact_tau: float) -> Pose:
    t0 = spec.takeoff_s
    crouch_end = t0 - 0.2
    if t < t0:
        s = min(max((t - crouch_end) / (t0 - crouch_end), 0.0), 1.0)
        bend = smoothstep(t / crouch_end) if t < crouch_end else 1 - smoothstep(s)
        arm = 30 - 20 * bend if t < crouch_end else 10 + 160 * smoothstep(s)
        return Pose(5 + 15 * bend, 175 - 45 * bend, 180 - 55 * bend, arm, 120)
    if spec.floor_jump:
        return Pose(5, 175, 178, 170, spec.toe)
    p = min((t - t0) / contact_tau, 1.0)
    return Pose(
        theta=10 + (spec.rotation_deg - 10) * smoothstep(p),
        hip_angle=keyframes(p, [(0, 172), (0.15, spec.position_hip), (0.7, spec.position_hip), (0.85, 178), (1, 178)]),
        knee_angle=keyframes(p, [(0, 178), (0.15, spec.position_knee), (0.7, spec.position_knee), (0.85, 178), (1, 178)]),
        arm_lift=keyframes(p, [(0, 170), (0.15, 60), (0.7, 60), (0.9, spec.entry_arm_lift), (1, spec.entry_arm_lift)]),
        toe=spec.toe,
    )


def generate(spec: DiveSpec) -> tuple[list[dict], dict]:
    """Return (frames, truth) with takeoff/apex/contact times and the jump height."""
    rng = np.random.default_rng(spec.seed)
    h = spec.height_m
    floor = 0.0 if spec.floor_jump else spec.board_height_m
    t0 = spec.takeoff_s
    crouch_end = t0 - 0.2
    hip_x0 = -0.1

    # Centre-of-mass height at takeoff: standing tall on the board in the takeoff pose.
    takeoff_pose = _pose_at(t0 - 1e-9, spec, 1.0)
    takeoff_com_y = floor + 0.53 * h + _com_offset(takeoff_pose, h)[1]

    if spec.floor_jump:
        contact_tau = 2 * spec.v0 / G
    else:
        # First contact when the lowest point of the (vertical) entry pose reaches the water.
        entry_pose = _pose_at(t0 + 10.0, spec, 1.0)
        j = _joints(np.zeros(2), entry_pose, h)
        lowest_below_com = _com_offset(entry_pose, h)[1] - min(p[1] for p in j.values())
        a, b, c = -0.5 * G, spec.v0, takeoff_com_y - lowest_below_com
        contact_tau = (-b - np.sqrt(b * b - 4 * a * c)) / (2 * a)
    t_contact = t0 + contact_tau

    frames = []
    for t in np.arange(0.0, t_contact + 0.4, 1.0 / spec.fps):
        pose = _pose_at(t, spec, contact_tau)
        if t < t0:
            s = min(max((t - crouch_end) / (t0 - crouch_end), 0.0), 1.0)
            dip = 0.2 * smoothstep(t / crouch_end) if t < crouch_end else 0.2
            # Cubic Hermite from rest to v0 over the push, so upward speed peaks at takeoff.
            push = 0.2 * (3 * s**2 - 2 * s**3) + (t0 - crouch_end) * spec.v0 * (s**3 - s**2) if t >= crouch_end else 0.0
            com = np.array([hip_x0, takeoff_com_y - dip + push])
        else:
            tau = t - t0
            y = takeoff_com_y + spec.v0 * tau - 0.5 * G * tau**2
            if spec.floor_jump and tau > contact_tau:
                # Knees soak up the landing: decelerate from landing speed to rest over 15 cm.
                decel = spec.v0**2 / (2 * 0.15)
                since = min(tau - contact_tau, spec.v0 / decel)
                y = takeoff_com_y - (spec.v0 * since - 0.5 * decel * since**2)
            com = np.array([hip_x0 + spec.vx * tau, y])
        hip = com - _com_offset(pose, h)

        lm = None
        if spec.floor_jump or t <= t_contact + 0.2:
            lm = _landmarks(_joints(hip, pose, h), spec, rng)
        frames.append({"t": round(float(t), 4), "lm": lm})

    truth = {
        "takeoff": t0,
        "apex": t0 + spec.v0 / G,
        "contact": t_contact,
        "jump_height_m": spec.v0**2 / (2 * G),
        "board_tip": {"x": ORIGIN_PX[0] / WIDTH, "y": (ORIGIN_PX[1] - spec.board_height_m * PX_PER_M) / HEIGHT},
        "water_y": ORIGIN_PX[1] / HEIGHT,
    }
    return frames, truth
