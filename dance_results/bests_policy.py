"""EX compatibility is explicit and independent of a record's MAX definition."""
from .migrations_v8 import ELIGIBLE

EX_POLICY='confirmed-perfect2-cool1-v1'
KNOWN_VERSIONS=('iidx-v1','iidx-v2-independent-max','custom-judged-v1')
ARITHMETIC="""typeof(r.perfect)='integer' AND r.perfect>=0 AND
 typeof(r.cool)='integer' AND r.cool>=0 AND typeof(r.bad)='integer' AND r.bad>=0 AND
 typeof(r.miss)='integer' AND r.miss>=0 AND r.ex_score=2*r.perfect+r.cool"""
PROOF="""r.review_id IS NOT NULL AND length(r.screenshot_sha256)=64 AND
 json_extract(r.evidence_json,'$.screenshot_sha256')=r.screenshot_sha256 AND
 json_extract(r.evidence_json,'$.values.perfect')=r.perfect AND
 json_extract(r.evidence_json,'$.values.cool')=r.cool AND
 json_extract(r.evidence_json,'$.values.bad')=r.bad AND
 json_extract(r.evidence_json,'$.values.miss')=r.miss AND
 EXISTS(SELECT 1 FROM game_difficulties identity_chart WHERE identity_chart.difficulty_id=r.difficulty_id
  AND json_extract(r.evidence_json,'$.song.song_id')=identity_chart.song_id
  AND json_extract(r.evidence_json,'$.values.difficulty')=identity_chart.difficulty) AND
 (json_extract(r.evidence_json,'$.result_verification.confirmed') IS 1 OR
  (json_extract(r.evidence_json,'$.review.identity_confirmation.source')='explicit_user_review' AND
   json_extract(r.evidence_json,'$.review.draft.identity_reviewed') IS 1 AND
   json_extract(r.evidence_json,'$.review.draft.player_verified') IS 1))"""
VERIFIED_EX='('+ELIGIBLE+') AND ('+ARITHMETIC+") AND json_extract(r.evidence_json,'$.scoring_version') IN ('"+"','".join(KNOWN_VERSIONS)+"') AND ("+PROOF+") AND json_extract(r.evidence_json,'$.comparison.eligible') IS NOT 0"
# Pre-formal imports remain a visibly separate fallback, never evidence for
# comparing a newly confirmed record. Partial unclassified FAILED is excluded.
LEGACY_RAW='('+ELIGIBLE+") AND r.review_id IS NULL AND json_extract(r.evidence_json,'$.scoring_version') IS NULL"
NO_VERIFIED="NOT EXISTS(SELECT 1 FROM play_records cr WHERE cr.difficulty_id=r.difficulty_id AND "+VERIFIED_EX.replace('r.','cr.')+')'
SCORE_ELIGIBLE='(('+VERIFIED_EX+') OR ('+LEGACY_RAW+" AND r.completion_status='COMPLETED' AND "+NO_VERIFIED+'))'
LAMP_ELIGIBLE='(('+VERIFIED_EX+') OR ('+LEGACY_RAW+' AND '+NO_VERIFIED+")) AND r.lamp_status IN ('FAILED','CLEAR','AC','ACP')"
TIME_ORDER="CASE WHEN julianday(r.played_at) IS NULL THEN 1 ELSE 0 END,julianday(r.played_at),r.record_id"
BESTS_VIEW="""CREATE VIEW difficulty_bests AS SELECT d.difficulty_id,d.song_id,d.difficulty,
 (SELECT MAX(r.ex_score) FROM play_records r WHERE r.difficulty_id=d.difficulty_id AND SCORE) AS highest_ex_score,
 CASE (SELECT MAX(CASE r.lamp_status WHEN 'ACP' THEN 4 WHEN 'AC' THEN 3 WHEN 'CLEAR' THEN 2 WHEN 'FAILED' THEN 1 ELSE 0 END)
 FROM play_records r WHERE r.difficulty_id=d.difficulty_id AND LAMP)
 WHEN 4 THEN 'ACP' WHEN 3 THEN 'AC' WHEN 2 THEN 'CLEAR' WHEN 1 THEN 'FAILED' ELSE 'UNPLAYED' END AS best_clear_lamp
 FROM game_difficulties d""".replace('SCORE',SCORE_ELIGIBLE).replace('LAMP',LAMP_ELIGIBLE)
