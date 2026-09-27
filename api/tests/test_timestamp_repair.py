import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import pytest
import repair_timestamps as repair
from db.local import LocalDatabase
from diving_tracker.timing import VideoTimeline
from tests.synthetic import DiveSpec, generate


def test_ambiguous_frame_mapping_is_refused():
    with pytest.raises(ValueError, match='count/order'):
        repair.validate_mapping([0,2], [0,.1], np.array([0,.1]), 30)
    with pytest.raises(ValueError, match='Unrecognized'):
        repair.validate_mapping([0,1], [0,.6], np.array([0,.1]), 30)


def test_repair_backs_up_is_idempotent_and_preserves_landmarks(tmp_path, monkeypatch):
    db = LocalDatabase(tmp_path/'db.sqlite3');db.init_schema([])
    diver=db.get_or_create_default_diver(); id=uuid4()
    frames, truth=generate(DiveSpec())
    count=len(frames)
    old=[i/30 for i in range(count)]
    times=np.array(old)*1.1
    for f,t in zip(frames,old):f['t']=t
    payload=dict(id=id, diver_id=diver['id'], recorded_at=datetime.now(UTC),
      video_path='video.mov',video_width=1280,video_height=720,fps=30.,
      setup={'position':'tuck','direction':'forward','somersaults':1.5,'apparatus':'springboard','board_height_m':1},
      calibration={'water_y':truth['water_y']},analysis={'model':{'schema':'coco17-v1'}})
    db.insert_dive(payload,frames,[])
    (tmp_path/'video.mov').write_bytes(b'test-video')
    monkeypatch.setattr(repair,'read_timeline',lambda path:VideoTimeline(times,0.))
    with sqlite3.connect(db.path) as c: before=c.execute('select landmarks from pose_frames order by frame_idx').fetchall()
    assert repair.repair(db.path,tmp_path)[0]['status']=='repairable'
    result=repair.repair(db.path,tmp_path,apply=True)
    assert result[-1]['updated']==1
    assert len(list(tmp_path.glob('*.before-timing-repair-*.sqlite3')))==1
    with sqlite3.connect(db.path) as c:
        assert c.execute('select landmarks from pose_frames order by frame_idx').fetchall()==before
        np.testing.assert_allclose([r[0] for r in c.execute('select t from pose_frames order by frame_idx')],times)
    assert repair.repair(db.path,tmp_path,apply=True)[0]['status']=='already-aligned'
