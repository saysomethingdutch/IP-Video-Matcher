"""Content timeline of a web screen recording (WO-102 §2): what the played video shows, second by second.

Every ~10 s of the played span a frame is taken from the B站 web player only (not the page: the sidebar
thumbnails would look like other works), and near-identical neighbours are folded together. Each frame gets
a content class (rules_tsxk.yaml timeline.content) and a traffic mark (none / tencent / other):

1. local first (D-057 ④): the official logo in the lower right → 吞噬星空; a film watermark → 剧场版;
   OCR addresses, group numbers, cloud-drive words, or an OpenCV QR code → 引流; a dark still → blank;
2. only the frames left over go to the cloud, four to a 2×2 collage with time marks, together with a
   reference board (guidance pictures and a few series frames), to GLM and DeepSeek; a label is adopted only
   when both agree (D-033), otherwise the frame stays "unknown" and is flagged;
3. neighbouring frames of one class become segments; boundaries are refined locally at 1 s.

Output: <out>/timeline.json (frames, segments, traffic intervals, AC lines, flags, cloud usage). Times in
segments and AC lines are on the B站 video's own clock (recording time minus the start of playback, or the
player readings from the web ledger when it has them).
"""
