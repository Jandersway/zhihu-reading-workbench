"""Local CPU embeddings for each account's own Zhihu collection."""
from functools import lru_cache
import hashlib
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.request import urlopen
import shutil

MODEL = 'BAAI/bge-small-zh-v1.5'
CACHE = Path(__file__).resolve().parents[1] / '.runtime' / 'embedding-models' / 'bge-small-zh-v1.5'
MODEL_REVISION = '75c43b069aac4d136ba6bc1122f995fedcfd2781'
E5_MODEL = 'intfloat/multilingual-e5-small'
E5_CACHE = CACHE.parent / 'multilingual-e5-small'
E5_REVISION = '761b726dd34fb83930e26aab4e9ac3899aa1fa78'
E5_FILES = {
    'tokenizer.json': ('tokenizer.json', '0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39'),
    'model.onnx': ('onnx/model_quantized.onnx', 'f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193'),
}
FILES = {
    'tokenizer.json': ('tokenizer.json', '48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26'),
    'model.onnx': ('onnx/model_quantized.onnx', '15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc'),
}
_lock = Lock()
_logger = logging.getLogger(__name__)


class LocalEncoder:
    def __init__(self, model_path, tokenizer_path, *, pool='cls', prefix=''):
        from onnxruntime import InferenceSession, SessionOptions
        from tokenizers import Tokenizer

        options = SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = InferenceSession(str(model_path), options, providers=['CPUExecutionProvider'])
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=128)
        pad = '[PAD]' if self.tokenizer.token_to_id('[PAD]') is not None else '<pad>'
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id(pad), pad_token=pad)
        self.inputs = [item.name for item in self.session.get_inputs()]
        self.pool = pool
        self.prefix = prefix

    def embed(self, texts):
        import numpy as np

        vectors = []
        for start in range(0, len(texts), 16):
            rows = self.tokenizer.encode_batch([self.prefix + text for text in texts[start:start + 16]])
            values = {
                'input_ids': np.asarray([row.ids for row in rows], dtype=np.int64),
                'attention_mask': np.asarray([row.attention_mask for row in rows], dtype=np.int64),
                'token_type_ids': np.asarray([row.type_ids for row in rows], dtype=np.int64),
            }
            (hidden, *_) = self.session.run(None, {name: values[name] for name in self.inputs})
            if hidden.ndim == 2:
                pooled = hidden
            elif self.pool == 'mean':
                mask = values['attention_mask'][..., None]
                pooled = (hidden * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1)
            else:
                pooled = hidden[:, 0]
            norm = np.linalg.norm(pooled, axis=1, keepdims=True)
            vectors.extend((pooled / norm.clip(min=1e-12)).tolist())
        return vectors


@lru_cache(maxsize=1)
def encoder():
    return verified_encoder(CACHE, FILES)


@lru_cache(maxsize=1)
def e5_encoder():
    return verified_encoder(E5_CACHE, E5_FILES, pool='mean', prefix='query: ')


def verified_encoder(cache, files, **options):
    for name, (_, checksum) in files.items():
        if not verified(cache / name, checksum):
            raise ValueError(f'Missing or invalid encoder file: {name}')
    return LocalEncoder(cache / 'model.onnx', cache / 'tokenizer.json', **options)


def encode(texts):
    if not texts:
        return []
    try:
        with _lock:
            return encoder().embed(texts)
    except Exception as exc:
        # A missing or unavailable optional CPU model keeps the lexical baseline working.
        _logger.warning('Local collection encoder unavailable (%s)', type(exc).__name__)
        return None


def collection_profile(titles):
    vectors = encode(list(titles.values()))
    if vectors is None:
        return None
    return {'model': MODEL_REVISION, 'vectors': {
        cid: [round(value, 6) for value in vector] for cid, vector in zip(titles, vectors, strict=True)
    }}


def has_current_profile(snapshot):
    profile = snapshot.get('profile') if snapshot else None
    return isinstance(profile, dict) and profile.get('model') == MODEL_REVISION


def semantic_matches(candidates, snapshot, excluded):
    if not has_current_profile(snapshot):
        return 'unavailable', {}
    profile = snapshot['profile']
    titles = snapshot['titles']
    favorites = [(cid, vector) for cid, vector in profile['vectors'].items()
                 if cid in titles and cid not in excluded]
    if not favorites or not candidates:
        return 'ready', {}
    vectors = encode([item['title'] for item in candidates])
    if vectors is None:
        return 'unavailable', {}
    return 'ready', match_title_vectors(candidates, titles, vectors, favorites)


def match_title_vectors(candidates, titles, vectors, favorites):
    import numpy as np

    # The encoder emits normalized vectors, so dot products are cosine similarities.
    matrix = np.asarray([vector for _, vector in favorites], dtype=np.float32)
    scores = np.asarray(vectors, dtype=np.float32) @ matrix.T
    matches = {}
    for candidate, row in zip(candidates, scores, strict=True):
        cid = candidate['candidate_id']
        best = max((i for i, (saved_id, _) in enumerate(favorites) if saved_id != cid),
                   key=row.__getitem__, default=None)
        if best is not None:
            cosine = round(float(row[best]), 6)
            matches[cid] = {'cosine': cosine, 'title': titles[favorites[best][0]]}
    return matches


def e5_matches(candidates, titles):
    if not titles or not candidates:
        return {}
    with _lock:
        encoder = e5_encoder()
        favorites = list(zip(titles, encoder.embed(list(titles.values())), strict=True))
        vectors = encoder.embed([item['title'] for item in candidates])
    return match_title_vectors(candidates, titles, vectors, favorites)


def verified(path, checksum):
    if not path.is_file():
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == checksum


def prepare_model(cache, files, model, revision):
    cache.mkdir(parents=True, exist_ok=True)
    origin = f'https://huggingface.co/Xenova/{model}/resolve/{revision}/'
    for name, (remote, checksum) in files.items():
        target = cache / name
        if verified(target, checksum):
            continue
        with NamedTemporaryFile(dir=cache, delete=False) as temp:
            temporary = Path(temp.name)
            try:
                with urlopen(origin + remote, timeout=120) as source:
                    shutil.copyfileobj(source, temp)
            except Exception:
                temp.close()
                temporary.unlink(missing_ok=True)
                raise
        if not verified(temporary, checksum):
            temporary.unlink()
            raise ValueError(f'Encoder file checksum mismatch: {name}')
        temporary.replace(target)


if __name__ == '__main__':
    from argparse import ArgumentParser
    from .serve import configure_imports

    configure_imports()
    parser = ArgumentParser()
    parser.add_argument('--e5', action='store_true', help='Prepare the optional E5 evaluation model')
    if parser.parse_args().e5:
        prepare_model(E5_CACHE, E5_FILES, 'multilingual-e5-small', E5_REVISION)
        print(f'{E5_MODEL}: {len(e5_encoder().embed(["本地模型检查"])[0])} dimensions ready')
    else:
        prepare_model(CACHE, FILES, 'bge-small-zh-v1.5', MODEL_REVISION)
        print(f'{MODEL}: {len(encoder().embed(["本地模型检查"])[0])} dimensions ready')
