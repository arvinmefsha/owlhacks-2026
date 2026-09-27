import numpy as np
import pytest
from diving_tracker.dive_context import DiveContext, RotationState, angular_delta, orientation, select_sequence, recovery_needed, phase_hypothesis
from diving_tracker.models import PoseMeasurement


def pose(angle=0., confidence=.9):
    points=np.tile([100.,100.],(17,1))
    points[[5,6]]=np.array([100.,100.])+30*np.array([np.sin(angle),-np.cos(angle)])
    points[[11,12]]=[100,100]
    return PoseMeasurement(points,np.full(17,confidence),np.array([30,30,170,170]),confidence)


def test_orientation_wrap_and_mirrored_rotation():
    for sign in [-1,1]:
        state=RotationState()
        for i,angle in enumerate([2.9,3.,3.1,3.2]):state.update(pose(sign*angle),i/60)
        assert np.sign(state.omega)==sign
        assert abs(angular_delta(state.predict(4/60),sign*3.3))<.1
        assert state.predict(1.) is None


def test_ambiguous_torso_does_not_invent_orientation():
    p=pose();p.keypoints[[5,6]]=p.keypoints[[11,12]]
    assert orientation(p) is None


@pytest.mark.parametrize('context',[DiveContext(),DiveContext('tuck','forward',4.5),DiveContext('pike','back',.5)])
def test_labels_cannot_force_candidate_coordinates(context):
    opening=[pose(a) for a in [0,.3,.7,1.,.9,.6]]
    candidates=[[p,pose(3.,.4)] for p in opening]
    # Selection deliberately receives observations only, not target rotation.
    assert select_sequence(candidates,np.arange(6)/30)==[0]*6
    assert context.somersaults is None or isinstance(context.somersaults,float)


def test_confident_rotation_jump_triggers_recovery():
    assert recovery_needed(pose(2.),pose(0.),.1,DiveContext())


def test_strong_new_observation_beats_stale_smooth_candidate():
    candidates=[[pose(0.)],[pose(0.,.5),pose(1.5,.95)],[pose(1.7,.95)]]
    assert select_sequence(candidates,np.arange(3)/60)==[0,1,0]


def test_missing_observations_reset_sequence_constraint():
    assert select_sequence([[pose()],[None],[pose(3.)]],[0,.5,1.])==[0,0,0]


def test_phase_uses_observed_opening_and_water_not_declared_rotation():
    p=pose();p.keypoints[[13,14]]=[100,75]
    assert phase_hypothesis(p,np.array([0,0]),300,'flight')=='compact'
    p.keypoints[[13,14]]=[100,140]
    assert phase_hypothesis(p,np.array([0,0]),300,'compact')=='opening'
    p.keypoints[:,1]+=300
    assert phase_hypothesis(p,np.array([0,0]),300,'opening')=='entry'
    assert phase_hypothesis(None,np.array([0,0]),300,'entry')=='unknown'
