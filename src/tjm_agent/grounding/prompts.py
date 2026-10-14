"""Prompts. The planner prompts are verbatim from ScreenSpot-Pro Appendix C (Tables 7 and 8).

The grounder prompt is UI-Venus-1.5's official grounding prompt (inclusionAI/UI-Venus,
models/grounding/ui_venus1_5_gd.py), including its refusal clause.
"""

POSITION_INFERENCE = """I want to identify a UI element that best matches my instruction. Please help me determine which region(s) of the screenshot to focus on and list the UI elements that might appear next to the target.
If the target does not exist in the screenshot, please output "No target".
Output Requirements:
1. List the possible regions in descending order of probability.
2. Always make specific, clear and unique references to avoid ambiguity. References such as "Other icons" and "window" are NOT allowed.
3. Use the following XML tags to describe items in the screenshot:
- <element>: Wrap a specific UI element.
- <area>: Describe an area of the UI containing multiple elements.
- <neighbor>: Describe a UI element that may appear around the target.
Example Output:
The <element>shortcut link</element> is most likely to be found in the <area>Settings window</area>, in the <area>tools panel</area>, next to the <neighbor>Search button</neighbor>.
Important Notes:
- The target UI element is guaranteed to be present in the screenshot.
Do not speculate about operations that could change the screenshot.
Instruction:
{instruction}"""

# Deviation (documented in docs/design.md): the paper's prompt asserts the target is
# guaranteed to be present. In live use it may not be, so the abstention line above
# ("If the target does not exist ... output "No target"") is what we rely on; the
# "guaranteed" note is kept verbatim for paper fidelity.

RESULT_CHECK = """You are given a cropped screenshot. Your task is to evaluate whether the marked element in the red box matches the target described in my instruction.
Please follow these steps:
1. Analyze the screenshot by describing its visible content and functionalities.
2. Determine which of the following applies:
- 'is_target': The marked element is the target.
- 'target_elsewhere': The marked element is not the target, but it exists elsewhere.
- 'target_not_found': The marked element is not the target, and it does not exist.
3. If the target exists, rewrite the instruction to make it clearer.
After your analysis, provide the result in JSON format:
- "result": (str) One of 'is_target', 'target_elsewhere', or 'target_not_found'.
- "new_instruction": (str, default null) A clearer version of the instruction.
Here is my instruction:
{instruction}"""

GROUNDER = (
    "Output the center point of the position corresponding to the following instruction: \n"
    "{instruction}. \n\nThe output should just be the coordinates of a point, in the format "
    "[x,y]. Additionally, if the task is infeasible (e.g., the task is not related to the "
    "image), the output should be [-1,-1]."
)

# Our addition (not from the paper): triage an unexpected screen state so the workflow can
# decide whether a bounded, safe dismissal exists. The chosen button is then located by
# ScreenSeekeR from its description — no stored image or coordinates.
DIALOG_TRIAGE = """You are helping a desktop automation script that was in the middle of this step:
{step}
The screen did not reach the expected state ({expected}). Look at the screenshot.
1. Is there a dialog, alert, sheet, panel or pop-up in front? Describe it briefly.
2. List the buttons it offers, exactly as labelled.
3. Classify the safest way forward:
- "safe": one button dismisses or answers it without deleting, overwriting, discarding user data, granting permissions, or sending anything.
- "destructive": every way forward risks data loss or overwriting.
- "permission": it asks to grant access, log in, or install something.
- "none": there is no dialog; the screen simply shows something else.
- "unknown": unclear.
4. If "safe", give the exact label of the one button to click."""
