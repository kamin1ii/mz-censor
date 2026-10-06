"""Entry point for the frozen build.

PyInstaller runs its entry script as `__main__` with no package context, so
the relative import in alice_censor/__main__.py cannot resolve and the exe
dies on launch with "attempted relative import with no known parent
package". Importing absolutely from here sidesteps that.

Running the app from source goes through `python -m rpg_censor`.
"""

import sys

if __name__ == "__main__":
    try:
        from rpg_censor.app import run
        sys.exit(run())
    except Exception:
        # A windowed PyInstaller executable has no stderr. Preserve the traceback
        # and make unattended startup checks fail instead of hanging on a dialog.
        import tempfile
        import traceback
        from pathlib import Path
        error = traceback.format_exc()
        log = Path(tempfile.gettempdir()) / 'MZCensor-startup-error.log'
        log.write_text(error, encoding='utf-8')
        if '--smoke-test' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f'MZ Censor could not start. Details: {log}\n\n{error}', 'MZ Censor', 0x10)
        sys.exit(1)
