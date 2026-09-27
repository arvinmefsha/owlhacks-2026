import numpy as np
from diving_tracker.models import PoseMeasurement
from diving_tracker.rotation_recovery import OrientationRecovery, recovery_score


def pose(turn=0, arms=.9):
    points=np.tile([100.,100.],(17,1))
    angle=turn*np.pi/2
    points[[5,6]]+=30*np.array([np.sin(angle),-np.cos(angle)])
    points[[15,16]]=[100,170]
    scores=np.full(17,.9);scores[[7,8,9,10]]=arms
    return PoseMeasurement(points,scores,np.array([50,50,150,180]),.9)


def test_successful_rotation_is_reused_and_all_alternatives_are_considered():
    r=OrientationRecovery(90)
    upright, inverted=pose(),pose(2)
    r.accept([(0,upright)])
    turns=r.alternatives(inverted,inverted.box,20,np.array([100,170]),300,True)
    assert set(turns)=={1,2,3}
    best=pose(2);best.confidence[:]=.99
    assert r.accept([(0,pose(2,.3)),(3,best)]) is best
    assert r.turn==3
    assert r.alternatives(best,best.box,21,np.array([100,170]),300,True)==[]


def test_budget_is_not_spent_while_waiting_on_board_or_underwater():
    r=OrientationRecovery(30);p=pose()
    for i in range(20):
        assert r.alternatives(p,p.box,i,np.array([100,170]),300,False)==[]
    assert r.used==0
    assert r.alternatives(p,np.array([50,310,150,400]),20,np.array([100,170]),300,True)==[]


def test_missing_arms_trigger_reacquisition_and_extra_passes_are_bounded():
    r=OrientationRecovery(30);r.airborne=True;p=pose(2,.2)
    for i in range(0,60,3):r.alternatives(p,p.box,i,np.array([100,170]),300,False)
    assert r.used==r.budget==10
    assert recovery_score(p)<recovery_score(pose(2,.9))


def test_tuck_with_no_landmarks_reacquires_from_airborne_box():
    r=OrientationRecovery(90)
    turns=r.alternatives(None,np.array([50,30,150,100]),20,np.array([100,170]),300,True)
    assert set(turns)=={1,2,3}
    assert r.airborne
