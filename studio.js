/* ============================================================
   Studio — frontend orchestration (Gradio click delegation)

   Approach: dynamic HTML uses data-* attributes. A single delegated
   click listener on .gradio-container catches them, writes a JSON
   payload into the hidden #studio-action-payload textbox (triggers
   Gradio's input event so Python sees the new value), then clicks
   the hidden #studio-action-trigger button to fire the Python
   handler. The handler reads the payload, dispatches by action
   type, and returns updates for both views.
   ============================================================ */
(function () {
  "use strict";

  const LOCAL_STORAGE_NAME_KEY = "studio.collab_name";

  // ----- helpers -----
  function setHiddenTextbox(elemId, value) {
    const root = document.getElementById(elemId);
    if (!root) {
      console.warn("[studio.js] missing element:", elemId);
      return false;
    }
    const input = root.querySelector("input, textarea");
    if (!input) return false;
    // Pick the native value setter that matches THIS element's prototype.
    // Calling the HTMLInputElement setter on a <textarea> (or vice versa)
    // throws "Illegal invocation".
    const proto = input.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const nativeSetter = Object.getOwnPropertyDescriptor(proto, "value").set;
    nativeSetter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }

  function clickHiddenButton(elemId) {
    const root = document.getElementById(elemId);
    if (!root) {
      console.warn("[studio.js] missing element:", elemId);
      return;
    }
    const btn = root.querySelector("button");
    if (btn) btn.click();
  }

  // Wait for a Gradio-rendered element to appear. Gradio hydrates components
  // asynchronously after DOMContentLoaded, so studio.js can be ready before the
  // orchestration components exist.
  function waitFor(elemId, timeoutMs = 8000) {
    return new Promise((resolve) => {
      const found = document.getElementById(elemId);
      if (found) return resolve(found);
      const start = Date.now();
      const obs = new MutationObserver(() => {
        const el = document.getElementById(elemId);
        if (el) {
          obs.disconnect();
          resolve(el);
        } else if (Date.now() - start > timeoutMs) {
          obs.disconnect();
          resolve(null);
        }
      });
      obs.observe(document.body, { childList: true, subtree: true });
    });
  }

  function dispatchAction(action, data) {
    // Include a nonce so consecutive identical actions still trigger
    // gr.Textbox.change (which only fires on a real value change).
    const payload = JSON.stringify({
      action,
      data: data || {},
      nonce: Date.now() + ":" + Math.random(),
    });
    setHiddenTextbox("studio-action-payload", payload);
  }

  // ----- loading overlay -----
  // Gradio nuốt ~300-800ms để đọc wav + render recording HTML. Trong khi đó
  // picker_view đã ẩn nên user thấy trắng. Phủ overlay tức thì cho phản hồi
  // ngay; MutationObserver tự gỡ khi recording HTML đã vào DOM.
  function showStudioLoadingOverlay(message) {
    let el = document.getElementById("studio-loading-overlay");
    if (el) return;
    el = document.createElement("div");
    el.id = "studio-loading-overlay";
    el.className = "studio-loading-overlay";
    el.innerHTML =
      "<div class='studio-loading-card'>" +
        "<div class='studio-loading-spinner'></div>" +
        "<div class='studio-loading-label'>" +
          (message || "Đang mở hội thoại…") +
        "</div>" +
      "</div>";
    document.body.appendChild(el);

    // Wait until the recording shell is BOTH in DOM AND visible. Gradio
    // applies `gr.update(visible=True)` on the Column wrapper and updates the
    // inner HTML in the same response, but the DOM mutations don't apply
    // atomically — innerHTML can fire MutationObserver before the column's
    // `display:none` is cleared. The <img onerror> rail-scroll trigger fires
    // either way (browser parses <img> in hidden DOM), so we can't rely on
    // "shell exists" alone — must also check offsetParent.
    function isVisibleNow() {
      const shell = document.querySelector(".studio-rec-shell");
      return !!(shell && shell.offsetParent !== null);
    }
    const obs = new MutationObserver(() => {
      if (isVisibleNow()) {
        hideStudioLoadingOverlay();
        obs.disconnect();
      }
    });
    obs.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["style", "class"] });

    // Belt-and-suspenders: poll every 80ms in case Gradio toggles visibility
    // via a CSS class change on an ancestor that the observer didn't catch.
    const pollId = setInterval(() => {
      if (isVisibleNow()) {
        hideStudioLoadingOverlay();
        clearInterval(pollId);
        obs.disconnect();
      }
    }, 80);

    // Hard timeout — tránh kẹt nếu có lỗi server không kích hoạt re-render.
    setTimeout(() => {
      hideStudioLoadingOverlay();
      obs.disconnect();
      clearInterval(pollId);
    }, 10000);
  }

  function hideStudioLoadingOverlay() {
    const el = document.getElementById("studio-loading-overlay");
    if (el) el.remove();
  }

  // ----- click delegation -----
  function installClickDelegate() {
    document.addEventListener("click", (e) => {
      const card = e.target.closest("[data-card-dialog]");
      if (card) {
        // Switch view ngay tức thì — không đợi server. Overlay phủ trong khi
        // recording_html còn rỗng để user không thấy trắng.
        setStudioView("recording");
        showStudioLoadingOverlay("Đang mở hội thoại…");
        dispatchAction("open_conversation", { dialog: card.dataset.cardDialog });
        return;
      }

      const filter = e.target.closest("[data-filter]");
      if (filter) {
        dispatchAction("set_filter", { filter: filter.dataset.filter });
        return;
      }

      const resume = e.target.closest("[data-resume-cta]");
      if (resume) {
        setStudioView("recording");
        showStudioLoadingOverlay("Đang mở hội thoại tiếp theo…");
        dispatchAction("resume_next", {});
        return;
      }

      const back = e.target.closest("[data-back-to-picker]");
      if (back) {
        setStudioView("picker");
        dispatchAction("back_to_picker", {});
        return;
      }

      const finishBtn = e.target.closest("[data-finish]");
      if (finishBtn) {
        setStudioView("picker");
        dispatchAction("finish", {});
        return;
      }

      const playUser = e.target.closest("[data-play-user]");
      if (playUser) {
        dispatchAction("play_user_audio", { idx: parseInt(playUser.dataset.playUser, 10) });
        return;
      }

      const playAssistant = e.target.closest("[data-play-assistant]");
      if (playAssistant) {
        dispatchAction("play_assistant_audio", { idx: parseInt(playAssistant.dataset.playAssistant, 10) });
        return;
      }

      const playAll = e.target.closest("[data-play-all]");
      if (playAll) {
        if (window.__studioPlaylistActive) {
          // Already playing → toggle off.
          window.studioStopPlaylist && window.studioStopPlaylist();
        } else {
          dispatchAction("play_all", {});
        }
        return;
      }

      const jump = e.target.closest("[data-jump-to]");
      if (jump) {
        const v = jump.getAttribute("data-jump-to");
        if (v) dispatchAction("jump_to", { idx: parseInt(v, 10) });
        return;
      }

      const rerec = e.target.closest("[data-rerec]");
      if (rerec) {
        // Rail buttons carry the target idx (data-rerec='3'); the hero's
        // preview-phase button has no value, meaning "the take I just made".
        const v = rerec.getAttribute("data-rerec");
        dispatchAction(
          "rerecord_last",
          v ? { idx: parseInt(v, 10) } : {}
        );
        return;
      }

      const save = e.target.closest("[data-save-next]");
      if (save) {
        dispatchAction("save_and_next", {});
        return;
      }

      const skip = e.target.closest("[data-skip-user]");
      if (skip) {
        dispatchAction("skip_user", {});
        return;
      }

      const recBtn = e.target.closest("[data-rec-start]");
      if (recBtn) {
        // Gradio's mic component uses .record-button for the start button
        // and .stop-button for the stop button. Match by class first; fall
        // back to aria-label="Start recording" if class names change.
        const mic = document.querySelector(
          "#mic-audio .record-button, " +
          "#mic-audio button[aria-label='Start recording']"
        );
        if (mic) {
          mic.click();
        } else {
          console.warn("[studio.js] record button not found in #mic-audio");
        }
        return;
      }

      const stopBtn = e.target.closest("[data-rec-stop]");
      if (stopBtn) {
        const stop = document.querySelector(
          "#mic-audio .stop-button, " +
          "#mic-audio button[aria-label='Stop recording']"
        );
        if (stop) {
          stop.click();
        } else {
          console.warn("[studio.js] stop button not found in #mic-audio");
        }
        return;
      }
    });
  }

  // ----- keyboard shortcuts -----
  function installKeyboardShortcuts() {
    // Phím tắt CHỈ chạy khi đang ở recording view. Trên picker, Enter có thể
    // race với click vừa rồi (server chưa kịp đổi view → kbd_enter dispatch
    // với view cũ = "picker" làm UI nhảy về home).
    function isRecordingViewActive() {
      const rec = document.querySelector(".studio-recording");
      if (!rec) return false;
      // Gradio ẩn Column bằng display:none trên wrapper.
      return rec.offsetParent !== null;
    }

    document.addEventListener("keydown", (e) => {
      // Don't fire when typing in an input
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
        return;
      }
      if (!isRecordingViewActive()) return;
      switch (e.key) {
        case " ":
          e.preventDefault();
          // Context-sensitive: stop > start > play. Click whichever is visible.
          const stopBtn = document.querySelector("[data-rec-stop]");
          const recBtn = document.querySelector("[data-rec-start]");
          const playUser = document.querySelector(".hero-audio-bar.user .play-circle");
          if (stopBtn) { stopBtn.click(); }
          else if (recBtn) { recBtn.click(); }
          else if (playUser) { playUser.click(); }
          // else: nothing visible to act on — swallow Space silently
          break;
        case "Enter":
          e.preventDefault();
          dispatchAction("kbd_enter", {});
          break;
        case "r": case "R":
          dispatchAction("kbd_rerec", {});
          break;
        case "ArrowRight":
          dispatchAction("kbd_skip", {});
          break;
        case "Escape":
          setStudioView("picker");
          dispatchAction("kbd_back", {});
          break;
      }
    });
  }

  // ----- localStorage name ------
  function persistName(name) {
    try { localStorage.setItem(LOCAL_STORAGE_NAME_KEY, name); } catch (_) {}
  }

  // Modal that blocks interaction until the user types a name. Used on first
  // access when localStorage is empty; resolves with the entered (trimmed)
  // name so callers can persist + dispatch it.
  function promptForNameModal() {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "studio-modal-overlay";
      overlay.innerHTML = (
        "<div class='studio-modal'>"
          + "<div class='studio-modal-emoji'>👋</div>"
          + "<h2>Chào bạn!</h2>"
          + "<p>Để bắt đầu thu âm, vui lòng cho tôi biết tên của bạn. "
          + "Tên này sẽ được dùng để đặt tên thư mục lưu các bản ghi.</p>"
          + "<input type='text' placeholder='Ví dụ: Nguyễn Văn A' "
          + "autocomplete='off' spellcheck='false' />"
          + "<button class='studio-modal-go'>Bắt đầu →</button>"
        + "</div>"
      );
      document.body.appendChild(overlay);

      const input = overlay.querySelector("input");
      const btn = overlay.querySelector("button");
      const submit = () => {
        const name = input.value.trim();
        if (!name) {
          input.classList.add("studio-modal-error");
          input.focus();
          return;
        }
        overlay.remove();
        resolve(name);
      };
      btn.addEventListener("click", submit);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") submit();
        if (e.target.classList.contains("studio-modal-error")) {
          e.target.classList.remove("studio-modal-error");
        }
      });
      // Defer focus until after the browser has rendered.
      setTimeout(() => input.focus(), 50);
    });
  }

  async function loadStoredName() {
    let name = null;
    try { name = localStorage.getItem(LOCAL_STORAGE_NAME_KEY); }
    catch (e) { console.warn("[studio.js] localStorage blocked:", e); }
    // Wait until the orchestration components are in the DOM. Gradio hydrates
    // them asynchronously after DOMContentLoaded.
    const ready = await waitFor("studio-action-payload");
    if (!ready) {
      console.warn("[studio.js] payload never appeared; skipping set_name");
      return;
    }
    if (!name) {
      // First-time access — block on the modal until the user types a name.
      try {
        name = await promptForNameModal();
        persistName(name);
      } catch (_) {
        return;
      }
    }
    setHiddenTextbox("studio-stored-name", name);
    dispatchAction("set_name", { name });
  }

  window.__studioAutoNext = function () {
    setTimeout(() => dispatchAction("save_and_next", {}), 600);
  };

  // Exposed for inline onclick on the name pill — open the same modal used
  // on first access so users get a consistent rename experience.
  window.studioPromptForName = async function () {
    const name = await promptForNameModal();
    persistName(name);
    dispatchAction("set_name", { name });
  };

  // navigator.mediaDevices is only defined in a secure context: HTTPS or
  // localhost/127.0.0.1. When users open the app at http://<LAN-IP>:7860 or
  // http://0.0.0.0:7860 the API is undefined and recording fails silently
  // with a console error. Show a banner so the cause is obvious.
  function warnIfInsecureContext() {
    if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === "function") {
      return;
    }
    const banner = document.createElement("div");
    banner.style.cssText = (
      "position:fixed;top:0;left:0;right:0;z-index:99999;" +
      "background:#fef3c7;color:#92400e;padding:14px 20px;" +
      "font:600 14px system-ui,sans-serif;text-align:center;" +
      "border-bottom:2px solid #f59e0b;"
    );
    banner.innerHTML = (
      "⚠️ Trình duyệt chặn mic vì URL này không phải HTTPS hoặc localhost. " +
      "Hãy mở bằng <code>http://localhost:7860</code>, " +
      "<code>http://127.0.0.1:7860</code>, hoặc link <code>gradio.live</code>."
    );
    document.body.appendChild(banner);
    document.body.style.paddingTop = "60px";
  }

  // ============================================================
  // Whole-conversation playback. Python ships a base64 JSON payload
  // [{idx, url}, ...] via an <img onerror> trigger that calls
  // window.studioStartPlaylist. We play each one in sequence, highlighting
  // the matching rail row, with a floating cancel button.
  // ============================================================
  let _playlist = null;
  let _playlistAudio = null;
  let _playlistPos = 0;
  // Track the playlist payload currently playing so duplicate triggers from
  // rapid server re-renders (we've observed 4 renders per single click) are
  // no-ops instead of restarting the same playlist mid-flight.
  let _activePlaylistKey = null;

  function _setPlaylistButtonLabel(active) {
    const btn = document.querySelector("[data-play-all]");
    if (btn) btn.textContent = active ? "⏸ Dừng nghe" : "▶ Nghe toàn bộ";
  }

  function _highlightRailRow(idx) {
    document.querySelectorAll(".studio-rec-rail .playing-now")
      .forEach(el => el.classList.remove("playing-now"));
    if (idx == null) return;
    const row = document.querySelector(
      `.studio-rec-rail [data-row-idx='${idx}']`
    );
    if (row) {
      row.classList.add("playing-now");
      row.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  function _playNext() {
    if (!_playlist || _playlistPos >= _playlist.length) {
      window.studioStopPlaylist();
      return;
    }
    const item = _playlist[_playlistPos++];
    _highlightRailRow(item.idx);
    // Don't pause the previous audio — pausing while play() is still
    // pending throws AbortError, which we then can't reliably distinguish
    // from a real failure. Just orphan it (null its event handlers + drop
    // the reference) and let the GC reclaim it. The user can't hear it
    // anyway once we move on.
    if (_playlistAudio) {
      _playlistAudio.onended = null;
      _playlistAudio.onerror = null;
      _playlistAudio.onabort = null;
      _playlistAudio = null;
    }
    const audio = new Audio(item.url);
    _playlistAudio = audio;
    audio.addEventListener("ended", () => {
      if (_playlistAudio === audio) _playNext();
    }, { once: true });
    audio.addEventListener("error", () => {
      if (_playlistAudio === audio) _playNext();
    }, { once: true });
    // Watchdog: if 1.5s after play() the element is still untouched
    // (paused at 0s with no loaded data), treat as broken and skip.
    setTimeout(() => {
      if (_playlistAudio === audio
          && audio.paused
          && audio.currentTime === 0
          && audio.readyState < 2) {
        console.warn("[studio.js] playlist audio never started, skipping idx", item.idx);
        _playNext();
      }
    }, 1500);
    audio.play().catch(() => {
      // Swallow. 'ended', 'error', or the watchdog above will drive
      // progression — using play().catch chains caused tight reentry
      // loops when AbortError didn't match err.name in some browsers.
    });
  }

  window.studioStartPlaylist = function (b64) {
    // Dedupe duplicate triggers (the same b64 payload firing N times from
    // rapid server re-renders): if we're already playing this exact
    // playlist, stay put.
    if (_activePlaylistKey === b64) return;
    try {
      const json = atob(b64);
      const items = JSON.parse(json);
      if (!Array.isArray(items) || !items.length) return;
      // Different payload (or fresh start) — replace any in-flight playlist.
      window.studioStopPlaylist();
      _activePlaylistKey = b64;
      _playlist = items;
      _playlistPos = 0;
      window.__studioPlaylistActive = true;
      _setPlaylistButtonLabel(true);
      _playNext();
    } catch (e) {
      console.warn("[studio.js] studioStartPlaylist failed:", e);
    }
  };

  window.studioStopPlaylist = function () {
    if (_playlistAudio) {
      try { _playlistAudio.pause(); } catch (_) {}
      _playlistAudio.onended = null;
      _playlistAudio.onerror = null;
      _playlistAudio = null;
    }
    _playlist = null;
    _playlistPos = 0;
    _activePlaylistKey = null;
    window.__studioPlaylistActive = false;
    _setPlaylistButtonLabel(false);
    _highlightRailRow(null);
  };

  // ============================================================
  // Live recording UI: timer + waveform via WebAudio AnalyserNode.
  // The hero's HTML ships static "0:00" + bars; we drive them client-side
  // while a recording or playback is active.
  // ============================================================
  const LiveUI = (() => {
    let audioCtx = null;
    let analyser = null;
    let stream = null;
    let mediaSource = null;
    let rafId = null;
    let startedAt = 0;
    let mode = null;  // "record" | "playback" | null

    function _ensureCtx() {
      if (!audioCtx) {
        const Ctor = window.AudioContext || window.webkitAudioContext;
        audioCtx = new Ctor();
      }
      // Some browsers suspend new contexts until a user gesture; resume.
      if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
      return audioCtx;
    }

    function _renderFrame() {
      if (!analyser) return;
      const bars = document.querySelectorAll(".hero-waveform .bar");
      if (bars.length) {
        const bins = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(bins);
        // Map the first N (skip a couple DC bins) to bar heights 6..50px.
        bars.forEach((bar, i) => {
          const v = bins[i + 2] || 0;
          const h = 6 + (v / 255) * 44;
          bar.style.height = h + "px";
        });
      }
      const t = document.querySelector(".hero-timer");
      if (t && startedAt) {
        const elapsed = Math.floor((Date.now() - startedAt) / 1000);
        const mm = Math.floor(elapsed / 60);
        const ss = elapsed % 60;
        t.textContent = mm + ":" + (ss < 10 ? "0" + ss : ss);
      }
      rafId = requestAnimationFrame(_renderFrame);
    }

    async function startRecording() {
      if (mode === "record") return;
      stopAll();
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
      try {
        // Re-use the user's mic. Browsers allow multiple consumers, so
        // Gradio's recorder continues working in parallel.
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (e) {
        console.warn("[studio.js] LiveUI mic failed:", e);
        return;
      }
      const ctx = _ensureCtx();
      const src = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.7;
      src.connect(analyser);
      startedAt = Date.now();
      mode = "record";
      _renderFrame();
    }

    function startPlayback(audioEl) {
      if (!audioEl) return;
      stopAll();
      const ctx = _ensureCtx();
      try {
        mediaSource = ctx.createMediaElementSource(audioEl);
      } catch (e) {
        // createMediaElementSource throws if the element was already wired
        // to a context — just bail rather than crashing.
        return;
      }
      analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.6;
      mediaSource.connect(analyser);
      mediaSource.connect(ctx.destination);  // keep audible
      startedAt = Date.now();
      mode = "playback";
      _renderFrame();
      audioEl.addEventListener("ended", stopAll, { once: true });
    }

    function stopAll() {
      if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
      if (stream) {
        stream.getTracks().forEach(t => t.stop());
        stream = null;
      }
      analyser = null;
      mediaSource = null;
      startedAt = 0;
      mode = null;
      // Don't close audioCtx — keep it for next playback (createMediaElementSource
      // can only be called once per element per context).
      // Reset bars to a tiny resting height so it's not visually misleading.
      document.querySelectorAll(".hero-waveform .bar").forEach(b => {
        b.style.height = "6px";
      });
    }

    return { startRecording, startPlayback, stopAll, getMode: () => mode };
  })();

  // Watch for hero state changes and drive LiveUI accordingly.
  // The hero is re-rendered by Python whenever rec_phase / current_turn
  // changes; a MutationObserver on the hero container fires every time.
  function installLiveUIWatcher() {
    const handle = () => {
      const recording = document.querySelector("[data-rec-stop]");
      const currentMode = LiveUI.getMode();
      if (recording) {
        if (currentMode !== "record") LiveUI.startRecording();
        return;
      }
      // Look for an autoplaying audio element we just injected (Khách
      // auto-play or replay tag).
      const autoplay = document.querySelector(
        ".studio-rec-hero audio[autoplay], "
        + ".studio-rec-hero ~ audio[autoplay], "
        + "audio[id^='studio-play-']"
      );
      if (autoplay && !autoplay.paused) {
        if (currentMode !== "playback") LiveUI.startPlayback(autoplay);
        return;
      }
      if (currentMode) LiveUI.stopAll();
    };
    // Initial pass + observe future DOM mutations.
    handle();
    const obs = new MutationObserver(() => handle());
    obs.observe(document.body, { childList: true, subtree: true });
    // Also poll cheaply for cases where an audio element changes state
    // (paused / ended) without DOM mutation.
    setInterval(handle, 400);
  }

  // Watchdog: if localStorage has a name but the picker still shows
  // "Đặt tên", Python's collab_state never received the set_name dispatch
  // (race condition: dispatch can fire before Gradio's session is fully
  // wired up on page load). Poll every second and re-dispatch until the UI
  // reflects the stored name. Backs off after a few attempts.
  function installNameWatchdog() {
    let attempts = 0;
    const interval = setInterval(() => {
      let stored;
      try { stored = localStorage.getItem(LOCAL_STORAGE_NAME_KEY); }
      catch (_) { clearInterval(interval); return; }
      if (!stored) {
        // No name to enforce; if user clears it, watchdog has nothing to do.
        return;
      }
      const pill = document.querySelector(".studio-name-pill");
      if (!pill) return;  // picker not rendered yet
      const txt = pill.textContent || "";
      if (txt.includes(stored)) {
        // Synced — done.
        clearInterval(interval);
        return;
      }
      attempts += 1;
      if (attempts > 6) {
        // Give up after ~6s to avoid a runaway loop if something else is wrong.
        clearInterval(interval);
        return;
      }
      console.log("[studio.js] name desync, re-dispatching set_name:", stored);
      dispatchAction("set_name", { name: stored });
    }, 1000);
  }

  // ----- view toggle (CSS-driven via body[data-studio-view]) -----
  // Gradio 6.14's gr.update(visible=...) on Column doesn't apply reliably in
  // the same response as a child HTML value update — gây cảnh "trắng màn,
  // phải bấm Enter để hiện". Workaround: cả 2 Column luôn visible=True,
  // hiển thị thực sự do CSS rule trên body[data-studio-view] quyết định.
  function setStudioView(v) {
    if (v !== "picker" && v !== "recording") return;
    document.body.dataset.studioView = v;
  }

  function installViewMarkerObserver() {
    // Server gửi <span data-studio-view-marker='picker|recording'> mỗi lần
    // re-render → đọc và sync sang body.
    function syncFromMarker() {
      const m = document.querySelector("[data-studio-view-marker]");
      if (!m) return;
      const v = m.getAttribute("data-studio-view-marker");
      if (v === "picker" || v === "recording") setStudioView(v);
    }
    syncFromMarker();
    const obs = new MutationObserver(syncFromMarker);
    obs.observe(document.body, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ["data-studio-view-marker"],
    });
  }

  // ----- boot -----
  function boot() {
    // Default view = picker (matches initial server marker). Set IMMEDIATELY
    // so CSS rules have something to act on before first server roundtrip.
    if (!document.body.dataset.studioView) setStudioView("picker");
    installViewMarkerObserver();
    installClickDelegate();
    installKeyboardShortcuts();
    warnIfInsecureContext();
    installLiveUIWatcher();
    // loadStoredName waits internally for the Gradio components to appear,
    // so we don't block boot here.
    loadStoredName();
    installNameWatchdog();
    console.log("[studio.js] ready");
  }

  if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(boot, 0);
  } else {
    document.addEventListener("DOMContentLoaded", boot);
  }
})();
