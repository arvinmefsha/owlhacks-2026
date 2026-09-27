from __future__ import annotations

import math

import numpy as np
from filterpy.kalman import KalmanFilter


class ConstantAccelerationPointFilter:
    """2-D constant-acceleration Kalman filter with confidence-adaptive updates."""

    def __init__(
        self,
        process_variance: float = 900.0,
        measurement_variance: float = 16.0,
        confidence_threshold: float = 0.4,
        max_prediction_seconds: float = 0.35,
    ) -> None:
        self.process_variance = float(process_variance)
        self.measurement_variance = float(measurement_variance)
        self.confidence_threshold = float(confidence_threshold)
        self.max_prediction_seconds = float(max_prediction_seconds)
        self.filter = KalmanFilter(dim_x=6, dim_z=2)
        self.filter.H = np.array([[1, 0, 0, 0, 0, 0], [0, 1, 0, 0, 0, 0]], dtype=float)
        self.filter.P = np.diag([25.0, 25.0, 400.0, 400.0, 2500.0, 2500.0])
        self.initialized = False
        self.last_timestamp: float | None = None
        self.time_since_measurement = math.inf
        self.last_measured_confidence = 0.0

    @staticmethod
    def _transition(dt: float) -> np.ndarray:
        half_dt2 = 0.5 * dt * dt
        return np.array(
            [
                [1, 0, dt, 0, half_dt2, 0],
                [0, 1, 0, dt, 0, half_dt2],
                [0, 0, 1, 0, dt, 0],
                [0, 0, 0, 1, 0, dt],
                [0, 0, 0, 0, 1, 0],
                [0, 0, 0, 0, 0, 1],
            ],
            dtype=float,
        )

    def _process_noise(self, dt: float) -> np.ndarray:
        # White jerk noise, independently applied to x and y.
        q1 = (
            np.array(
                [
                    [dt**5 / 20, dt**4 / 8, dt**3 / 6],
                    [dt**4 / 8, dt**3 / 3, dt**2 / 2],
                    [dt**3 / 6, dt**2 / 2, dt],
                ],
                dtype=float,
            )
            * self.process_variance
        )
        q = np.zeros((6, 6), dtype=float)
        q[np.ix_([0, 2, 4], [0, 2, 4])] = q1
        q[np.ix_([1, 3, 5], [1, 3, 5])] = q1
        return q

    def step(
        self, timestamp: float, measurement: np.ndarray | None, confidence: float
    ) -> tuple[np.ndarray, float, bool]:
        measured = (
            measurement is not None
            and np.asarray(measurement).shape == (2,)
            and np.isfinite(measurement).all()
            and confidence >= self.confidence_threshold
        )
        if not self.initialized:
            self.last_timestamp = float(timestamp)
            if not measured:
                return np.full(2, np.nan), 0.0, True
            self.filter.x = np.array(
                [measurement[0], measurement[1], 0, 0, 0, 0], dtype=float
            ).reshape(6, 1)
            self.initialized = True
            self.time_since_measurement = 0.0
            self.last_measured_confidence = float(confidence)
            return np.asarray(measurement, dtype=float), float(confidence), False

        raw_dt = float(timestamp) - float(self.last_timestamp)
        dt = float(np.clip(raw_dt, 1e-3, 0.2))
        self.last_timestamp = float(timestamp)
        self.filter.F = self._transition(dt)
        self.filter.Q = self._process_noise(dt)
        self.filter.predict()

        if measured:
            safe_confidence = float(np.clip(confidence, self.confidence_threshold, 1.0))
            self.filter.R = (
                np.eye(2)
                * self.measurement_variance
                / (safe_confidence * safe_confidence)
            )
            self.filter.update(np.asarray(measurement, dtype=float))
            self.time_since_measurement = 0.0
            self.last_measured_confidence = safe_confidence
            return self.filter.x[:2, 0].copy(), safe_confidence, False

        self.time_since_measurement += max(raw_dt, 0.0)
        if self.time_since_measurement > self.max_prediction_seconds:
            return np.full(2, np.nan), 0.0, True
        decay = math.exp(
            -3.0 * self.time_since_measurement / self.max_prediction_seconds
        )
        return self.filter.x[:2, 0].copy(), self.last_measured_confidence * decay, True


class KeypointFilter:
    """Runs an independent motion model for every body landmark."""

    def __init__(self, keypoint_count: int = 17, **point_filter_options: float) -> None:
        self.filters = [
            ConstantAccelerationPointFilter(**point_filter_options)
            for _ in range(keypoint_count)
        ]

    def step(
        self,
        timestamp: float,
        keypoints: np.ndarray | None,
        confidence: np.ndarray | None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        count = len(self.filters)
        output = np.full((count, 2), np.nan, dtype=float)
        output_confidence = np.zeros(count, dtype=float)
        predicted = np.ones(count, dtype=bool)
        for index, point_filter in enumerate(self.filters):
            point = (
                None if keypoints is None else np.asarray(keypoints[index], dtype=float)
            )
            score = 0.0 if confidence is None else float(confidence[index])
            output[index], output_confidence[index], predicted[index] = (
                point_filter.step(timestamp, point, score)
            )
        return output, output_confidence, predicted
