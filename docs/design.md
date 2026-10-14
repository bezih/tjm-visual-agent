# Design: ScreenSeekeR Visual Grounding for Desktop Automation

*Part 1 design document — TJM Labs take-home. Target length: 3–4 pages.*

## 1. Goal and scope

Build a Python application that, from a screenshot alone, locates a desktop icon described in
natural language, double-clicks it to launch the application, and writes the first ten
JSONPlaceholder posts to `Desktop/tjm-project/post_{id}.txt` through that application's UI —
re-grounding the icon from a fresh screenshot before every launch.

The grounding engine is the focus. It is an implementation of **ScreenSeekeR** (Li et al.,
*ScreenSpot-Pro*, 2025, §4.2, Algorithm 1, Appendix C) and is written to be target-agnostic:
`(screenshot, "description of a UI element") → location | ambiguous | not_found`. Nothing in it
knows about Notepad, TextEdit, or blog posts.

**Success criteria**

1. Ten files, `post_1.txt … post_10.txt`, whose bytes equal `"Title: {title}\n\n{body}"` for the
   first ten posts returned by the API.
2. Every launch is preceded by a fresh screenshot and a ScreenSeekeR search; no coordinates are
   reused between iterations.
3. The icon is found wherever the reviewer drags it (top-left, center, bottom-right at minimum),
   and the system *declines to click* when the icon is absent or the result cannot be verified.
4. Every search leaves an inspectable trace (planner output, candidate boxes, crops, timings).

## 2. Assumptions and the macOS adaptation

The assignment specifies Windows 10/11 at 1920×1080 with Notepad. With the interviewer's
permission I am building on **macOS (Apple M2, 8 GB, built-in Retina display)**:

| Assignment (Windows) | This implementation (macOS) | Why it is equivalent |
|---|---|---|
| Notepad desktop shortcut | **TextEdit alias** on the Desktop | Same role: plain-text editor launched by double-clicking a desktop icon. |
| 1920×1080 | 1710×1112 points, 3420×2224 pixels (2× Retina) | A *harder* case: screenshots are in pixels, clicks are in points, so coordinate mapping must be explicit. |
| Ctrl+S / Save dialog | ⌘S / macOS save sheet | Same UI-driven save; filename and folder entered through the dialog. |
| Close Notepad | Quit TextEdit (⌘Q) | Forces the next iteration to relaunch from the desktop icon. |

Other deviations, stated up front:

- TextEdit defaults to rich text. The workflow switches each new document to plain text
  (Format ▸ Make Plain Text, ⇧⌘T) and verifies the saved file is UTF-8 `.txt`. TextEdit's
  "smart quotes/dashes" substitutions are disabled for the run so the typed text is saved
  byte-exact; this is verified by reading the file back.
- Grounding uses screenshots only. Accessibility APIs, Spotlight, `open -a`, and stored icon
  templates are not used to *find or launch* the app. (Reading the saved file from disk is used
  for *verification*, which the assignment allows.)
- macOS requires Screen Recording and Accessibility permissions for the terminal running the
  agent; a `doctor` command checks both before anything runs.

## 3. Why ScreenSeekeR (and not template matching or OCR)

The brief asks for an approach that works "for any icon or button even if we don't have the exact
image or text beforehand" and that can deal with unexpected pop-ups. That rules out the classic
options as the *primary* method:

- **Template matching** needs the exact icon image; it breaks with icon size settings, light/dark
  themes, Retina scaling, and selection highlights, and cannot generalise to unseen targets.
- **OCR on the label** fails for unlabeled buttons and icons, and for truncated labels.
- **Single-shot VLM grounding** generalises, but degrades sharply on large, dense, high-resolution
  screens — the core finding of ScreenSpot-Pro (best model 18.9% when targets cover ~0.07% of the
  screen).

ScreenSeekeR addresses that last failure by **narrowing the search the way a person does**: a
strong multimodal *planner* reasons about where a target is likely to be ("on the desktop, near
other app aliases"; "in the dialog's button row, next to *Save*"), a specialist *grounder* localises
those references, and the search recurses into the most promising crop until a verified hit is
found. In the paper this lifts OS-Atlas-7B from 18.9% to 48.1% on ScreenSpot-Pro with no
training. A desktop icon is an easy case for this method — the brief says so explicitly — and the
point of the exercise is that the same engine finds a "Cancel" button in a pop-up it has never seen.

## 4. Models

| Role | Paper | This implementation | Notes |
|---|---|---|---|
| Planner (position inference, result checking) | GPT-4o via API | **Claude Opus 5.5** via Anthropic API | Any strong multimodal model fits this role; the paper's prompts are used verbatim. Swapping vendors is a config change. |
| Grounder | OS-Atlas-7B (local) | **UI-Venus-1.5-2B**, 6-bit MLX, local on Apple Silicon | A 7B grounder does not fit comfortably in 8 GB RAM. UI-Venus-1.5-2B is a GUI-grounding specialist (57.7% on ScreenSpot-Pro by itself, per its model card) and supports explicit refusal (`[-1,-1]`). |

Both sit behind two small interfaces (`Planner`, `Grounder`) in `grounding/backends.py`; nothing
else in the code imports a model SDK. Disclosure: screenshots and crops are sent to Anthropic's
API for planning and checking. The demo runs on a cleared desktop so no private content is sent.

## 5. Grounding engine — mapping the paper to code

```
VisualSearch(T, I, viewport, depth)
  if depth ≥ Dmax or viewport ≤ Smin:          → leaf: ground + check
  areas  = Planner.position_inference(T, I)    → <element>, <area>, <neighbor> references
  boxes  = Grounder.ground(each reference, I)  → points/boxes in viewport coords
  cands  = dilate(boxes, Smin)                 → context-preserving candidate patches
  cands  = NMS(cands)                          → drop redundant overlaps
  score  = Σ_boxes gaussian_center_score(box, cand)    (Eq. 1, σ = 0.3)
  for cand in sort(cands, score):               → recurse into the crop
      result = VisualSearch(T, crop(I, cand), depth + 1)
      if result.terminate: return result
  return None
```

| Paper mechanism | Implementation | Paper vs. ours |
|---|---|---|
| Position inference (Appendix C, Table 7) | Planner receives the viewport image and the paper's prompt; we parse `<element>`, `<area>`, `<neighbor>` tags in order; `"No target"` short-circuits to *not found*. | Paper-derived; prompt verbatim. |
| Grounding of references | Each tagged phrase is grounded in the current viewport. The grounder returns a point; we form a box around it (default 2% of the viewport side for elements/neighbors; areas are given a larger prior box). | **Ours**: the paper's grounder returns boxes; ours returns points, so the box size is a documented hyperparameter. Dilation dominates it anyway. |
| Dilation | Expand each box to at least `Smin` (default 1280 px — the paper's leaf size — capped by the viewport), clamped to the image. | Paper-derived; `Rmax` (max ratio) is undefined in the paper, so we cap patches at 50% of the viewport side per level to guarantee narrowing. |
| Candidate scoring (Eq. 1–2) | `s = exp(−((x′−0.5)² + (y′−0.5)²) / 2σ²)` if a voting box centre lies inside the candidate, else 0; summed over all grounded boxes; σ = 0.3. | Paper-derived, unit-tested. |
| NMS | Greedy IoU suppression (threshold 0.5) on candidates ordered by score. | Algorithm 1 lists scoring *after* NMS but uses `nms_patches` before it is defined; we interpret as: dilate → score → NMS → sort. |
| Recursion and termination | Depth-first over sorted candidates, `Dmax = 3`; leaf when the viewport is ≤ `Smin` or at max depth. | Algorithm 1 resets `d ← 0` inside the function; we treat it as the initial call only. |
| Leaf grounding + result check (Appendix C, Table 8) | Grounder predicts the target in the leaf crop; we draw a red box on the crop and ask the planner the paper's check prompt; JSON verdict `is_target` / `target_elsewhere` / `target_not_found`, optional `new_instruction`. | Paper-derived. `target_elsewhere` continues the search with the rewritten instruction; `target_not_found` prunes the branch. |

**Coordinates.** Every viewport carries its rectangle in full-screenshot pixel space; all model
outputs are converted immediately (`grounder 0–1000 → viewport px → screenshot px`). Only
`geometry.py` converts screenshot pixels to click points (`÷ backing scale`, here 2.0). The
transformation is unit-tested and verified live by moving the mouse to a grounded point and
re-capturing.

**Outcomes and budgets.** The engine returns `found` (verified box + click point), `ambiguous`
(more than one verified match, e.g. two TextEdit aliases), or `not_found`. It never converts
uncertainty into a click. Budgets bound the worst case: max depth, max planner calls (default 8),
max grounder calls, and a wall-clock limit (default 90 s). Model confidence is not treated as
calibrated probability; only the planner's explicit check verdict gates a click.

## 6. Automation workflow

A deterministic state machine drives the known task; the grounding engine is called wherever a
*screen location* is needed. The model is not asked to re-plan the whole task each step.

For each post (ids 1–10, fetched once and validated: integer id, non-empty string title/body):

1. **Pre-check**: TextEdit not running (quit it cleanly if a previous run left it open; never
   discard unsaved user documents — stop instead). Output path must not already exist, or it is
   moved to a timestamped backup only with `--overwrite`.
2. **Ground** the target "TextEdit application icon on the desktop" from a fresh screenshot.
3. **Launch**: double-click the click point; wait (bounded) until TextEdit is frontmost with an
   editable untitled document. If TextEdit opens its file browser instead, choose *New Document*.
4. **Prepare**: switch to plain text; confirm via window state.
5. **Type** `"Title: {title}\n\n{body}"` once (no blind retries — retyping risks duplicate text).
6. **Save**: ⌘S, ⌘⇧G to enter `~/Desktop/tjm-project`, type `post_{id}.txt`, confirm; handle
   "replace?" and ".txt extension?" dialogs by grounding the specific button by description.
7. **Verify** on disk: file exists, name correct, bytes equal the expected text.
8. **Quit** TextEdit (⌘Q) and confirm the process exited before the next iteration.

## 7. Failure handling

| Failure | Detection | Response |
|---|---|---|
| Icon moved | — | Normal case: every launch re-grounds from a fresh screenshot. |
| Icon absent / hidden behind a window | Planner says "No target" or every check returns `target_not_found` | Minimise/hide frontmost windows once (show Desktop), re-search; then stop with a clear error and saved trace. |
| Two similar icons (e.g. TextEdit + another editor) | Multiple verified matches | Return `ambiguous`; do not click. |
| Wrong click (opened something else, or nothing) | Frontmost app ≠ TextEdit within timeout | Close what opened if it is ours; re-ground once; then stop. |
| Unexpected pop-up / dialog | Expected state not reached; screenshot shows a dialog | Ask the planner to describe the dialog; if it is a known-safe dismissal (e.g. "OK" on an informational alert) ground that button by description and click it; stop on anything destructive, permission-related, or unclear. |
| Save dialog variants, file exists | Planner/grounder on the sheet | Ground buttons by description, never by fixed coordinates. Never overwrite without `--overwrite`. |
| API failure / malformed model output | HTTP errors, parse failures | Bounded retries with backoff for transient errors; malformed output counts as a failed call, never a guess. |
| Content mismatch after save | Disk read ≠ expected | Mark the post failed, keep the file for inspection, continue or stop per flag. |

## 8. Testing and evidence

- **Unit tests (CI)**: coordinate transforms, dilation/clamping, Eq. 1 scoring, NMS, recursion
  termination and budget exhaustion with fake planner/grounder, response parsing, expected-text
  construction, workflow transitions against a fake desktop.
- **Replay tests**: recorded screenshots + recorded model responses, run deterministically.
- **Live tests (opt-in, real Mac)**: icon in top-left / center / bottom-right; icon absent;
  decoy icons; full ten-post run. Each produces an annotated screenshot and a trace in
  `artifacts/`; the curated three go to `evidence/` with a manifest (commit, models, config,
  timings).
- Reported separately: grounding success, abstentions, wrong clicks, end-to-end completion,
  latency, model calls. Mocked results are never presented as live results.

## 9. Trade-offs and performance

- **Latency vs. robustness**: a full ScreenSeekeR search costs a few planner calls (≈ 3–10 s
  each) and several local grounder calls (≈ 1–3 s each on M2), so a search takes tens of seconds,
  versus ~2 s for single-shot grounding. That is acceptable for launching an app ten times; it is
  the cost of a method that generalises to dense professional UIs.
- **Planned optimisation (only after the baseline works)**: an adaptive fast path — ground the
  whole screen once, verify with the result-check prompt, and escalate to the full search only if
  verification fails. Measured against the full search on the same screenshots; kept only if it
  helps. The full ScreenSeekeR mode remains independently runnable.
- **Cost**: roughly 3–8 planner calls per launch with downscaled crops; well under a dollar for
  the full run.

## 10. Deferred improvements

Caching the planner's layout description between iterations; a local planner to remove the
network dependency; multi-monitor support; batching grounder calls across references; a learned
calibrator for when to trust the fast path; Windows/Notepad port (only the desktop executor and
app-specific workflow constants change).
