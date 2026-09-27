"""吞噬星空 (WO-102 §3–4): Q–AC from the web ledger, the content timeline and a target-blind GLM call.

config.py  — cases/tsxk.yaml, the workbook (input columns only) and the web-ledger interface;
fields.py  — the columns code decides from the timeline and the ledger (Q, W, X, T, AA, S, AB; Y and Z
             when no 吞噬星空 animation is shown);
classify.py — the GLM call for the rest (Y, U, V, Z) and the AC sections, with its checks;
ac.py      — AC assembled: the model's sections plus the time lines code writes;
check.py   — the 30-row check list and the scoring script (not run in WO-102).
"""
