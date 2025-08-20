
# AI Candidate Evaluation System — v2
- Filename chip with remove for PDF upload
- Room ID field inside Live Interview card
- Captions auto-start/stop with call
- Generate **PDF** report (resume insights + interview key points)
- Candidate mode via `?role=candidate` (hides resume & report sections)

Run:
```
pip install -r requirements.txt
uvicorn main:app --reload
```
Open http://127.0.0.1:8000 (recruiter)
Open http://127.0.0.1:8000/?role=candidate (candidate)
