# AI Answer Script Evaluator
Grades handwritten college answer scripts (weekly tests, mids, semester exams) with a vision LLM. A teacher reviews every mark before it is final, and approved marks sync to the college Excel sheet.

## Stack
- Backend: Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL
- Jobs: Redis + arq for background processing
- AI / OCR: Azure Document Intelligence `prebuilt-read` for transcription (default); Groq `qwen/qwen3.8-27b` vision model selectable with `TRANSCRIBE_PROVIDER=groq`; grading model chosen in Phase 2
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
Code done, 240 tests passing (all mocked, no network). Live: 29-page sample transcribed with Azure, 28/28 answer pages OK, ~4.5-5 min on the free F0 tier.
- `backend/extract/`: `ingest` (PDF via PyMuPDF / images / image dir), `preprocess` (grayscale; blank detection at 1500px by ink ratio after removing ruling lines; JPEG sent at 2400px for OCR, 1500px for Groq), `roll_number` (OpenCV QR on full-res cover, then cover text: OCR "Roll No" field or vision model), `azure_ocr` (async Azure SDK, SDK retries off; our retry/backoff/timeout/semaphore; shared pause on 429/connection errors; quota detection; OcrPage Pydantic validation), `layout` (rows rebuilt in the page's reading frame, rule-based question labels with state carried across pages, diagram/equation-fragment and unclear-word flags), `vision` + `orientation` (Groq path: JSON-object output, 2x2 rotation mosaic check, TPM pacing), `clients` (provider switch), `pipeline` (merge into answers by normalised question number; `review_notes` when a label reappears after other answers).
- CLI: `python -m backend.extract <pdf|images|dir> [--pretty] [--concurrency N]`; offline tuning: `python -m backend.extract.inspect <script>`; engine comparison: `python -m experiments.ocr_compare`.
- Page 1 is the cover (roll number only, never transcribed); `HAS_COVER_PAGE=false` to change.
- Provider history: Anthropic -> Groq -> Azure (user decisions). Groq free tier (8K tokens/min, 200K/day ≈ 60 pages/day) was too slow; laptop standby also stalled runs.
- Sample: `samples/script1.pdf` = public CBSE 2023 Class 12 Physics topper sheet (29 pages, scanned sideways, roll number masked; git-ignored).
- OCR comparison on 3 pages vs hand reference: Groq vision 98% word recall, keeps equations, but duplicated a page and invented an equation line; Azure 92% words, verbatim prose, equations/fractions/diagrams garbled; EasyOCR 9% (unusable).
- Known Azure limits: equations garbled (teacher must check maths/physics); diagrams only flagged; rule-based labels can misfire (numbered points or misreads like "i)"->"1)" become keys; MCQ answers "13. (d)" become key "13d" until Phase 2 maps keys to the question paper); F0 = 500 pages/month, 1 request/second.
- Open items: rerun only failed pages; S0 tier + higher AZURE_MAX_CONCURRENCY for real volumes; tune `BLANK_INK_RATIO` on booklets with blank pages; optional hybrid (OCR text checked by an LLM) for equation-heavy subjects.

### Phase 2 — Grading against an Excel marking scheme (branch `phase-2-grading`)
Done, 311 tests passing (all mocked). Live: 5-question reference scheme on the sample graded in ~1 min, ~8K Groq tokens.
- `backend/llm/groq_chat.py`: shared Groq JSON client (concurrency, token pacing, shared pauses, daily limit, retries, Pydantic validation) used by transcription and grading.
- `backend/grading/`: `scheme` (Excel import, columns by header name, row-level errors; OR groups), `template` (`python -m backend.grading.template scheme.xlsx`), `mapping` (answer keys -> scheme rows: exact, MCQ "13d"/"(c)", sub-parts combined, unlabelled sub-parts from the parent, OR alternatives), `grader` (MCQs by rule; others by Groq `openai/gpt-oss-120b`, strict JSON, reasoning medium, temperature 0.2; marks range-checked, retried once, never clamped; answer fenced against prompt injection; OR group counts the better alternative), `__main__` CLI (`python -m backend.grading --scheme s.xlsx --script result.json | scan`, `-o` for UTF-8 file output).
- Output is always status "proposed"; nothing is written to the college Excel (later phase, after teacher approval).
- needs_review: low confidence, OCR problem, diagram/equation fragments, unclear words, carried review notes, or any deduction made with less than high confidence (live run: OCR read "i"/"j" as "c"/";" and the model deducted 2 marks on 34(a) without flagging it).
- Live findings: reference scheme (`samples/reference_scheme.xlsx`, written for testing, not CBSE's official scheme) gave 8.5-9/11 across two runs; marks vary between runs on OCR-damaged answers (Q19 2 -> 1.5), so flagged deductions matter. Review flags are frequent because most answers carry unclear-word/equation notes.
- Free tier: gpt-oss-120b 8K tokens/min, 200K/day; ~1.5K tokens per written question.
- Open items: official marking scheme for a real exam to measure agreement with teacher marks; reduce noisy review flags.

### Phase 3 — Teacher workflow backend (branch `phase-3-teacher-workflow`)
Done, 380 tests passing (all mocked). Live: real Azure + Groq, full flow on the 29-page sample in ~5.6 min (admin upload → AI grading 334 s → teacher password change, review, approve, submit → Excel row `CBSE23T01 | 1 | 2 | 3 | 3 | 1 | 10`).
- Flow (user decisions): admin (exam cell) creates exam from the scheme Excel, bundles, uploads one PDF per student, assigns bundles by employee ID; AI grades in the background; teacher logs in (employee ID + password), reviews, approves scripts, submits bundles; when every bundle of the exam is submitted, per-question marks + total by roll number go to Excel.
- `backend/app/`: `models` (users, sessions, exams, bundles, scripts, question_marks, audit_log; Alembic `alembic/versions/0001`), `auth` (argon2, hashed session tokens, lockout, generic login errors), `deps` (Annotated deps, password-change gate), `main` (`create_app` with injectable AI factories/job handler; CSRF header guard on writes; requeues unfinished scripts on start), `routes/auth|admin|teacher`, `status` (bundle/exam status derived from scripts), `marks` (OR groups, totals, approval rules), `excel_export` + `exporting` (guards: all bundles submitted, all scripts approved, roll numbers present and unique; atomic save; optional `EXCEL_MASTER_PATH` replaces only that exam's sheet), `storage` (paths by DB id, uploads type/size checked, threaded writes), `manage` (`init-db`, `create-admin`).
- `worker/`: `runner.InlineRunner` (one job at a time, in-process; closes AI clients on stop), `jobs.process_script` (extract → upright page images → grade → question_marks; failures stored as short codes `unreadable_pdf` / `api_key` / `quota` / `error:<Type>`; quota never leaves a half-graded script).
- Privacy: library loggers capped above DEBUG (`backend/logging_setup.py`) — at DEBUG the SQLite driver logged SQL values and the AI SDKs logged request bodies with student data. `data/` and `*.db` git-ignored.
- Run: `python -m backend.app.manage create-admin <ID>` then `uvicorn backend.app.main:app` (API docs at /docs).
- Deviations from CLAUDE.md stack until Phase 5: SQLite + in-process runner (Docker/PostgreSQL/Redis not installed on this laptop).
- Open items: Phase 4 web app (Next.js); Phase 5 Docker Compose with PostgreSQL + Redis/arq; per-question leftovers can be long when the scheme covers few questions.
