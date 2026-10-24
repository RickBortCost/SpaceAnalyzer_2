"""Observed calendar ticks; start is immutable, exclusive end may be shortened."""
from datetime import datetime,timedelta

class PDA3TimestampHandler:
    @staticmethod
    def describe(reader):
        def calendar(ticks):
            try:return (datetime(1,1,1)+timedelta(microseconds=ticks//10)).isoformat(timespec='microseconds')
            except (ValueError,OverflowError):raise ValueError('PDA3: timestamp fuori intervallo') from None
        return {'internal_start_calendar':calendar(reader.start_ticks),'internal_end_calendar':calendar(reader.end_ticks),
            'internal_timezone':'non decodificata: metadati opachi','duration_seconds':reader.duration_s,
            'timestamp_write_supported':False,'internal_start_modified':False,
            'internal_end_modified':reader.duration_s<600,'end_rewrite_validation':'experimental; user ibaAnalyzer check required',
            'compatibility':'Il calendario interno del modello non e un timestamp UTC verificato.'}
