from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from twilio.rest import Client

from PyPDF2 import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem

import uvicorn
import os
import uuid
from collections import defaultdict
import json
import re
import tempfile

TWILIO_ACCOUNT_SID = "ACfc5817f4aa305e609fa1d0d124081539"
TWILIO_AUTH_TOKEN = "70237162dae6f8325ca6b369028463c1"
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

app = FastAPI(title="AI Candidate Evaluation System v3")

# Static & templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# In-memory stores
rooms_resume = {}              
rooms_captions = defaultdict(list)  

# ---------------- Helpers ----------------

SKILL_BANK = [
    "python","java","javascript","typescript","react","node.js","node","fastapi","flask","django",
    "sql","postgresql","mysql","mongodb","nosql","rest","api","docker","kubernetes","aws","gcp",
    "azure","machine learning","ml","deep learning","nlp","pandas","numpy","scikit-learn",
    "git","html","css","tailwind","ci/cd","linux","bash","data analysis","power bi","excel"
]

POSITIVE_KEYWORDS = ["led","achieved","improved","optimized","increased","reduced","built","delivered","designed","developed","automated","deployed","launched"]
NEGATIVE_CLUES   = ["gap","unemployed","fired","terminated","probation","disciplinary","incomplete","unfinished"]

def extract_text_from_pdf(uploaded_file) -> str:
    reader = PdfReader(uploaded_file)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
        text += "\n"
    return text.strip()

def extract_candidate_name(text: str) -> str:
    """Heuristic: first non-empty line that looks like a name (no digits, short)."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line.split()) <= 5 and not any(ch.isdigit() for ch in line):
            return line
    return "Unknown Candidate"

def find_relevant_skills(text: str):
    text_low = text.lower()
    found = []
    for s in SKILL_BANK:
        if re.search(r"\b" + re.escape(s) + r"\b", text_low):
            found.append(s)
    return sorted(set(found))

def pick_key_sentences(text: str, max_sentences: int = 10):
    sentences = re.split(r"(?<=[\.\!\?])\s+", text)
    scored = []
    for s in sentences:
        s2 = s.strip()
        if not s2:
            continue
        score = 0
        if re.search(r"\b(\d+(\.\d+)?%?)\b", s2):
            score += 2
        score += sum(1 for k in POSITIVE_KEYWORDS if k in s2.lower())
        scored.append((score, len(s2), s2))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _,__,s in scored[:max_sentences]]

def infer_strengths_and_weaknesses(text: str, job_role: str | None, skills_found):
    strengths = []
    weaknesses = []
    if len(skills_found) >= 6:
        strengths.append("Broad technical skill set")
    if any(k in text.lower() for k in ["project","capstone","intern","internship","contributed","open source"]):
        strengths.append("Hands-on project experience")
    if any(k in text.lower() for k in ["team","collaborat","stakeholder","cross-functional"]):
        strengths.append("Collaboration and communication")
    if any(k in text.lower() for k in POSITIVE_KEYWORDS):
        strengths.append("Outcome-focused with measurable achievements")
    if "lead" in text.lower() or "led" in text.lower():
        strengths.append("Leadership exposure")
    if not re.search(r"\b(20\d{2})\b", text):
        weaknesses.append("Limited evidence of recent work/education timeline")
    if job_role:
        jr = job_role.lower()
        role_map = {
            "data": ["python","pandas","numpy","sql","machine learning","power bi","excel"],
            "ml": ["python","scikit-learn","pandas","numpy","ml","nlp"],
            "frontend": ["javascript","react","html","css","typescript","tailwind"],
            "backend": ["python","fastapi","flask","django","node","sql","rest"],
            "devops": ["docker","kubernetes","aws","ci/cd","linux","bash"]
        }
        expected = []
        for key, skills in role_map.items():
            if key in jr:
                expected = skills
                break
        missing = [s for s in expected if s not in skills_found]
        if missing:
            weaknesses.append("Missing expected role skills: " + ", ".join(missing))
    if any(k in text.lower() for k in NEGATIVE_CLUES):
        weaknesses.append("Potential employment or performance concerns (clarify during interview)")
    return strengths, weaknesses

# ---------------- Routes ----------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, role: str = "recruiter", room: str | None = None):
    return templates.TemplateResponse("index.html", {"request": request, "role": role, "room": room})

@app.post("/api/analyze_resume")
async def analyze_resume(room: str = Form("default"), file: UploadFile | None = None, job_role: str = Form(None)):
    if not file or file.content_type != "application/pdf":
        return JSONResponse({"error": "Please upload a PDF file."}, status_code=400)
    try:
        text = extract_text_from_pdf(file.file)
    except Exception as e:
        return JSONResponse({"error": f"Failed to read PDF: {e}"}, status_code=400)

    candidate_name = extract_candidate_name(text)
    skills = find_relevant_skills(text)
    strengths, weaknesses = infer_strengths_and_weaknesses(text, job_role, skills)
    key_sentences = pick_key_sentences(text, max_sentences=3)   # ✅ limit to 3

    insights = {
        "candidate_name": candidate_name,
        "job_role": job_role,
        "file_name": file.filename,
        "skills": skills,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "key_sentences": key_sentences,
        "raw_text": text
    }
    rooms_resume[room] = insights
    return {"message": "Resume analyzed successfully", "file_name": file.filename, "insights": insights}

@app.get("/api/generate_pdf")
async def generate_pdf(room: str = "default"):
    resume = rooms_resume.get(room, {})
    captions = rooms_captions.get(room, [])

    # Only candidate's lines
    candidate_lines = [line.split(":",1)[1].strip() for line in captions if line.startswith("Candidate:")]
    key_points = pick_key_sentences(" ".join(candidate_lines), max_sentences=10) if candidate_lines else []

    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("Candidate Evaluation Report", styles["Title"]))
    story.append(Paragraph(f"Room: <b>{room}</b>", styles["Normal"]))
    story.append(Spacer(1, 8))

    # Candidate Info
    story.append(Paragraph("Candidate Information", styles["Heading2"]))
    story.append(Paragraph(f"Name: <b>{resume.get('candidate_name','Unknown')}</b>", styles["Normal"]))
    story.append(Paragraph(f"Applied Role: <b>{resume.get('job_role','Not specified')}</b>", styles["Normal"]))
    story.append(Spacer(1, 8))

    # Resume Analysis
    story.append(Paragraph("Resume Analysis", styles["Heading2"]))
    if resume:
        for title, items in [
            ("Key Strengths", resume.get("strengths", [])),
            ("Areas to Probe / Weaknesses", resume.get("weaknesses", [])),
            ("Relevant Skills", resume.get("skills", [])),
            ("Notable Lines from Resume (Top 3)", resume.get("key_sentences", [])),
        ]:
            story.append(Paragraph(title, styles["Heading3"]))
            if items:
                story.append(ListFlowable([ListItem(Paragraph(str(it), styles["Normal"])) for it in items], bulletType='bullet'))
            else:
                story.append(Paragraph("None identified", styles["Normal"]))
            story.append(Spacer(1, 6))
    else:
        story.append(Paragraph("No resume analyzed.", styles["Normal"]))

    # Interview Key Points (Candidate only)
    story.append(Paragraph("Interview Key Points (Candidate)", styles["Heading2"]))
    if key_points:
        story.append(ListFlowable([ListItem(Paragraph(s, styles["Normal"])) for s in key_points], bulletType='bullet'))
    else:
        story.append(Paragraph("No key points said by the candidate in the interview.", styles["Normal"]))

    # ✅ Final Recommendation Section
    story.append(Spacer(1, 8))
    story.append(Paragraph("Final Recommendation", styles["Heading2"]))
    if resume:
        strengths = resume.get("strengths", [])
        weaknesses = resume.get("weaknesses", [])
        skills = resume.get("skills", [])
        job_role = resume.get("job_role")

        strengths_count = len(strengths)
        weaknesses_count = len(weaknesses)

        coverage_ok = True
        if job_role:
            jr = job_role.lower()
            role_map = {
                "data": ["python","pandas","numpy","sql","machine learning","power bi","excel"],
                "ml": ["python","scikit-learn","pandas","numpy","ml","nlp"],
                "frontend": ["javascript","react","html","css","typescript","tailwind"],
                "backend": ["python","fastapi","flask","django","node","sql","rest"],
                "devops": ["docker","kubernetes","aws","ci/cd","linux","bash"]
            }
            expected = []
            for key, sk in role_map.items():
                if key in jr:
                    expected = sk
                    break
            if expected:
                overlap = sum(1 for s in expected if s in skills)
                coverage_ratio = overlap / len(expected)
                coverage_ok = coverage_ratio >= 0.5

        if strengths_count > weaknesses_count and coverage_ok:
            story.append(Paragraph(f"✅ Candidate is recommended to be <b>HIRED</b> for the role of {job_role or 'the applied position'}.", styles["Normal"]))
        else:
            story.append(Paragraph(f"❌ Candidate is <b>NOT RECOMMENDED</b> for hire for the role of {job_role or 'the applied position'}.", styles["Normal"]))
    else:
        story.append(Paragraph("No resume or job role information available to make a recommendation.", styles["Normal"]))

    tmp_path = os.path.join(tempfile.gettempdir(), f"candidate_report_{room}_{uuid.uuid4().hex[:8]}.pdf")
    SimpleDocTemplate(tmp_path, pagesize=A4, title="Candidate Evaluation Report").build(story)
    return FileResponse(tmp_path, filename=f"Candidate_Report_{room}.pdf", media_type="application/pdf")

@app.get("/api/get_turn_credentials")
def get_turn_credentials():
    token = client.tokens.create()
    return {"iceServers": token.ice_servers}

# ---------------- WebSocket Hub ----------------

class RoomHub:
    def __init__(self):
        self.rooms = defaultdict(set)

    async def connect(self, ws: WebSocket, room: str):
        await ws.accept()
        self.rooms[room].add(ws)

    def disconnect(self, ws: WebSocket, room: str):
        self.rooms[room].discard(ws)
        if not self.rooms[room]:
            self.rooms.pop(room, None)

    async def broadcast(self, room: str, message: str, sender: WebSocket | None = None):
        for peer in list(self.rooms.get(room, [])):
            if peer != sender:
                try:
                    await peer.send_text(message)
                except Exception:
                    pass

hub = RoomHub()

@app.websocket("/ws/{room}")
async def websocket_endpoint(websocket: WebSocket, room: str):
    await hub.connect(websocket, room)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                obj = None

            # Capture caption messages
            if obj and obj.get("type") == "caption":
                text = str(obj.get("text", "")).strip()
                sender_label = obj.get("sender", "Unknown")
                if text:
                    line = f"{sender_label}: {text}"
                    rooms_captions[room].append(line)
                    await hub.broadcast(room, json.dumps({"type":"caption","text":text,"sender":sender_label}), sender=websocket)
                continue

            # Otherwise relay signaling
            await hub.broadcast(room, data, sender=websocket)
    except WebSocketDisconnect:
        hub.disconnect(websocket, room)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)