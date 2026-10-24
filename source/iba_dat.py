"""Viewing of iba DAT parts written by Space Analyzer 2.x (DAT recording removed in 3.0).

A DAT part is opened through its lossless companion Parquet, verified by SHA-256;
the native ibaFiles writer and its DLL are no longer distributed.
"""
import hashlib
from pathlib import Path

VIEW_NOTE='Grafici, marker, FFT e calcoli leggono il Parquet originale associato, limitato a questa parte DAT; non i valori Float32 del DAT.'

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
