# AI Answer Script Evaluator
Grades handwritten college answer scripts (weekly tests, mids, semester exams) with a vision LLM. A teacher reviews every mark before it is final, and approved marks sync to the college Excel sheet.

## Stack
- Backend: Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL
- Jobs: Redis + arq for background processing
- AI: Groq API (`qwen/qwen3.8-27b` vision model for transcription; grading model chosen in Phase 2)
- Frontend: Next.js + TypeScript + Tailwind
- Excel: openpyxl
- Deploy: Docker Compose

## Hard rules
- No mark is ever written to Excel without teacher approval.
- Every AI output is validated against a Pydantic schema.
- All API calls are async, with concurrency limits, retries and timeouts.
- Student data never goes in logs. Secrets only in .env.
- Every feature gets tests. Run tests before saying a phase is done.

## Workflow
- Before coding a phase, show me a plan and wait for my approval.
- Commit after each working step with a clear message.
- At the end of each phase, update the "Progress" section below.

## Progress
### Phase 1 â€” Ingestion & extraction (branch `phase-1-extraction`)
Code done, 176 tests passing (all mocked, no network). Live run on the sample only partly succeeded (Groq free-tier limits); see below.
- `backend/extract/`: `ingest` (PDF via PyMuPDF / images / image dir), `preprocess` (grayscale, max 1500px, JPEG, blank detection by ink ratio after removing ruling lines), `roll_number` (OpenCV QR on full-res cover, vision fallback, regex-validated), `vision` (AsyncGroq, semaphore, per-call timeout, shared pause on 429, JSON-object output validated by Pydantic; reasoning off), `orientation` (one vision check per script on a 2x2 mosaic of the four rotations), `retry` (exp. backoff + jitter, retry-after), `pipeline` (merges page segments into answers by normalised question number).
- CLI: `python -m backend.extract <pdf|images|dir> [--pretty] [--concurrency N]`; offline tuning: `python -m backend.extract.inspect <script>`.
- Page 1 is treated as the cover (roll number only, never transcribed); `HAS_COVER_PAGE=false` to change.
- Switched from Anthropic to Groq (user decision); Anthropic SDK removed.
- Sample: `samples/script1.pdf` = public CBSE 2023 Class 12 Physics topper sheet (29 pages, git-ignored).
- Live findings (Groq `qwen/qwen3.8-27b`): strict `json_schema` returns empty transcriptions with reasoning off, so `json_object` is the default; the sample is scanned sideways (needs 90° CCW), which motivated auto-rotation; free tier = 8,000 tokens/min (~2-3 pages/min).
- Live runs (2026-10-07): rotation detected correctly (90°); successful pages give sensible keys (34, 35a, 26bi, 23a across pages 23-25, MCQs 1-14). Groq free tier for qwen3.8-27b = 8K tokens/min and 200K tokens/day: ~3K tokens/page means ~60 pages/day; the second run hit the daily cap (11 pages failed). Laptop standby also stalled runs (ConnectError bursts).
- Rate handling: client-side TPM pacing, shared pause on 429/connection errors, separate 429 retry budget, daily-limit detection (remaining pages skipped, reported as failed).
- Open items: decide on Groq paid tier / provider for real volumes; rerun only failed pages; tune `BLANK_INK_RATIO` on booklets that have blank pages; per-page orientation if scripts mix orientations; roll number on the sample is masked (CBSE), so the vision fallback correctly returns null.