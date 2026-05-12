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
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, "value"
    )?.set || Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, "value"
    ).set;
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

  function dispatchAction(action, data) {
    const payload = JSON.stringify({ action, data: data || {} });
    if (!setHiddenTextbox("studio-action-payload", payload)) return;
    // Tiny defer so Gradio's input listener registers the new value
    setTimeout(() => clickHiddenButton("studio-action-trigger"), 30);
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
          dispatchAction("kbd_space", {});
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
  function loadStoredName() {
    try {
      const name = localStorage.getItem(LOCAL_STORAGE_NAME_KEY);
      if (name) {
        setHiddenTextbox("studio-stored-name", name);
        // Fire the load-name action so Python knows we have a name
        setTimeout(() => {
          dispatchAction("set_name", { name });
        }, 80);
      }
    } catch (e) {
      console.warn("[studio.js] localStorage blocked:", e);
    }
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
    loadStoredName();
    console.log("[studio.js] ready");
  }

  if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(boot, 0);
  } else {
    document.addEventListener("DOMContentLoaded", boot);
  }
})();
