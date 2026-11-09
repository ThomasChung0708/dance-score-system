"""Optional local OCR backend. Never imports OCR dependencies for catalog work."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path

ENGINE_PROFILE = 'rapidocr-3.10.0-ppocrv5-mobile-det-rec-v1'


class LocalOCREngine:
    def __init__(self, model_dir: Path, low_latency=False):
        try:
            from rapidocr import RapidOCR, ModelType, OCRVersion
        except ImportError as error:
            raise RuntimeError('請先以 Python 3.11 安裝 requirements-ocr.txt；參閱 docs/PHASE4A_OCR.md。') from error
        parameters = {
            'Global.model_root_dir': str(model_dir.resolve()),
            'Global.use_cls': False, 'Global.text_score': 0.35,
            'Global.log_level': 'warning',
            'Det.model_type': ModelType.MOBILE, 'Det.ocr_version': OCRVersion.PPOCRV5,
            'Rec.model_type': ModelType.MOBILE, 'Rec.ocr_version': OCRVersion.PPOCRV5,
            'EngineConfig.onnxruntime.intra_op_num_threads': 4,
            'EngineConfig.onnxruntime.inter_op_num_threads': 1,
        }
        if low_latency:
            parameters.update({'Det.limit_type': 'max', 'Det.limit_side_len': 1024})
        self.engine = RapidOCR(params=parameters)

    def recognize(self, image, *, origin=(0, 0), scale=1):
        import numpy as np
        result = self.engine(np.array(image.convert('RGB'))[..., ::-1].copy(), use_det=True, use_cls=False, use_rec=True)
        if result.boxes is None or result.txts is None:
            return []
        return [dict(text=str(text), confidence=float(score), box=[
            [float(x)/scale+origin[0], float(y)/scale+origin[1]] for x, y in box])
            for box, text, score in zip(result.boxes, result.txts, result.scores)]

    def recognize_line(self, image, *, origin=(0, 0), scale=1):
        """Known single result cell; retain confidence and the entire crop as evidence."""
        import numpy as np
        result = self.engine(np.array(image.convert('RGB'))[..., ::-1].copy(), use_det=False, use_cls=False, use_rec=True)
        if result.txts is None:
            return []
        x, y = origin
        width, height = image.width/scale, image.height/scale
        return [dict(text=str(text), confidence=float(score),
                     box=[[x,y],[x+width,y],[x+width,y+height],[x,y+height]])
                for text, score in zip(result.txts, result.scores) if str(text).strip()]

    def metadata(self):
        import onnxruntime
        return {'profile': ENGINE_PROFILE, 'backend': 'ONNX Runtime / CPU',
                'languages': ['繁體中文', '日本語', 'English'],
                'packages': {n: importlib.metadata.version(n) for n in
                             ['rapidocr', 'onnxruntime', 'numpy', 'Pillow', 'opencv-python-headless']},
                'available_providers': onnxruntime.get_available_providers()}


def cached_page(engine, image, image_sha256, cache_dir):
    """Only cache model output, keyed by pixels and engine profile; never by filename."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((image_sha256+ENGINE_PROFILE).encode()).hexdigest()
    path = cache_dir/(key+'.json')
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    tokens = engine.recognize(image)
    path.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding='utf-8')
    return tokens
