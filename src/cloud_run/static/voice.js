// Live voice calls: mic -> /ws/voice -> Gemini Live or the OpenAI Realtime API -> speaker, with a live transcript.
"use strict";

(function () {
  const A = window.companyAssistant;
  const $ = (id) => document.getElementById(id);
  const OUT_RATE = 24000;

  let call = null;

  class VoiceCall {
    constructor(partner) {
      this.partner = partner;                   // assistant and chatgpt hear you directly; claude is relayed
      this.label = A.partnerInfo(partner).label;
      this.muted = false;
      this.ready = false;
      this.playHead = 0;
      this.sources = new Set();
      this.live = { you: "", assistant: "" };   // current turn's running transcript
      this.lines = [];                          // finished lines shown this call
    }

    async start() {
      if (window.companySpeaker) window.companySpeaker.stop();  // a call and a read-out would talk over each other
      $("voicebar").hidden = false;
      $("startVoice").disabled = true;
      // Each step says what it's waiting for, so a stall is never just "Connecting…".
      setState("請允許使用咪高峰：留意網址列旁邊的提示");
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      setState("正在啟動咪高峰…");
      this.inCtx = new AudioContext();
      await this.inCtx.audioWorklet.addModule("/static/pcm-capture.js");
      this.node = new AudioWorkletNode(this.inCtx, "pcm-capture", { processorOptions: { targetRate: 16000 } });
      this.inCtx.createMediaStreamSource(this.stream).connect(this.node);
      this.node.port.onmessage = (e) => this.onMic(e.data);
      if (this.inCtx.state === "suspended") await this.inCtx.resume();
      this.outCtx = new AudioContext({ sampleRate: OUT_RATE });

      setState("正在開啟語音線路…");
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      this.ws = new WebSocket(`${scheme}://${location.host}/ws/voice?partner=${encodeURIComponent(this.partner)}`);
      this.ws.binaryType = "arraybuffer";
      this.ws.onopen = () => {
        setState("正在接通…");  // the server names the model it's reaching next
        this.watchdog = setTimeout(() => {
          if (!this.ready) this.fail("語音服務在 25 秒內沒有回應，請稍後再試。");
        }, 25000);
      };
      this.ws.onmessage = (e) => (typeof e.data === "string" ? this.onJson(JSON.parse(e.data)) : this.play(e.data));
      this.ws.onclose = (e) => {
        if (!this.ready && !this.ended) this.fail(e.code === 1006 ? "語音線路在準備好之前中斷了。" : `語音線路已關閉（${e.code}）。`);
        else this.end(false);
      };
      this.ws.onerror = () => { if (!this.ready) this.fail("無法開啟語音線路。"); };
    }

    fail(message) {
      A.toast(message);
      this.end(false);
    }

    onMic(buffer) {
      const pcm = new Int16Array(buffer);
      let peak = 0;
      for (let i = 0; i < pcm.length; i += 8) peak = Math.max(peak, Math.abs(pcm[i]));
      $("vmeter").style.width = `${Math.min(100, Math.round((peak / 12000) * 100))}%`;
      if (this.ready && !this.muted && this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(buffer);
    }

    onJson(msg) {
      if (msg.type === "ready") {  // names the model on the line, so it's plain who's answering
        this.ready = true; clearTimeout(this.watchdog);
        setState(msg.model ? `正在聆聽（${msg.model}），請說話` : "正在聆聽，請說話");
      }
      else if (msg.type === "status") setState(msg.text);
      else if (msg.type === "transcript") {
        this.live[msg.who] += msg.text;
        if (msg.who === "assistant") setState("正在說話…");
        this.renderLive();
      } else if (msg.type === "tool") setState(msg.name === `ask_${this.partner}` ? `${this.label} is thinking…` : `Working: ${msg.name.replace(/_/g, " ")}…`);
      else if (msg.type === "interrupted") { this.stopPlayback(); setState("Listening…"); }
      else if (msg.type === "turn_complete") {
        for (const who of ["you", "assistant"]) {
          if (this.live[who].trim()) this.lines.push({ role: who === "you" ? "user" : "assistant", text: this.live[who].trim(), at: new Date().toISOString(), voice: true });
          this.live[who] = "";
        }
        this.renderLive();
        setState("Listening…");
        A.refreshKnowledge();
      } else if (msg.type === "error") {
        if (!this.ready) this.fail(msg.message);  // couldn't start: say why and close the bar
        else { A.toast(msg.message); setState(msg.message); }
      }
    }

    renderLive() {
      const extra = this.lines.map((t) => A.turnNode(t));
      if (this.live.you.trim()) extra.push(A.turnNode({ role: "user", text: this.live.you.trim() }, true));
      if (this.live.assistant.trim()) extra.push(A.turnNode({ role: "assistant", text: this.live.assistant.trim() }, true));
      A.renderThread(extra);
    }

    play(buffer) {
      const pcm = new Int16Array(buffer);
      if (!pcm.length) return;
      const audio = this.outCtx.createBuffer(1, pcm.length, OUT_RATE);
      const data = audio.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) data[i] = pcm[i] / 0x8000;
      const src = this.outCtx.createBufferSource();
      src.buffer = audio;
      src.connect(this.outCtx.destination);
      const at = Math.max(this.outCtx.currentTime + 0.02, this.playHead);  // queue back-to-back, no gaps
      src.start(at);
      this.playHead = at + audio.duration;
      this.sources.add(src);
      src.onended = () => this.sources.delete(src);
    }

    stopPlayback() {
      for (const s of this.sources) { try { s.stop(); } catch { /* already stopped */ } }
      this.sources.clear();
      this.playHead = 0;
    }

    toggleMute() {
      this.muted = !this.muted;
      $("vmute").textContent = this.muted ? "取消靜音" : "靜音";
      setState(this.muted ? "已靜音" : "正在聆聽…");
    }

    end(sendStop = true) {
      if (this.ended) return;
      this.ended = true;
      clearTimeout(this.watchdog);
      try { if (sendStop && this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "stop" })); } catch { /* closing */ }
      try { this.ws && this.ws.close(); } catch { /* closing */ }
      this.stopPlayback();
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      try { this.inCtx && this.inCtx.close(); } catch { /* closed */ }
      try { this.outCtx && this.outCtx.close(); } catch { /* closed */ }
      $("voicebar").hidden = true;
      setState("連接中…");  // what the next call starts from
      $("startVoice").disabled = false;
      $("vmute").textContent = "靜音";
      call = null;
      setTimeout(() => A.loadTalk(), 800);  // the server saved the finished turns into the conversation
    }
  }

  function setState(text) { $("vstate").textContent = text; }

  $("startVoice").onclick = async () => {
    if (call) return;
    if (!navigator.mediaDevices || !window.AudioWorkletNode) { A.toast("這個瀏覽器不支援即時語音，請改用 Chrome 或 Edge。"); return; }
    call = new VoiceCall(A.partner);
    try { await call.start(); }
    catch (e) {
      const why = {
        NotAllowedError: "咪高峰被封鎖了。請在網站設定（網址列的鎖頭圖示）中允許，然後再試。",
        NotFoundError: "找不到咪高峰。請接上一個，或在系統設定中選擇。",
        NotReadableError: "咪高峰正被另一個應用程式使用。請先關閉它，然後再試。",
      }[e.name] || e.message;
      A.toast(why);
      call && call.end(false);
    }
  };
  $("vend").onclick = () => call && call.end();
  A.endVoice = () => call && call.end();  // switching partners hangs up: a call belongs to one partner
  $("vmute").onclick = () => call && call.toggleMute();
  window.addEventListener("beforeunload", () => call && call.end());
})();
