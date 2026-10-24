"""Lossless Parquet storage profiles shared by Record and dataset exports.

Encoding/compression only: no rounding, resampling, filtering or value casts.
PyArrow is imported by the caller, outside the acquisition event loop.
"""
import json


PROFILES = {
    "compact": {"codec": "zstd", "level": 9, "row_group_rows": 32768, "flush_seconds": 5,
                "label": "Parquet ZSTD · Compatto"},
    "maximum": {"codec": "zstd", "level": 19, "row_group_rows": 65536, "flush_seconds": 5,
                "label": "Parquet ZSTD · Massima compressione"},
    "fast": {"codec": "zstd", "level": 3, "row_group_rows": 32768, "flush_seconds": 5,
             "label": "Parquet ZSTD · Rapido"},
    "compatible": {"codec": "snappy", "level": None, "row_group_rows": 4096, "flush_seconds": 1,
                   "label": "Parquet Snappy · Compatibilità"},
}
DICTIONARY_COLUMNS = {"node_id", "signal_name", "data_type", "status", "quality", "sample_ms", "status_code"}


def storage_profile(fmt, options=None):
    if options is not None and not isinstance(options, dict):
        raise ValueError("Opzioni registrazione non valide")
    fmt = str(fmt).lower()
    if fmt == "csv":
        return {"format": "csv", "profile": "text", "codec": "none", "level": None,
                "row_group_rows": 4096, "flush_seconds": 1, "label": "CSV", "lossless": True}
    if fmt == 'das':
        return {'format':'das','profile':'fda-das4','codec':'none','level':None,'row_group_rows':32768,
                'flush_seconds':5,'label':'FDA DAS4 + Parquet originali','lossless':False,'original_archive_lossless':True}
    if fmt == 'rbf':
        return {'format':'rbf','profile':'rbf-v3','codec':'none','level':None,'row_group_rows':32768,
                'flush_seconds':5,'label':'RBF v3 + Parquet originali','lossless':False,'original_archive_lossless':True}
    if fmt != "parquet":
        raise ValueError("Scegliere CSV, Binario Parquet, FDA DAS4 o RBF v3. La registrazione .dat (iba/PDA3) è stata rimossa nella 3.0; i file .dat restano apribili")
    key = (options or {}).get("parquet_profile", "compact")
    if not isinstance(key, str) or key not in PROFILES:
        raise ValueError("Profilo binario non valido: scegliere Compatto, Massima compressione, Rapido o Compatibilità")
    return {"format": "parquet", "profile": key, "lossless": True, **PROFILES[key]}


def parquet_options(schema, profile=None):
    """Use identical options for the real writer and file-size estimates."""
    profile = profile or storage_profile("parquet")
    if profile["codec"] == "snappy":
        return {"compression": "snappy"}
    # Dictionary encoding for repeated labels; byte-stream split rearranges the
    # original IEEE-754 bytes, making floating-point columns easier to compress.
    # In particular, do not dictionary-encode those columns (it takes precedence).
    return {"compression": "zstd", "compression_level": profile["level"],
            "use_dictionary": [f.name for f in schema if f.name in DICTIONARY_COLUMNS],
            "use_byte_stream_split": [f.name for f in schema if str(f.type) in ("double", "float")],
            "version": "2.6", "data_page_version": "2.0", "write_page_checksum": True}


def parquet_metadata(profile):
    return {b"storage": json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            b"lossless": b"true", b"sample_policy": b"all original acquisition samples"}
