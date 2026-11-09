"""Fixed, labelled static OCR exam. Answers are used only by the scorer."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

from dance_results import ocr_static
from dance_results.result_ocr import recognize_result

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / 'tests' / 'fixtures'
PRIMARY_FIELDS = (
    'screen_type', 'title', 'difficulty', 'game_level', 'bpm',
    'perfect', 'cool', 'bad', 'miss', 'judgments', 'ex_score',
    'selected_pack',
)
JUDGMENTS = ('perfect', 'cool', 'bad', 'miss')


def image_name(name):
    """Labels address PNGs relative to the local screenshot directory."""
    if not isinstance(name, str):
        raise ValueError('Image name must be a relative PNG path')
    path = PurePosixPath(name.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in name or path.suffix.lower() != '.png':
        raise ValueError('Image name must be a relative PNG path: ' + name)
    return path.as_posix()


def merge_label(labels, name, row, source):
    name = image_name(name)
    if not isinstance(row, dict) or not re.fullmatch(r'[0-9a-fA-F]{64}', row.get('sha256', '')):
        raise ValueError(f'{source}: missing or invalid sha256 for {name}')
    if not isinstance(row.get('fields', {}), dict):
        raise ValueError(f'{source}: fields must be an object for {name}')
    # Windows paths are case-insensitive. Preserve the first spelling for display.
    name = next((old for old in labels if old.casefold() == name.casefold()), name)
    merged = labels.setdefault(name, {'fields': {}, 'sources': []})
    allowed = row.get('allow_uncertain', [])
    if not isinstance(allowed, list) or any(key != 'selected_pack' for key in allowed):
        raise ValueError(f'{source}: allow_uncertain supports only selected_pack for {name}')
    for key in ('sha256', 'screen_type', 'local_player_status', 'allow_uncertain'):
        if key not in row:
            continue
        value = row[key].lower() if key == 'sha256' else row[key]
        if key in merged and merged[key] != value:
            raise ValueError(f'{source}: conflicting {key} labels for {name}')
        merged[key] = value
    for key, value in row.get('fields', {}).items():
        if key in merged['fields'] and merged['fields'][key] != value:
            raise ValueError(f'{source}: conflicting {key} labels for {name}')
        merged['fields'][key] = value
    merged['sources'].append(str(source))


def load_labels(fixtures=FIXTURES):
    labels = {}
    for filename in ('phase4a_ground_truth.json', 'phase4c_ground_truth.json'):
        path = fixtures / filename
        data = json.loads(path.read_text(encoding='utf-8'))
        for name, row in data['images'].items():
            merge_label(labels, name, row, filename)
    for path in sorted((fixtures / 'ocr_labels').rglob('*.json')):
        row = json.loads(path.read_text(encoding='utf-8'))
        if not all(key in row for key in ('image', 'sha256', 'screen_type', 'fields')):
            raise ValueError(f'{path}: image, sha256, screen_type and fields are required')
        merge_label(labels, row['image'], row, str(path.relative_to(fixtures)))
    for name, row in labels.items():
        if 'screen_type' not in row:
            raise ValueError('Missing screen_type label: ' + name)
    return labels


def derived_ex(fields):
    perfect, cool = fields.get('perfect'), fields.get('cool')
    if all(type(value) is int and value >= 0 for value in (perfect, cool)):
        return perfect * 2 + cool
    return None


def score_sample(name, label, actual, *, error=None):
    """Pure exact-value scoring; null answers never enter any denominator."""
    expected = dict(label.get('fields', {}))
    for key in ('screen_type', 'local_player_status'):
        if key in label:
            expected[key] = label[key]
    if 'ex_score' not in expected and derived_ex(expected) is not None:
        expected['ex_score'] = derived_ex(expected)
    got = dict(actual)
    got['ex_score'] = derived_ex(got)
    comparisons = [
        {
            'field': key,
            'expected': wanted,
            'got': got.get(key),
            'evaluated': wanted is not None,
            'correct': wanted == got.get(key) if wanted is not None else None,
        }
        for key, wanted in expected.items()
    ]
    uncertainties = []
    for item in comparisons:
        if item['field'] == 'selected_pack' and item['evaluated']:
            uncertain = item['got'] is None or item['got'] == 'uncertain'
            item['outcome'] = 'UNCERTAIN' if uncertain else 'PASS' if item['correct'] else 'FAIL'
            item['allowed_uncertain'] = uncertain and 'selected_pack' in label.get('allow_uncertain', [])
            if uncertain:
                uncertainties.append(item)
    failures = [item for item in comparisons if item['correct'] is False and not item.get('allowed_uncertain')]
    return {
        'image': name,
        'sha256': label['sha256'],
        'status': 'FAIL' if failures or error else 'UNCERTAIN' if uncertainties else 'PASS',
        'error': error,
        'comparisons': comparisons,
        'mismatches': failures,
        'uncertainties': uncertainties,
        'pack_diagnostics': {key: actual[key] for key in (
            'selected_pack_id', 'selected_pack_confidence', 'selected_pack_evidence', 'pack_reason') if key in actual},
    }


def summarize(samples):
    fields = {key: {'correct': 0, 'total': 0} for key in PRIMARY_FIELDS}
    for sample in samples:
        if sample['status'] == 'SKIPPED':
            continue
        for item in sample['comparisons']:
            metric = fields.setdefault(item['field'], {'correct': 0, 'total': 0})
            if not item['evaluated']:
                continue
            metric['correct'] += int(item['correct'])
            metric['total'] += 1
            if item['field'] == 'selected_pack':
                metric['uncertain'] = metric.get('uncertain', 0) + int(item.get('outcome') == 'UNCERTAIN')
            if item['field'] in JUDGMENTS:
                fields['judgments']['correct'] += int(item['correct'])
                fields['judgments']['total'] += 1
    for metric in fields.values():
        metric['accuracy'] = metric['correct'] / metric['total'] if metric['total'] else None
    return fields


def make_baseline(samples, fields):
    return {
        'schema_version': 1,
        'fields': fields,
        'samples': {
            sample['image']: {
                'status': sample['status'],
                'sha256': sample['sha256'],
                'comparisons': sample['comparisons'],
            }
            for sample in samples if sample['status'] != 'SKIPPED'
        },
    }


def compare_baseline(samples, fields, baseline):
    """Compare exact ratios plus sample status so aggregate gains cannot hide loss."""
    regressions = []
    current = {sample['image']: sample for sample in samples}
    # Remove unavailable images from BOTH populations. Otherwise losing an easy
    # local-only screenshot could look like an OCR regression on unchanged data.
    comparable = {key: dict(metric) for key, metric in baseline['fields'].items()}
    for name, sample in current.items():
        if sample['status'] != 'SKIPPED':
            continue
        old_sample = baseline.get('samples', {}).get(name, {})
        for item in old_sample.get('comparisons', []):
            if not item['evaluated']:
                continue
            keys = [item['field']]
            if item['field'] in JUDGMENTS:
                keys.append('judgments')
            for key in keys:
                comparable[key]['correct'] -= int(item['correct'])
                comparable[key]['total'] -= 1
    for old in comparable.values():
        old['accuracy'] = old['correct'] / old['total'] if old['total'] else None
    for key, old in comparable.items():
        new = fields.get(key, {'correct': 0, 'total': 0, 'accuracy': None})
        # Missing local images and wholly unlabelled groups are not failures.
        if not old['total'] or not new['total']:
            continue
        if new['correct'] * old['total'] < old['correct'] * new['total']:
            regressions.append({
                'field': key,
                'reason': 'accuracy_lower_than_baseline',
                'baseline': old,
                'current': new,
            })
    for name, old in baseline.get('samples', {}).items():
        sample = current.get(name)
        if old['status'] == 'PASS' and (sample is None or sample['status'] in ('FAIL', 'UNCERTAIN')):
            regressions.append({'image': name, 'reason': 'previously_passing_sample_now_fails_or_label_removed'})
        elif old['status'] == 'UNCERTAIN' and (sample is None or sample['status'] == 'FAIL'):
            regressions.append({'image': name, 'reason': 'previously_allowed_uncertainty_now_fails_or_label_removed'})
    return regressions


def recognize_sample(path, digest, engine, catalog, *, no_cache=False, player_name='TC', sample_kind=None):
    """Compose existing static OCR helpers and the existing result recognizer.

    No answers, filenames-as-evidence or monitor state are involved. sample_kind
    selects only the explicit ROI route, never the expected Pack name.
    The page engine/profile, normalization, badge crop and cache are those of
    ocr_static; result cells use result_ocr.recognize_result, as in the monitor.
    """
    from PIL import Image

    with Image.open(path) as image:
        native = (engine.recognize(image) if no_cache else
                  ocr_static.cached_page(engine, image, digest, PROJECT / 'reports' / 'phase4a' / 'cache'))
        if sample_kind == 'pack_list_crop':
            from dance_results.pack_selection import analyze_pack_crop
            selection = analyze_pack_crop(native, image, catalog)
            return {'screen_type': 'pack_list_crop', **{key: selection[key] for key in (
                'selected_pack', 'selected_pack_id', 'selected_pack_confidence',
                'selected_pack_evidence', 'pack_reason')}}
        tokens = ocr_static.normalize_tokens(native, *image.size)
        screen, _ = ocr_static.classify(tokens)
        actual = {'screen_type': screen}
        if screen == 'song_select':
            selection = ocr_static.parse_selection(tokens, image.convert('RGB').resize((1024, 800)))
            from dance_results.pack_selection import infer_selected_pack
            infer_selected_pack(selection, catalog)
            # Same read-only matching as static OCR; no catalog fill is scored.
            catalog.match(selection['fields'], None)
            actual.update({key: field['value'] for key, field in selection['fields'].items()})
            actual.update({key: selection[key] for key in (
                'selected_pack', 'selected_pack_id', 'selected_pack_confidence',
                'selected_pack_evidence', 'pack_reason')})
        elif screen == 'results':
            crop = image.crop((0, int(image.height * .27), int(image.width * .062), int(image.height * .79)))
            extra = engine.recognize(crop.resize((crop.width * 3, crop.height * 3)),
                                     origin=(0, int(image.height * .27)), scale=3)
            context = ocr_static.result_context(
                tokens + ocr_static.normalize_tokens(extra, *image.size), player_name)
            actual['local_player_status'] = context['local_player_status']
            result = recognize_result(image, engine, player_name, tokens=tokens)
            actual.update({key: field['value'] for key, field in result['fields'].items()})
        elif screen == 'waiting_room':
            actual.update(ocr_static.room_context(tokens, player_name))
        elif screen == 'gameplay':
            actual['local_player_status'] = 'in_progress_not_final'
        return actual


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def print_sample(sample):
    print(f"{sample['status']} {sample['image']}", flush=True)
    if sample.get('error'):
        print('  ' + sample['error'], flush=True)
    for item in sample.get('mismatches', []):
        print(f"  {item['field']}: expected={item['expected']!r}, got={item['got']!r}", flush=True)
    for item in sample.get('uncertainties', []):
        print(f"  UNCERTAIN {item['field']}: allowed={item['allowed_uncertain']}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update-baseline', action='store_true')
    parser.add_argument('--no-cache', action='store_true', help='Pass through to the static OCR page reader')
    parser.add_argument('--player-name', default='TC')
    args = parser.parse_args(argv)
    report_path = PROJECT / 'reports' / 'ocr_regression' / 'latest.json'
    baseline_path = FIXTURES / 'ocr_regression_baseline.json'
    report = {
        'schema_version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'no_cache': args.no_cache,
        'player_name': args.player_name,
        'samples': [],
        'regressions': [],
    }
    catalog = None
    try:
        labels = load_labels()
        baseline = None if args.update_baseline else json.loads(baseline_path.read_text(encoding='utf-8'))
        source = PROJECT / 'tests' / '專案測試圖片'
        files = {}
        for name in labels:
            path = source / name
            if not path.resolve().is_relative_to(source.resolve()):
                raise ValueError('Image path escapes the screenshot directory: ' + name)
            if path.is_file():
                files[name] = path
        catalog = ocr_static.ReadOnlyCatalog(PROJECT / 'data' / 'library.sqlite3')
        before = ocr_static.audit(catalog.db)
        ocr_static.check_integrity(catalog.db)
        engine = None
        if files:
            models = PROJECT / '.ocr-models'
            # Fail offline instead of allowing RapidOCR's first-use downloader.
            for filename in ('ch_PP-OCRv5_det_mobile.onnx', 'ch_PP-OCRv5_rec_mobile.onnx'):
                if not (models / filename).is_file():
                    raise ValueError('Required local model is missing: ' + filename)
            engine = ocr_static.LocalOCREngine(models)
            report['engine'] = engine.metadata()
        originals = {}
        invalid_hash = False
        for name, label in labels.items():
            if name not in files:
                sample = {
                    'image': name,
                    'sha256': label['sha256'],
                    'status': 'SKIPPED',
                    'error': 'Local image file is missing',
                    'comparisons': [],
                    'mismatches': [],
                }
            else:
                path = files[name]
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                originals[path] = digest
                if digest != label['sha256']:
                    invalid_hash = True
                    sample = score_sample(name, label, {}, error='Image SHA-256 differs from label; OCR not evaluated')
                    sample['comparisons'] = []
                    sample['mismatches'] = []
                else:
                    try:
                        options = {'sample_kind': 'pack_list_crop'} if label['screen_type'] == 'pack_list_crop' else {}
                        actual = recognize_sample(path, digest, engine, catalog,
                                                  no_cache=args.no_cache, player_name=args.player_name, **options)
                        sample = score_sample(name, label, actual)
                    except Exception as error:
                        sample = score_sample(name, label, {}, error=f'{type(error).__name__}: {error}')
            report['samples'].append(sample)
            print_sample(sample)
        report['fields'] = summarize(report['samples'])
        report['counts'] = {
            status: sum(sample['status'] == status for sample in report['samples'])
            for status in ('PASS', 'FAIL', 'SKIPPED')
        }
        if any(sample.get('uncertainties') for sample in report['samples']):
            report['counts']['UNCERTAIN'] = sum(sample['status'] == 'UNCERTAIN' for sample in report['samples'])
        report['safety'] = {
            'database_mode': 'mode=ro + query_only=ON',
            'database_unchanged': before == ocr_static.audit(catalog.db),
            'original_images_unchanged': all(
                hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in originals.items()),
        }
        ocr_static.check_integrity(catalog.db)
        print('\nPer-field accuracy (null labels and missing images excluded):')
        for key, metric in report['fields'].items():
            rate = f"{metric['accuracy']:.2%}" if metric['accuracy'] is not None else 'N/A'
            print(f"  {key}: {metric['correct']}/{metric['total']} ({rate})")
            if key == 'selected_pack':
                print(f"    UNCERTAIN: {metric.get('uncertain', 0)}")
        if invalid_hash or not all(report['safety'][key] for key in ('database_unchanged', 'original_images_unchanged')):
            raise ValueError('Image hash or read-only safety check failed; baseline was not updated')
        if args.update_baseline:
            write_json(baseline_path, make_baseline(report['samples'], report['fields']))
            print('Baseline updated: ' + str(baseline_path))
        else:
            report['regressions'] = compare_baseline(report['samples'], report['fields'], baseline)
            for regression in report['regressions']:
                print('REGRESSION ' + json.dumps(regression, ensure_ascii=False))
        report['exit_code'] = 1 if report['regressions'] else 0
        print('Overall: ' + json.dumps(report['counts']) + f"; exit {report['exit_code']}")
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        report['exit_code'] = 2
        print(report['error'], file=sys.stderr)
    finally:
        if catalog is not None:
            catalog.close()
        write_json(report_path, report)
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
