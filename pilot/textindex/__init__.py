"""Source subtitle index (WO-002a): burned-in subtitle OCR of the source episodes, single-line lookup
and sequence matching.

    python -m pilot.textindex build --episodes real_test --work qsjx
    python -m pilot.textindex query --work qsjx "现在要怎么办呀"
    python -m pilot.textindex match --work qsjx lines.tsv

Python API: search.Index.load(db).search(text); sequence.match_sequence(index, [(t, text), ...]).
"""
