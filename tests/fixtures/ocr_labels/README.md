# Local screenshot labels

Add one `.json` per manually inspected PNG, following
[OCR_REGRESSION.md](../../../docs/OCR_REGRESSION.md).

Required keys: `image` (relative to `tests/專案測試圖片/`), `sha256`,
`screen_type`, and `fields`. Use the existing ground-truth field names.
Unknown answers are null and are not evaluated. Do not change the old ground
truth files or commit screenshots. The example in the documentation is not a
sample; only actual `.json` files in this folder are loaded.
