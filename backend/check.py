"""Run the workbench regression suite against disposable databases."""
import unittest

from .serve import configure_imports


if __name__ == '__main__':
    configure_imports()
    suite = unittest.defaultTestLoader.loadTestsFromName('backend.test_api')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
