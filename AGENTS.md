# Project rules

- Run tests: `.venv-ocr\Scripts\python.exe -X utf8 -m unittest discover -s tests`.
- Run the OCR exam: `OCRRegression.cmd` (or
  `.venv-ocr\Scripts\python.exe -X utf8 -m tools.ocr_regression`).
- Record an intentional baseline with `OCRRegression.cmd --update-baseline`.
  Review the measured failures; never tune code against the exam to inflate numbers.
- Never commit `tests/專案測試圖片/`, `data/`, or `reports/`.
- Keep code and JSON formatted across multiple lines.
- Run the full test suite and the OCR exam before finishing work.
