# Local screenshot labels

Add one `.json` per manually inspected PNG, following
[OCR_REGRESSION.md](../../../docs/OCR_REGRESSION.md).

Required keys: `image` (relative to `tests/專案測試圖片/`), `sha256`,
`screen_type`, and `fields`. Use the existing ground-truth field names.
Unknown answers are null and are not evaluated. Do not change the old ground
truth files or commit screenshots. The example in the documentation is not a
sample; only actual `.json` files in this folder are loaded.

Optional `fields.selected_pack` is the full displayed Pack name. Exact matches
PASS; a different Pack always FAILs. Missing/uncertain output is recorded as
UNCERTAIN and counted separately (also a sample FAIL by default). A top-level
`"allow_uncertain": ["selected_pack"]` permits abstention for that field only;
the sample is then UNCERTAIN unless another field fails. This never permits a
wrong Pack. Non-null uncertain answers remain in total, but not correct.

Use `screen_type: "pack_list_crop"` for a left-list-only ROI. The exam bypasses
full-screen classification and runs native-coordinate Pack-list analysis only.
The label's answer is never used by the analyzer. Full SETTING dialogs use
`song_select`. Pack inference requires two distinct matched song titles from the
right list; a left-row visual hint alone cannot confirm selection.

`pack_select_blue1st.json` permits abstention because the crop has neither a song
list nor a reliable unique selection border against its green artwork. The
expected name remains the manually supplied truth; allowing uncertainty does
not change it. Keep existing ground-truth and baseline files unchanged.
