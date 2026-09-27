import numpy as np
import pytest

from diving_tracker.refinement import refine_poses
from diving_tracker.backend import unrotate_points
from diving_tracker.timing import read_timeline


def test_strong_fast_motion_has_no_phase_delay():
    times = np.arange(240)/60
    raw = np.zeros((240, 17, 2))
    raw[:, :, 0] = (300+100*np.sin(4*np.pi*times))[:, None]
    raw[:, :, 1] = 100
    points, scores, estimated = refine_poses(times, raw, np.full((240,17), .9), 600)
    np.testing.assert_array_equal(points, raw)
    assert not estimated.any()


def test_short_bracketed_gap_but_no_extrapolation_or_water_crossing():
    times = np.arange(12)/60
    raw = np.tile(np.array([100.,100.]), (12,17,1))
    scores = np.ones((12,17))*.9
    scores[3:5, 9] = 0
    scores[6:, 10] = 0
    raw[5, 9, 0] = 103
    raw[:, 11, 1] = 650
    scores[3:5,11] = 0
    points, confidence, estimated = refine_poses(times, raw, scores, 600)
    assert estimated[3:5,9].all()
    assert (confidence[3:5,9] <= .29).all()
    assert np.isnan(points[6:,10]).all()
    assert np.isnan(points[3:5,11]).all()


@pytest.mark.parametrize("turns", [0,1,2,3])
def test_rotated_pixel_coordinates_return_to_original(turns):
    image = np.zeros((80,120), dtype=np.uint8)
    image[20,30] = 1
    y,x = np.argwhere(np.rot90(image, turns))[0]
    np.testing.assert_array_equal(unrotate_points(np.array([[x,y]],float),120,80,turns),[[30,20]])


def test_vfr_timestamps_and_nonzero_start(tmp_path):
    # Isolate AV/FFmpeg from OpenCV in this process, as in production.
    import subprocess, sys
    path = tmp_path/'vfr.mp4'
    subprocess.run([sys.executable, '-c', '''
import av, numpy as np, sys
from fractions import Fraction
with av.open(sys.argv[1], 'w') as c:
 s=c.add_stream('libx264',rate=60);s.width=64;s.height=64;s.pix_fmt='yuv420p';s.time_base=Fraction(1,6000)
 for pts in [12000,12100,12400,12500]:
  f=av.VideoFrame.from_ndarray(np.zeros((64,64,3),dtype=np.uint8),format='rgb24');f.pts=pts;f.time_base=Fraction(1,6000)
  for p in s.encode(f): c.mux(p)
 for p in s.encode(): c.mux(p)
''',str(path)],check=True)
    timeline = read_timeline(path)
    np.testing.assert_allclose(timeline.times, [0,1/60,4/60,5/60],atol=1e-5)
    assert timeline.origin_seconds == pytest.approx(2.)
    assert timeline.metadata()['variable_rate']


def test_propagated_crop_failure_reacquires_on_same_frame(monkeypatch):
    from types import SimpleNamespace
    import diving_tracker.tracker as module
    from diving_tracker.backend import BackendConfig
    from diving_tracker.calibration import CalibrationData
    from diving_tracker.models import PoseMeasurement
    from diving_tracker.timing import VideoTimeline
    class Capture:
        index=0
        def isOpened(self): return True
        def get(self, key): return 30 if key==module.cv2.CAP_PROP_FPS else 8 if key==module.cv2.CAP_PROP_FRAME_COUNT else 100
        def read(self):
            if self.index==8:return False,None
            frame=np.full((100,100,3),self.index,dtype=np.uint8);self.index+=1;return True,frame
        def release(self): pass
    class Backend:
        config=BackendConfig(device='cpu')
        detects=[]
        poses=[]
        def detect(self, frame, roi, predicted):
            self.detects.append(int(frame[0,0,0]));return np.array([20,20,70,80]),.9
        def estimate_pose(self, frame, box, confidence, **kwargs):
            index=int(frame[0,0,0]);self.poses.append(index)
            if index==2 and self.poses.count(2)==1:return None
            points=np.tile([45.,50.],(17,1));points[5:7,1]=35;points[11:13,1]=60
            return PoseMeasurement(points,np.full(17,.9),np.array([20,20,70,80]),.9)
    monkeypatch.setattr(module,'read_timeline',lambda p:VideoTimeline(np.arange(8)/30,0.))
    monkeypatch.setattr(module.cv2,'VideoCapture',lambda p:Capture())
    monkeypatch.setattr(module.BoardTipTracker,'step',lambda self, frame:np.array([40.,40.]))
    monkeypatch.setattr(module.KinematicsAnalyzer,'analyze',lambda self, tracks:SimpleNamespace(summary={}))
    backend=Backend()
    tracks,_=module.DivingTracker(backend=backend).process('fake',CalibrationData((40,40),(40,90),(0,0,100,100)))
    assert 2 in backend.detects
    assert len(backend.detects)<8
    assert set(backend.poses)==set(range(8))
    assert len(tracks)==8
