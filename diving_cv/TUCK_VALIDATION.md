# Dive-aware recovery validation

## Rotation carry follow-up

The original context scheduler could miss the useful crop orientation and then
revert to an unrotated crop on the very next frame. On frame 330, direct comparison
of all four quarter-turns recovered the extended arms with turn 3 while the original
orientation confidently misplaced them. The revised scheduler carries successful
orientation forward, searches the remaining orientations during airborne recovery,
and explicitly weights distal-arm confidence. An airborne detector box can initiate
recovery even when no reliable joints remain.

On the same 465-frame clip, the 18-joint spot-check mean error improved from 40.0 to
16.0 pixels and median from 26.7 to 11.2 pixels. Opening-arm confidence coverage
(frames 315–340, elbows/wrists ≥0.35) improved from 86.5% to 94.2%. Full flight body
coverage changed from 91.9% to 91.5%; the change is not a universal coverage gain.
Processing increased from 42.65 to 49.88 seconds on CPU. These remain small-sample,
approximate checks, not ground truth for every frame or joint. Some compact-pose
arms are still misplaced; hidden limbs remain uncertain.

The new budget supersedes the original limits below: three alternative orientations
per recovery event, events spaced at least three frames apart, total extra passes
capped at floor(source frames / 3), minimum 3. The successful rotation is reused
without extra inference between recovery events. Preparation on the board does not
consume this airborne budget.

Tested on the local CPU environment, September 27, 2026. No model weights changed.
The comparison baseline is commit `8d3b142` (768-pixel pose input and offline refinement).

## Measurements

The 465-frame tuck clip took 40.65 seconds at baseline and 42.65 seconds with context
and sequence selection (about 5% additional processing). The exact presentation
timestamps remain unchanged. No source frames were skipped.

Eighteen approximate manually located visible joint centers across three frames
(tight tuck, opening, inverted extension) were assessed using the nearer left/right
member of each anatomical pair because the far side is ambiguous. Mean error changed
from 50.4 to 40.0 source pixels; median error changed from 23.3 to 26.7 pixels. This is
a small spot check, not a blinded benchmark, and cannot quantify left/right accuracy.
For frames 290–354, the fraction of body joints with confidence at least 0.35 changed
from 90.5% to 91.9%. Confidence coverage is not an accuracy measurement.

Rendered inspection still shows incorrect arm placement on some inverted frames.
Entry metrics can be unavailable when the last supported joints disappear before
surface contact. The implementation does not invent those observations. Broader
diving-specific labels are needed before claiming general accuracy improvement.

The final 270-frame pike control measured 24.99 seconds before and 25.16 seconds
with context recovery; timestamps remained unchanged.
No straight-dive video was available. Synthetic tests cover missing/wrong context,
early opening, wraparound, mirrored spin, ambiguous torsos, and discontinuities.

## Design limits

Direction and somersault count are recorded but never impose the rotation sign,
trajectory, or final pose. Tuck context affects recovery scheduling; sequence scoring
uses observed candidates. Continuity penalties are capped to avoid suppressing fast
real motion. Phase hypotheses are diagnostic and do not force the dive into a script.

Retry frames are at least three frames apart. At most two alternative quarter-turn
orientations are evaluated on an ambiguous retry frame, with a total budget of
floor(source frames / 8) extra passes. This is bounded recovery, not exhaustive
orientation search. Uncertain orientation can still choose the wrong crop.

New jobs retain candidates and selections in their local tracking NPZ artifact.
Old results require reanalysis; this change does not rewrite their poses.
