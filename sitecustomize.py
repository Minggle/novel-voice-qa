"""Subprocess coverage bootstrap via sitecustomize."""
import atexit
import os
import sys

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage
        root = os.path.dirname(os.path.abspath(__file__))
        cov_file = os.environ.get("COVERAGE_FILE") or os.path.join(root, ".coverage")
        _cov = coverage.Coverage(
            config_file=os.environ.get("COVERAGE_PROCESS_START"),
            data_file=cov_file,
            data_suffix=True,
            source=[os.path.join(root, "scripts")],
        )
        _cov.start()

        def _save():
            try:
                _cov.stop()
                _cov.save()
            except Exception:
                pass

        atexit.register(_save)
    except Exception:
        pass
