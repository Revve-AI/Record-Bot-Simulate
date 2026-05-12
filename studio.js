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

  // ----- click delegation -----
  function installClickDelegate() {
    document.addEventListener("click", (e) => {
      const card = e.target.closest("[data-card-dialog]");
      if (card) {
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
        dispatchAction("resume_next", {});
        return;
      }

      const back = e.target.closest("[data-back-to-picker]");
      if (back) {
        dispatchAction("back_to_picker", {});
        return;
      }

      const finishBtn = e.target.closest("[data-finish]");
      if (finishBtn) {
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

      const rerec = e.target.closest("[data-rerec]");
      if (rerec) {
        dispatchAction("rerecord_last", {});
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
        // Click Gradio's hidden Audio record button
        const mic = document.querySelector("#mic-audio button[aria-label*='record' i], #mic-audio button[title*='record' i], #mic-audio button.record-button");
        if (mic) mic.click();
        return;
      }

      const stopBtn = e.target.closest("[data-rec-stop]");
      if (stopBtn) {
        const stop = document.querySelector("#mic-audio button[aria-label*='stop' i], #mic-audio button.stop-button");
        if (stop) stop.click();
        return;
      }
    });
  }

  // ----- keyboard shortcuts -----
  function installKeyboardShortcuts() {
    document.addEventListener("keydown", (e) => {
      // Don't fire when typing in an input
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
        return;
      }
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
          dispatchAction("kbd_enter", {});
          break;
        case "r": case "R":
          dispatchAction("kbd_rerec", {});
          break;
        case "ArrowRight":
          dispatchAction("kbd_skip", {});
          break;
        case "Escape":
          dispatchAction("kbd_back", {});
          break;
      }
    });
  }

  // ----- localStorage name ------
  async function loadStoredName() {
    let name = null;
    try { name = localStorage.getItem(LOCAL_STORAGE_NAME_KEY); }
    catch (e) { console.warn("[studio.js] localStorage blocked:", e); return; }
    if (!name) return;
    // Wait until the orchestration components are in the DOM. Gradio hydrates
    // them asynchronously after DOMContentLoaded.
    const ready = await waitFor("studio-action-payload");
    if (!ready) {
      console.warn("[studio.js] payload never appeared; skipping set_name");
      return;
    }
    setHiddenTextbox("studio-stored-name", name);
    dispatchAction("set_name", { name });
  }

  function persistName(name) {
    try { localStorage.setItem(LOCAL_STORAGE_NAME_KEY, name); } catch (_) {}
  }

  window.__studioAutoNext = function () {
    setTimeout(() => dispatchAction("save_and_next", {}), 600);
  };

  // Expose for inline onclick on the name pill
  window.studioPromptForName = function () {
    const current = localStorage.getItem(LOCAL_STORAGE_NAME_KEY) || "";
    const name = window.prompt("Tên của bạn:", current);
    if (name == null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    persistName(trimmed);
    dispatchAction("set_name", { name: trimmed });
  };

  // ----- boot -----
  function boot() {
    installClickDelegate();
    installKeyboardShortcuts();
    // loadStoredName waits internally for the Gradio components to appear,
    // so we don't block boot here.
    loadStoredName();
    console.log("[studio.js] ready");
  }

  if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(boot, 0);
  } else {
    document.addEventListener("DOMContentLoaded", boot);
  }
})();
