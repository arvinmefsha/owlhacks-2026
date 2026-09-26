-- Core tables (plain Postgres). Every statement is idempotent; it runs at API startup.
-- Timescale features (hypertables, continuous aggregate, compression) live in timescale.sql.

CREATE TABLE IF NOT EXISTS divers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    height_cm REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    diver_id UUID NOT NULL REFERENCES divers (id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    readiness_hr REAL,
    readiness_br REAL,
    readiness_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS sessions_diver_idx ON sessions (diver_id, started_at DESC);

CREATE TABLE IF NOT EXISTS dives (
    id UUID PRIMARY KEY,
    session_id UUID REFERENCES sessions (id) ON DELETE SET NULL,
    diver_id UUID NOT NULL REFERENCES divers (id) ON DELETE CASCADE,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    setup JSONB NOT NULL,
    calibration JSONB NOT NULL,
    source TEXT NOT NULL,
    video_path TEXT,
    video_mime TEXT,
    video_width INT NOT NULL,
    video_height INT NOT NULL,
    fps REAL,
    overall_score REAL,
    scores JSONB,
    analysis JSONB,
    feedback JSONB,
    feedback_source TEXT,
    vision_review JSONB
);

CREATE INDEX IF NOT EXISTS dives_diver_idx ON dives (diver_id, recorded_at DESC);

-- One row per video frame: 33 landmarks x (x, y, z, visibility) flattened, NULL when no pose was found.
CREATE TABLE IF NOT EXISTS pose_frames (
    dive_id UUID NOT NULL REFERENCES dives (id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    frame_idx INT NOT NULL,
    t REAL NOT NULL,
    landmarks REAL[]
);

CREATE INDEX IF NOT EXISTS pose_frames_dive_idx ON pose_frames (dive_id, frame_idx);

-- One row per scored metric per dive (plus the phase and overall scores), for progress charts.
CREATE TABLE IF NOT EXISTS dive_metrics (
    dive_id UUID NOT NULL REFERENCES dives (id) ON DELETE CASCADE,
    diver_id UUID NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    phase TEXT NOT NULL,
    metric TEXT NOT NULL,
    value DOUBLE PRECISION,
    score DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS dive_metrics_diver_idx ON dive_metrics (diver_id, metric, recorded_at DESC);

CREATE TABLE IF NOT EXISTS workouts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    targets TEXT[] NOT NULL,
    description TEXT NOT NULL,
    sets INT,
    reps TEXT,
    equipment TEXT
);
