(() => {
  const form = document.querySelector("#interview-answer");
  const timer = document.querySelector("#interview-timer");
  if (!form || !timer) return;
  const end = performance.now() + Number(timer.dataset.seconds) * 1000;
  let recorder, stream, chunks = [], previewURL, expired = false, pending = false;
  const controls = document.querySelector("#media-controls");
  const start = document.querySelector("#record-start");
  const stop = document.querySelector("#record-stop");
  const discard = document.querySelector("#record-discard");
  const status = document.querySelector("#recording-status");
  const preview = document.querySelector("#record-preview");
  const input = form.querySelector("[name=recording]");
  const submit = document.querySelector("#answer-submit");
  const mode = form.dataset.mode;
  if (mode === "text") input.hidden = true;
  function release() {
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null;
  }
  function clearPreview() {
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null;
    if (preview) { preview.removeAttribute("src"); preview.hidden = true; }
    input.value = "";
  }
  function tick() {
    const seconds = Math.max(0, Math.ceil((end - performance.now()) / 1000));
    timer.textContent = String(seconds);
    if (!seconds && !expired) {
      expired = true;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      release();
      if (start) start.disabled = true;
      if (status) status.textContent = "Time expired. Continue to the next question.";
    }
  }
  tick();
  const interval = setInterval(tick, 250);
  addEventListener("pagehide", () => {
    clearInterval(interval);
    if (recorder && recorder.state !== "inactive") recorder.stop();
    release();
    if (previewURL) URL.revokeObjectURL(previewURL);
  });
  if (!controls) return;
  if (!navigator.mediaDevices || !window.MediaRecorder || !window.DataTransfer) return;
  controls.hidden = false;
  const types = mode === "video" ? ["video/webm;codecs=vp8,opus", "video/webm"] : ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"];
  const type = types.find(t => MediaRecorder.isTypeSupported(t));
  if (!type) { status.textContent = "Recording unavailable in this browser; use text."; start.disabled = true; return; }
  if (expired) start.disabled = true;
  start.addEventListener("click", async () => {
    if (expired || pending || (recorder && recorder.state !== "inactive")) return;
    pending = true;
    start.disabled = true;
    submit.disabled = true;
    clearPreview();
    chunks = [];
    let bytes = 0;
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: true, video: mode === "video"});
      if (expired) { release(); submit.disabled = false; pending = false; return; }
      recorder = new MediaRecorder(stream, {mimeType: type, audioBitsPerSecond: 48000, videoBitsPerSecond: 256000});
      recorder.ondataavailable = e => {
        if (!e.data.size) return;
        bytes += e.data.size;
        if (bytes <= 8 * 1024 * 1024) chunks.push(e.data);
        else if (recorder.state !== "inactive") recorder.stop();
      };
      recorder.onstop = () => {
        release();
        stop.disabled = true;
        start.disabled = expired;
        submit.disabled = false;
        pending = false;
        if (expired || bytes > 8 * 1024 * 1024 || !chunks.length) {
          status.textContent = expired ? "Time expired; recording not saved." : "Recording too large or empty; use text.";
          chunks = [];
          return;
        }
        const mime = type.split(";")[0];
        const blob = new Blob(chunks, {type: mime});
        const transfer = new DataTransfer();
        transfer.items.add(new File([blob], mime.endsWith("ogg") ? "answer.ogg" : "answer.webm", {type: mime}));
        input.files = transfer.files;
        previewURL = URL.createObjectURL(blob);
        preview.src = previewURL;
        preview.hidden = false;
        discard.disabled = false;
        status.textContent = "Recording ready; review before submitting.";
        chunks = [];
      };
      recorder.onerror = () => { release(); submit.disabled = false; pending = false; start.disabled = expired; status.textContent = "Recording failed; use text."; };
      recorder.start(250);
      stop.disabled = false;
      discard.disabled = true;
      status.textContent = "Recording…";
    } catch {
      release();
      pending = false;
      start.disabled = expired;
      submit.disabled = false;
      status.textContent = "Microphone or camera unavailable; use text.";
    }
  });
  stop.addEventListener("click", () => { if (recorder && recorder.state !== "inactive") recorder.stop(); });
  discard.addEventListener("click", () => { clearPreview(); discard.disabled = true; status.textContent = "Recording discarded."; });
  form.addEventListener("submit", event => {
    if (pending || (recorder && recorder.state !== "inactive")) { event.preventDefault(); status.textContent = "Stop recording before submitting."; }
  });
})();
