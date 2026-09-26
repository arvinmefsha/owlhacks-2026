# AI Dive Coach MVP

Bare-bones prototype for analyzing one side-view dive video with MediaPipe Pose.

## Run locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Upload a video, click the two endpoints of the board in the calibration frame, and then click **Analyze calibrated dive**. The prototype tracks foot-to-board distance and visual board movement in addition to pose feedback.

This first version intentionally uses interpretable heuristics. Keep the phone stationary. Board distance is reported in pixels, and board depression is an optical-flow estimate; physical units require a scale reference and a clearly visible board.
