import unittest

import numpy as np

from diving_tracker.calibration import CalibrationData
from diving_tracker.filters import ConstantAccelerationPointFilter
from diving_tracker.kinematics import (
    KinematicsAnalyzer,
    anthropometric_com,
    stabilize_left_right,
)


class CalibrationTests(unittest.TestCase):
    def test_one_meter_scale_uses_vertical_distance(self):
        calibration = CalibrationData((100.0, 80.0), (105.0, 480.0), (0, 0, 640, 520))
        self.assertEqual(calibration.pixels_per_meter, 400.0)
        calibration.validate(640, 520)

    def test_non_one_meter_reference_scales_distance(self):
        calibration = CalibrationData(
            (100.0, 80.0),
            (105.0, 480.0),
            (0, 0, 640, 520),
            vertical_reference_m=2.0,
        )
        self.assertEqual(calibration.pixels_per_meter, 200.0)


class FilterTests(unittest.TestCase):
    def test_low_confidence_gap_is_predicted_then_expires(self):
        point_filter = ConstantAccelerationPointFilter(max_prediction_seconds=0.2)
        point, confidence, predicted = point_filter.step(
            0.0, np.array([10.0, 20.0]), 0.9
        )
        self.assertFalse(predicted)
        self.assertTrue(np.isfinite(point).all())
        point, confidence, predicted = point_filter.step(
            0.05, np.array([0.0, 0.0]), 0.1
        )
        self.assertTrue(predicted)
        self.assertGreater(confidence, 0.0)
        self.assertGreater(point[0], 1.0)
        point, confidence, predicted = point_filter.step(0.25, None, 0.0)
        self.assertTrue(predicted)
        self.assertTrue(np.isnan(point).all())
        self.assertEqual(confidence, 0.0)


class KinematicsTests(unittest.TestCase):
    def test_anthropometric_com_is_finite_with_complete_pose(self):
        points = np.stack([np.array([100.0 + i, 50.0 + 3 * i]) for i in range(17)])
        com = anthropometric_com(points)
        self.assertTrue(np.isfinite(com).all())
        self.assertGreaterEqual(com[0], points[:, 0].min())
        self.assertLessEqual(com[0], points[:, 0].max())

    def test_global_left_right_flip_is_corrected(self):
        reference = np.zeros((17, 2), dtype=float)
        points = np.zeros((17, 2), dtype=float)
        confidence = np.ones(17, dtype=float)
        for left, right in ((5, 6), (7, 8), (11, 12)):
            reference[left] = [10.0 + left, 20.0]
            reference[right] = [100.0 + right, 20.0]
            points[left] = reference[right]
            points[right] = reference[left]
        corrected, _ = stabilize_left_right(points, confidence, reference)
        np.testing.assert_allclose(
            corrected[[5, 6, 7, 8, 11, 12]], reference[[5, 6, 7, 8, 11, 12]]
        )

    def test_ballistic_fit_recovers_gravity(self):
        times = np.linspace(0.0, 1.0, 61)
        heights = 1.2 + 3.0 * times - 0.5 * 9.81 * times**2
        fit = KinematicsAnalyzer._ballistic_fit(times, heights, 0, len(times) - 1)
        self.assertAlmostEqual(fit["gravity_m_s2"], 9.81, places=4)
        self.assertAlmostEqual(fit["r_squared"], 1.0, places=6)
        self.assertTrue(fit["verified"])


if __name__ == "__main__":
    unittest.main()
