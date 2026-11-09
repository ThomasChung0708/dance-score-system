"""Shared automatic-submission OCR requirements; diagnostics do not set policy."""

AUTO_CONFIDENCE = .95
RESULT_NUMERIC = ('perfect', 'cool', 'bad', 'miss', 'max_combo', 'game_score')
RESULT_REQUIRED = (*RESULT_NUMERIC, 'grade', 'completion_status', 'player_name', 'title')
SELECTION_REQUIRED = ('title', 'difficulty', 'game_level')
CUSTOM_SELECTION_REQUIRED = ('total_notes', 'duration_seconds')
RESULT_NONFATAL_ERRORS = ('footer_level_is_host_not_local',)


def result_required(ocr):
    return (*RESULT_REQUIRED, *(('game_level',) if not ocr.get('multiplayer') else ()))
