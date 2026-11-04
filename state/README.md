# Growth Engine state branch

This branch is not source code. It holds the deduplication state that a
GitHub Actions runner cannot keep on its own: a runner is temporary, so
`database/growth.db` is gone the moment a run ends, and a fresh database
would report every collected item as new on every run.

`state/research_keys.ndjson` is written by:

    python agents/controller/main.py state-export --path <file>

and read back at the start of a run by:

    python agents/controller/main.py state-import --path <file> --allow-missing

One record per line, sorted by canonical URL so the diff stays small and
readable. Article bodies are deliberately absent — they are only needed
inside the run that collected them, and they are ~85% of the bytes.

Only the research collection workflow writes here. Never merge this
branch into `main`.
