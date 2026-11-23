from importlib.util import find_spec
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def configure_imports():
    sys.path.insert(0, str(ROOT / '.venv_lib'))
    configured = os.environ.get('ZHIHU_AGENT_ROOT')
    if configured:
        source = Path(configured).expanduser().resolve() / 'src'
        if not (source / 'zhihu_agent' / 'workflow.py').is_file():
            raise SystemExit('ZHIHU_AGENT_ROOT must point to a Zhihu Agent source checkout.')
        sys.path.insert(0, str(source))
    elif find_spec('zhihu_agent') is None:
        raise SystemExit('Install the Zhihu Agent core package or set ZHIHU_AGENT_ROOT to its source checkout.')


if __name__ == '__main__':
    configure_imports()
    import uvicorn

    uvicorn.run('backend.main:app', host='127.0.0.1', port=8765, workers=1)
