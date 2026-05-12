# Recording UI Redesign — Design Spec

**Date:** 2026-05-12
**Author:** Hieu + Claude
**Status:** Draft — awaiting user review

## 1. Goal

Replace the current Gradio recording UI with a focused, two-screen experience that:
- Makes the recording task unambiguous to a non-technical recorder
- Treats the conversation queue as a separate page, not a sidebar
- Surfaces current-conversation progress prominently
- Makes every recorded turn easy to replay
- Visually separates "audio that auto-played for context" from "audio you recorded"
- Applies a coherent, professional design system (Linear-style structure, warm orange palette)

The backend audio/IO logic (dialog parsing, VAD segmentation, file outputs, normalization) is **not** changing. This is a UI replacement plus one small backend addition for partial-progress tracking.

## 2. Non-goals

- No changes to the `.dialog` / `.wav` input format
- No changes to the output directory layout (`output/<collab>/<dialog_stem>/`)
- No changes to silero-vad usage or sample-rate handling
- No multi-recorder coordination (each recorder still has their own output folder)

## 3. Information architecture

Two screens, with a hard separation:

```
   ┌─────────────────┐         pick a conversation         ┌─────────────────┐
   │   PICKER PAGE   │  ───────────────────────────────▶   │ RECORDING PAGE  │
   │   (queue list)  │                                     │ (focused task)  │
   │                 │  ◀─────────────────────────────────── │                 │
   └─────────────────┘         "← Hội thoại khác"          └─────────────────┘
                            (or after finishing one)
```

- **Picker page** is the landing screen. Recorders see it once per session start and any time they tap "← Hội thoại khác" on the recording page, or after finishing a conversation.
- **Recording page** is full-screen, single-purpose. Once entered, the only ways out are: switch conversation (back to picker), finish current conversation, or close the tab.

## 4. Design system

### Tokens

| Token | Value | Use |
|---|---|---|
| `--bg` | `#faf8f3` | App background |
| `--bg-2` | `#f3efe5` | Recessed surfaces, palette block |
| `--surface` | `#ffffff` | Cards, top bar, progress bar background |
| `--surface-2` | `#f3efe5` | Chip backgrounds, inactive buttons |
| `--border` | `#e7e0cf` | All borders, dividers |
| `--border-strong` | `#d4cab4` | Hover/focus borders |
| `--text` | `#1f1d18` | Primary text, dark CTA surface |
| `--text-2` | `#5a574a` | Secondary text |
| `--text-3` | `#8f8a7a` | Tertiary text, meta |
| `--brand` | `#e2731f` | **Primary actions, progress bar, record button** |
| `--brand-hover` | `#c25f15` | Brand hover state |
| `--brand-deep` | `#7a3d0c` | Text on brand-soft backgrounds |
| `--brand-soft` | `#fbeedc` | Brand-tinted backgrounds (role tags, soft pills) |
| `--success` | `#5a8c5a` | Used sparingly (e.g. "Đã xong" badge on picker) |

**Color discipline:** the brand color (`#e2731f`) is the only chromatic accent in the UI. It drives:
- All primary buttons (Bắt đầu, save & next)
- The progress bar fill
- The record button and its pulsing recording state
- Active states (selected filter chip's underline, focused inputs)

Secondary actions use neutral surfaces (`--surface-2` background, `--text-2` text). No green-for-success, no blue-for-info — only the brand color + neutral grays + occasional `--success` for the "done" badge.

### Typography

- **System:** Inter (`Inter, ui-sans-serif, system-ui, -apple-system`)
- **Wordmark only:** serif fallback (`Tiempos, Charter, Georgia, serif`) on "Studio" logo
- **Sizes:** 11px (meta), 12px (body small), 13px (body), 14px (UI), 18–24px (hero turn text)
- **Numerals:** `font-feature-settings: "tnum"` for progress counters

### Spacing & radius

- 4px grid: 4, 8, 10, 12, 14, 18, 22, 24, 32, 48
- Radius: `4px` (chips, kbd), `6–8px` (small buttons), `10–12px` (cards), `14px` (frames), `99px` (pills, record button)

### Shadows

- Flat by default. Cards: `0 1px 0 rgba(0,0,0,.02)` (hairline).
- Hero record button only: `0 8px 24px -6px rgba(226,115,31,.45)` (subtle brand glow).
- No drop shadows on inputs or rails.

### Keyboard

- `Space` — start / stop recording (assistant turn); pause/resume playback (Khách turn)
- `Enter` — Save & next (in preview state)
- `R` — Re-record (in preview state)
- `→` — Skip current Khách turn
- `Esc` — Back to picker page (with confirmation if mid-recording)

## 5. Picker page

### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│ [● Studio]                          [Hôm nay 2/8] [Tổng 14/60] [Hieu ▾]
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ [▶]  Thu tiếp câu kế tiếp chưa hoàn thành                │   │
│  │      Hội thoại #4 · 12 câu · dừng ở câu 7    [Bắt đầu →] │   │  ← hero CTA
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Hoặc chọn hội thoại bất kỳ          [Chưa thu 46] [Đã xong 14] │  ← filters
│  Đã xong 14 · Chưa thu 46 · Tổng 60      [Tất cả 60]            │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ Hội thoại #4 │  │ Hội thoại #5 │  │ Hội thoại #6 │  …       │  ← grid
│  │ [CHƯA THU]   │  │ [CHƯA THU]   │  │ [CHƯA THU]   │           │
│  │ 12 câu · 5p  │  │  9 câu · 4p  │  │ 14 câu · 6p  │           │
│  │ 6 câu thu    │  │ 4 câu thu    │  │ 7 câu thu    │           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Components

**Top bar (sticky, 56px):**
- Wordmark `● Studio` (brand dot + serif "Studio")
- Right cluster: today's count, total count, name pill (click to rename)
- Name persists in `localStorage` under key `studio.collab_name`

**Hero "thu tiếp" CTA (dark surface + brand button):**
- Dark `--text` background (#1f1d18), inverted text
- Brand-color icon tile + "Bắt đầu →" button
- Subtitle shows next unfinished conversation: `Hội thoại #N · M câu · dừng ở câu K`
- Hidden if no partial-progress data exists yet (first session) — replaced by "Bắt đầu hội thoại đầu tiên" pointing at the first card

**Filter chips:**
- Default selection: "Chưa thu" (matches today's "hide done" toggle in current app)
- Active chip: `--text` background, light text
- Inactive: `--surface` background, `--text-2` text, `--border` border

**Conversation card:**
- Title: `Hội thoại #N` (1-indexed by sort order)
- Badge: `CHƯA THU` (brand-soft) or `ĐÃ XONG` (`--success`-tinted, dimmed card)
- Date in `DD/MM` format
- Meta line: `M câu · ~K phút · X câu thu` (X câu thu requires backend partial-progress; defaults to "0 câu thu" if file absent)
- Hover: brand-color border, slight translateY
- Click anywhere on card → enters recording page

### State

- **Empty state** (no `.dialog` files): centered illustration + "Không tìm thấy hội thoại nào ở `input/`. Bấm 🔄 để quét lại."
- **All done state**: filter "Chưa thu" empty → "🎉 Bạn đã thu xong tất cả các hội thoại. Cảm ơn!"

## 6. Recording page

### Layout

Split: **left rail 340px · right hero (fills remaining)**. Top bar (40px) + progress strip (44px) span both columns. Total: full viewport height, no scroll on the page itself — only the left rail scrolls.

```
┌──────────────────────────────────────────────────────────────────┐
│ [● Studio] [← Hội thoại] Hội thoại #3 · 11/01     [2/8] [Hieu]   │  top bar
├──────────────────────────────────────────────────────────────────┤
│  Câu 6 / 12     ████████░░░░░░░░░░░░░░░░░░░░░░░ 50%              │  progress
├────────────────┬─────────────────────────────────────────────────┤
│ [3] Bạn đã thu │                                                 │
│                │              ┌─────────────────┐                │
│ ┌────────────┐ │              │   ROLE TAG      │                │
│ │🧑 Khách    │ │              └─────────────────┘                │
│ │ Alo.   [▶] │ │              ┌─────────────────────────┐        │
│ └────────────┘ │              │                         │        │
│                │              │   Current turn text     │        │
│ ┌────────────┐ │              │                         │        │
│ │● ĐÃ THU    │ │              └─────────────────────────┘        │
│ │ Dạ em nghe │ │                                                 │
│ │ ▶ Phát ↻   │ │                       ●                         │  hero
│ └────────────┘ │                       record                    │
│                │                                                 │
│ ┌────────────┐ │              Bấm để ghi · Space                 │
│ │🧑 Khách    │ │                                                 │
│ │ Cho anh… ▶ │ │                                                 │
│ └────────────┘ │                                                 │
│                │                                                 │
│ ┌────────────┐ │                                                 │
│ │● ĐÃ THU    │ │                                                 │
│ │ ... ▶ ↻    │ │                                                 │
│ └────────────┘ │                                                 │
│                │                                                 │
│ ┌────────────┐ │                                                 │
│ │🧑 Khách ⏵  │ │  ← currently auto-playing (brand-tinted)        │
│ │ Thứ bảy… ⏸ │ │                                                 │
│ └────────────┘ │                                                 │
└────────────────┴─────────────────────────────────────────────────┘
```

### Top bar

- `← Hội thoại` button (secondary): returns to picker. If mid-recording, shows confirm dialog.
- Conversation title + date (centered-left)
- Right cluster: today's count, name (same component as picker)

### Progress strip

- Label: `Câu N / Total`
- Bar: 4px tall, `--bg-2` track, `--brand` fill
- Right: percent
- Sticky under top bar

### Left rail (history)

**Header:** `[count-pill] Bạn đã thu` — count is total assistant turns recorded so far in this conversation. Brand-color count pill on `--bg`.

**Two row types, interleaved chronologically:**

1. **Khách context row** (any user turn already past):
   - Surface card, neutral border, no left accent strip
   - `Khách` chip (neutral `--surface-2`), `#N` index, italic-ish text in `--text-2`
   - Always-visible 26px circular play button (neutral surface, brand-color icon, brand background on hover)
   - Tapping plays the cached user audio in-place (no auto-advance — pure replay)

2. **Recorded assistant row** (any assistant turn the recorder has saved):
   - Surface card with a 3px-wide brand-color stripe on the left edge
   - Top: `ĐÃ THU` brand-color tiny label + `Câu N · 0:03` meta
   - Middle: turn text
   - Actions: `▶ Phát lại` (dark text-surface button, primary-styled within the row), `↻ Thu lại` (neutral)
   - `Thu lại` deletes the saved audio for that turn, sets current turn back to N, and shows the recording UI again

**Currently-playing state** (a Khách row is the active turn the app is auto-playing):
- Brand-soft background, brand-colored border, soft glow
- Play button switches to `⏸` and is filled brand-color
- Stays in the rail (not the hero — the hero only shows assistant turns, see below)

### Right hero (action area)

The hero has **4 distinct states** based on which turn is current:

#### State 1 — Khách turn (auto-playing)

The current turn is a Khách turn. Audio auto-plays from the segmented user audio (existing backend).

- Role tag: `Đang nghe khách` (brand-soft, brand-deep text)
- Turn card: same text content, neutral border
- Below: audio scrubber (28px tall, brand-color play/pause + scrubber line + time)
- Hint: `⏳ Tự sang câu kế khi nghe xong · → để bỏ qua`
- Optional skip button: `Bỏ qua câu này →` (tertiary, text-only)
- When audio ends, system waits `USER_PAUSE_SEC` (0.6s, existing constant) then advances

#### State 2 — Assistant turn idle (ready to record)

The current turn is an assistant turn, mic is open but not recording.

- Role tag: `Đến lượt bạn` (brand-soft)
- Turn card: brand-color border (subtle), text shown larger
- Big 80px brand-color circular record button (filled, white dot inside, brand-glow shadow)
- Hint: `**Bấm để ghi âm** · hoặc nhấn Space`

#### State 3 — Recording in progress

After tapping record (or Space).

- Role tag: `● ĐANG GHI ÂM` (brand-color background, white text — the live-recording state is the strongest visual moment in the UI)
- Turn card: brand-color border, subtle pulse
- Live timer (mm:ss, monospace, brand color)
- Live waveform (15 brand-color bars, animated from mic input level)
- Stop button: same shape and color as record, now pulses with brand-color rings (`box-shadow` keyframe), inner shape switches from circle to rounded square
- Hint: `Bấm để **kết thúc** · hoặc Space · tự dừng khi im lặng 1.5s` (existing SILENCE_MS behavior)

#### State 4 — Preview (just finished recording)

After silero-vad auto-stop or manual stop.

- Role tag: `✅ Đã thu xong — nghe lại`
- Turn card: unchanged
- Audio playback bar (full width inside hero, brand-tinted)
- Two buttons side-by-side:
  - `↻ Thu lại` — secondary (neutral surface)
  - `💾 Lưu & câu kế →` — primary (brand, prominent)
- Hint: `Enter để lưu · R để thu lại`

#### Completion state (when current_turn >= total)

Replaces hero contents:
- Big centered card: "🎉 Tuyệt vời! Bạn đã ghi xong **N câu** của hội thoại này."
- Primary button: `📦 Hoàn tất & về danh sách` — runs existing `action_finish` (writes `dialog.json`, `dialog_normalized.dialog`, user audio) then routes to picker

## 7. State transitions

```
                              ┌──────────────┐
   open picker  ───────────▶  │  PICKER PAGE │
                              └──────┬───────┘
                                     │  pick conversation
                                     ▼
                              ┌──────────────┐
                              │   current    │
                              │   turn = 0   │
                              └──────┬───────┘
                                     │
        ┌───────── if user ──────────┴──────── if assistant ──────┐
        ▼                                                          ▼
   STATE 1 (auto-play)                              STATE 2 (idle, ready)
        │                                                          │
        │ audio ends (+ 0.6s pause)                     tap / Space│
        │ OR → key                                                 │
        ▼                                                          ▼
   advance current_turn ────────────────────────────▶ STATE 3 (recording)
                                                                   │
                                                       stop / Space / 1.5s silence
                                                                   ▼
                                                          STATE 4 (preview)
                                                                   │
                                                ┌──────────────────┼──────────────┐
                                                ▼                                  ▼
                                              ↻ R                             💾 Enter
                                              back to STATE 2              save + advance
                                                                                   │
                                                                                   ▼
                                                              if more turns: back to top
                                                              if done: COMPLETION
```

## 8. Backend impact

What stays identical:
- `parse_dialog_file` — unchanged
- `segment_user_turns` (and `trim_silences`) — unchanged
- `action_recording_done` — unchanged (still writes `turn_NN_assistant.wav`)
- `action_finish` — unchanged (still writes `dialog.json`, `dialog_normalized.dialog`, user audio)
- `is_dialog_done` — unchanged (checks for `dialog.json`)
- Output directory layout — unchanged

What changes:

1. **New: partial-progress file** — to support the picker's "thu tiếp" CTA and per-card "X câu thu" counter. After each successful `action_recording_done` (or `action_save_continue`), write a tiny `progress.json` in the conversation's output dir:
   ```json
   { "last_recorded_turn": 6, "recorded_count": 3, "updated_at": "2026-05-12T03:32:11" }
   ```
   Cleaned up automatically on `action_finish` (since the conversation is done). Adds one file write per turn save. **~10 lines of backend code.**

2. **New API surface** — the UI moves from server-rendered Gradio components to a small JSON API so the SPA can drive both pages. Endpoints (FastAPI):
   - `GET /api/conversations?collab=<name>` — list `.dialog` files with done/partial status
   - `GET /api/conversations/<name>?collab=<name>` — load dialog turns + segmented user audio URLs
   - `POST /api/conversations/<name>/turns/<idx>/audio` — upload assistant recording (multipart)
   - `POST /api/conversations/<name>/finish` — write final outputs
   - `GET /audio/user/<name>/<idx>` — stream trimmed user-turn audio
   - `GET /audio/assistant/<collab>/<dialog>/<idx>` — stream saved assistant audio
   These wrap the existing Python functions — no new audio logic.

3. **Remove:** all Gradio UI code in `app.py` (everything below the `_render` / `action_*` functions). The audio/segmentation helpers move to a new module `recording_backend.py`. The FastAPI app + new SPA frontend replace the Gradio layer.

## 9. Implementation approach

**Stay on Gradio.** No stack changes, no architectural changes — same dependencies (`gradio`, `silero-vad`, `torch`, `numpy`, `soundfile`), same Dockerfile, same `python app.py` entrypoint, same port 7860, same docker-compose volumes.

Concrete shape:

- **Single `gr.Blocks`** page. Two views (picker, recording) modeled as two top-level `gr.Column` blocks with `visible=` toggled from a `gr.State` (`view: "picker" | "recording"`). No `gr.Tabs` — the user picks via a card click, not a tab click.
- **All visuals via `gr.HTML()`** with a single CSS block at the top of the file declaring the design tokens (`--brand`, `--bg`, etc.) as CSS variables on `.gradio-container`. Existing inline `<style>` block in `app.py` gets replaced wholesale.
- **`gr.Audio(sources=["microphone"], type="filepath")`** stays exactly as today — same `action_recording_done` handler reads the file and calls existing save logic. This component is the one place we can't fully restyle (Gradio internal), so we wrap it in a styled `gr.Column` and hide its default chrome with targeted CSS overrides where possible.
- **Click handlers on dynamic HTML** (conversation cards on the picker, play buttons on rail items, etc.) use the existing pattern: render the HTML with `data-*` attributes, expose hidden `gr.Button`s named `_card_click`, `_play_user_audio`, etc., and use a `js=` snippet on `gr.Blocks.load` to delegate clicks to those hidden buttons via a JS event listener attached to `.gradio-container`. This is how `action_load`/`action_save_continue` etc. already get wired.
- **Keyboard shortcuts** via a `gr.Blocks.load(js=...)` snippet that adds `keydown` listeners and dispatches clicks on hidden `gr.Button`s (`_kbd_space`, `_kbd_enter`, `_kbd_r`, `_kbd_skip`, `_kbd_back`).
- **`localStorage` for collaborator name** via a `js=` snippet on `gr.Blocks.load` that reads `studio.collab_name` and dispatches an input event into the (hidden) `gr.Textbox` already used for the name. Writing happens via the same channel when the user edits.
- **`progress.json`** is written synchronously inside `action_save_continue` (and `action_recording_done` if we want even finer granularity). Deleted at the start of `action_finish`.

**Backend extraction:** `parse_dialog_file`, `segment_user_turns`, `trim_silences`, `audio_to_data_url`, `is_dialog_done` and the small file-IO helpers move into `recording_backend.py` to keep `app.py` focused on UI state and event handlers. No behavior changes — pure refactor for legibility.

**Files touched:**
- `app.py` — rewritten UI section (Blocks, HTML, handlers); imports from `recording_backend`
- `recording_backend.py` — new module, ~250 lines extracted verbatim from current `app.py`
- `static/studio.css` (optional, served via Gradio's `allowed_paths`) — design tokens + layout CSS. Can also live inline at the top of `app.py` if we prefer one-file simplicity.
- `static/studio.js` (optional) — click delegation + keyboard listeners + localStorage. Same one-file caveat.

**Acknowledged trade-offs:** Gradio wraps every component in its own DOM, so a few details (audio component chrome, file upload progress) can't be made pixel-perfect. We get ~80% of the design quality at ~30% of the effort vs a FastAPI+SPA rewrite. Zero risk to deploy infra.

## 10. Open questions

1. **Partial progress file** — confirmed in scope? (~10 LoC backend; needed for "thu tiếp" CTA and per-card "X câu thu"). If skipped, the picker drops the hero CTA and the per-card progress counter.

2. ~~Frontend stack (Option B vs C)~~ — **Resolved:** stay on Gradio. No stack change.

3. **Auth / multi-recorder isolation** — currently anyone with the URL types a name. Should the name be enforced (no recording until set)? Should we add a simple session cookie so different recorders on the same machine don't collide? Out of scope unless flagged.

4. **Keyboard shortcuts** — confirmed: Space (record/stop, play/pause), Enter (save), R (re-record), → (skip Khách), Esc (back). If any of these conflict with browser shortcuts in practice, we drop them in a follow-up.

5. **"Thu lại" on past assistant turns** — currently the spec lets the recorder re-record any past turn via the rail. This requires resetting `current_turn` mid-conversation. Is this desired (vs only allowing re-record on the most recent turn)?
