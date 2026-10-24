"""Startup progress markers for the launcher splash screen.

One stdout line per stage, only when the Windows launcher asks for them
(SPACE_SPLASH=1); the launcher copies every other line to its log unchanged.
"""
import os

MARKER = '@@SPACE_ANALYZER_STARTUP'


def report(percent, label):
    if os.environ.get('SPACE_SPLASH') != '1':
        return
    try:
        print(f'{MARKER} {max(0, min(100, int(percent)))} {label}', flush=True)
    except (OSError, ValueError):
        pass
