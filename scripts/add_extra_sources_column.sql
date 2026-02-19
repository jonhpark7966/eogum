-- Add extra_sources column for multicam support
ALTER TABLE projects ADD COLUMN extra_sources jsonb DEFAULT '[]'::jsonb;
-- Format: [{"r2_key": "sources/xxx.mp4", "filename": "cam2.mp4", "size_bytes": 524288000}]
