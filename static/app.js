document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id);
  const role = window.APP_ROLE || "recruiter";

  // Elements
  const pdfInput = $("pdfInput");
  const fileChip = $("fileChip");
  const fileNameEl = $("fileName");
  const removeFileBtn = $("removeFileBtn");
  const resumeHint = $("resumeHint");

  const roomInput = $("roomInput");
  const startCallBtn = $("startCallBtn");
  const endCallBtn = $("endCallBtn");
  const toggleMicBtn = $("toggleMicBtn");

  const localVideo = $("localVideo");
  const remoteVideo = $("remoteVideo");
  const captionsArea = $("captionsArea");

  const shareBox = $("shareBox");
  const shareLink = $("shareLink");
  const copyLinkBtn = $("copyLinkBtn");

  const generatePdfBtn = $("generateReportBtn");

  // Select job role by name (index.html uses name="job_role" without an id)
  const jobRoleInput = document.querySelector('input[name="job_role"]');

  // State
  let room = (roomInput && roomInput.value.trim()) || "default";
  let localStream = null;
  let pc = null;
  let ws = null;
  let recognition = null;
  let captionsRunning = false;

  // Track resume analysis to avoid redundant calls
  let lastAnalyzedKey = null; // `${file.name}|${jobRole}|${room}`
  let analyzing = false;

  // Prefill room from URL (?room=...)
  const urlParams = new URLSearchParams(location.search);
  if (roomInput && !roomInput.value && urlParams.get("room")) {
    roomInput.value = urlParams.get("room");
  }
  room = roomInput ? roomInput.value.trim() || "default" : "default";

  // ---------- Helpers: Resume analysis ----------
  async function maybeAnalyze(force = false) {
    if (!pdfInput || !pdfInput.files || !pdfInput.files[0]) {
      // No PDF yet
      return null;
    }
    const file = pdfInput.files[0];
    const jobRole = (jobRoleInput && jobRoleInput.value.trim()) || "";

    if (!jobRole) {
      // Wait until user specifies job role
      return null;
    }

    const key = `${file.name}|${jobRole}|${room}`;
    if (!force && key === lastAnalyzedKey) {
      return null; // Already analyzed for this combination
    }
    if (analyzing) return null;

    analyzing = true;
    try {
      if (resumeHint) resumeHint.textContent = "Analyzing resume…";
      const formData = new FormData();
      formData.append("file", file);
      formData.append("room", room);
      formData.append("job_role", jobRole);

      const res = await fetch("/api/analyze_resume", { method: "POST", body: formData });
      const data = await res.json();

      if (!res.ok || data.error) {
        if (resumeHint) resumeHint.textContent = `❌ ${data.error || "Failed to analyze resume."}`;
        return null;
      }

      lastAnalyzedKey = key;
      if (resumeHint) resumeHint.textContent = `✅ Analyzed: ${data.file_name} for role “${jobRole}”`;
      return data;
    } catch (err) {
      if (resumeHint) resumeHint.textContent = "❌ Failed to analyze resume.";
      console.error("Analyze error:", err);
      return null;
    } finally {
      analyzing = false;
    }
  }

  function resetAnalysisState() {
    lastAnalyzedKey = null;
  }

  // ---------- Resume upload UX ----------
  if (pdfInput) {
    pdfInput.addEventListener("change", async () => {
      const file = pdfInput.files[0];
      if (!file) {
        fileChip && fileChip.classList.add("hidden");
        if (resumeHint) resumeHint.textContent = "";
        resetAnalysisState();
        return;
      }
      if (fileNameEl) fileNameEl.textContent = file.name;
      fileChip && fileChip.classList.remove("hidden");

      resetAnalysisState();
      // If job role already present, analyze immediately
      await maybeAnalyze(false);
    });
  }

  if (jobRoleInput) {
    // As soon as the user specifies/edits the job role, analyze (if PDF present)
    jobRoleInput.addEventListener("input", async () => {
      resetAnalysisState();
      await maybeAnalyze(false);
    });
  }

  if (removeFileBtn) {
    removeFileBtn.addEventListener("click", () => {
      if (pdfInput) pdfInput.value = "";
      fileChip && fileChip.classList.add("hidden");
      if (resumeHint) resumeHint.textContent = "";
      resetAnalysisState();
    });
  }

  if (roomInput) {
    roomInput.addEventListener("input", async () => {
      room = roomInput.value.trim() || "default";
      // Changing room means a different report bucket; re-analyze for this room if we already have both PDF + job role
      resetAnalysisState();
      await maybeAnalyze(false);
    });
  }

  // ---------- WebSocket helpers ----------
  function sendWS(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    else if (ws) ws.addEventListener("open", () => ws.send(JSON.stringify(msg)), { once: true });
  }

  function connectWebSocket() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/${room}`);
    ws.onmessage = async (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch (_) { msg = null; }
      if (!msg) return;

      if (msg.type === "offer") {
        await ensurePC();
        await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        sendWS({ type: "answer", sdp: pc.localDescription });
        return;
      }
      if (msg.type === "answer") {
        await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
        return;
      }
      if (msg.type === "candidate") {
        try { await pc.addIceCandidate(msg.candidate); } catch {}
        return;
      }
      if (msg.type === "caption") {
        appendCaption(`${msg.sender}: ${msg.text}`);
        return;
      }
    };
  }

  // ---------- ICE servers ----------
  async function getIceServers() {
    const res = await fetch("/api/get_turn_credentials");
    const data = await res.json();
    return { iceServers: data.iceServers };
  }

  async function ensurePC() {
    if (pc) return pc;
    const servers = await getIceServers();
    pc = new RTCPeerConnection(servers);
    if (localStream) localStream.getTracks().forEach(t => pc.addTrack(t, localStream));
    pc.onicecandidate = (e) => { if (e.candidate) sendWS({ type: "candidate", candidate: e.candidate }); };
    pc.ontrack = (e) => { if (remoteVideo) remoteVideo.srcObject = e.streams[0]; };
    return pc;
  }

  // ---------- Call controls ----------
  async function initMedia() {
    localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    if (localVideo) localVideo.srcObject = localStream;
  }

  if (startCallBtn) startCallBtn.addEventListener("click", async () => {
    room = roomInput ? roomInput.value.trim() || "default" : "default";
    await initMedia();
    connectWebSocket();
    await ensurePC();

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    sendWS({ type: "offer", sdp: pc.localDescription });

    if (role !== "candidate" && shareLink && shareBox) {
      const link = `${window.location.origin}/?role=candidate&room=${encodeURIComponent(room)}`;
      shareLink.value = link;
      shareBox.classList.remove("hidden");
    }

    startCaptions();
  });

  if (endCallBtn) endCallBtn.addEventListener("click", () => {
    stopCaptions();
    if (pc) { pc.close(); pc = null; }
    if (ws) { ws.close(); ws = null; }
    if (localStream) localStream.getTracks().forEach(t => t.stop());
  });

  if (toggleMicBtn) toggleMicBtn.addEventListener("click", () => {
    if (!localStream) return;
    const audioTrack = localStream.getAudioTracks()[0];
    if (!audioTrack) return;
    audioTrack.enabled = !audioTrack.enabled;
    toggleMicBtn.textContent = audioTrack.enabled ? "🎤 Mic" : "🔇 Mic Off";
  });

  if (copyLinkBtn) copyLinkBtn.addEventListener("click", async () => {
    if (!shareLink || !shareLink.value) return;
    try {
      await navigator.clipboard.writeText(shareLink.value);
      copyLinkBtn.textContent = "Copied!";
      setTimeout(()=> copyLinkBtn.textContent = "Copy Link", 1200);
    } catch {
      alert("Copy failed, please copy manually.");
    }
  });

  // ---------- Captions ----------
  function setupRecognition() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { console.log("SpeechRecognition not supported"); return null; }
    const recog = new SR();
    recog.interimResults = true;
    recog.continuous = true;
    recog.lang = "en-US";

    recog.onresult = (ev) => {
      let finalChunk = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        const text = res[0].transcript;
        if (res.isFinal) finalChunk += text.trim() + " ";
      }
      if (finalChunk.trim()) {
        const sender = role === "candidate" ? "Candidate" : "Recruiter";
        appendCaption(`${sender}: ${finalChunk.trim()}`);
        sendWS({ type: "caption", text: finalChunk.trim(), sender });
      }
    };
    recog.onend = () => { if (captionsRunning) recog.start(); };
    return recog;
  }

  function startCaptions() {
    if (!recognition) recognition = setupRecognition();
    if (!recognition) return;
    captionsRunning = true;
    recognition.start();
  }

  function stopCaptions() {
    captionsRunning = false;
    if (recognition) recognition.stop();
  }

  function appendCaption(line) {
    if (captionsArea) {
      captionsArea.value += line + "\n";
      captionsArea.scrollTop = captionsArea.scrollHeight;
    }
  }

  // ---------- Generate PDF ----------
  if (generatePdfBtn) {
    generatePdfBtn.addEventListener("click", async () => {
      // Ensure analysis exists for current room + file + job role BEFORE generating
      const file = pdfInput && pdfInput.files && pdfInput.files[0];
      const jobRole = (jobRoleInput && jobRoleInput.value.trim()) || "";
      room = roomInput ? roomInput.value.trim() || "default" : "default";

      if (!file) {
        if (resumeHint) resumeHint.textContent = "Please upload a resume PDF first.";
        return;
      }
      if (!jobRole) {
        if (resumeHint) resumeHint.textContent = "Please specify a job role.";
        if (jobRoleInput) jobRoleInput.focus();
        return;
      }

      // Re-run (or skip if up-to-date) then open PDF
      await maybeAnalyze(false);
      window.open(`/api/generate_pdf?room=${encodeURIComponent(room)}`, "_blank");
    });
  }
}); 