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
### Phase 1 — Ingestion & extraction (branch `phase-1-extraction`)
Done, 130 tests passing (all mocked, no network). Live run on a real scan still pending.
- `backend/extract/`: `ingest` (PDF via PyMuPDF / images / image dir), `preprocess` (grayscale, max 1500px, JPEG, blank detection by ink ratio after removing ruling lines), `roll_number` (OpenCV QR on full-res cover, vision fallback, regex-validated), `vision` (AsyncGroq, semaphore, per-call timeout, strict JSON-schema output validated by Pydantic; reasoning off), `retry` (exp. backoff + jitter, retry-after), `pipeline` (merges page segments into answers by normalised question number).
- CLI: `python -m backend.extract <pdf|images|dir> [--pretty] [--concurrency N]`; offline tuning: `python -m backend.extract.inspect <script>`.
- Page 1 is treated as the cover (roll number only, never transcribed); `HAS_COVER_PAGE=false` to change.
- Switched from Anthropic to Groq (user decision); Anthropic SDK removed.
- Sample: `samples/script1.pdf` = public CBSE 2023 Class 12 Physics topper sheet (29 pages, git-ignored).
- Open items: confirm strict `json_schema` works with images on Groq (else `VISION_RESPONSE_FORMAT=json_object`); tune `BLANK_INK_RATIO` on booklets that have blank pages.