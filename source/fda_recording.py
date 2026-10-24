"""FDA compatibility export; lifecycle shared with other exporters."""
from converted_recording import ConvertedRecording
from fda_das import export_parquet

class FDARecording(ConvertedRecording):
    def __init__(self,directory,version,capacity,recorder_factory):
        super().__init__(directory,version,capacity,recorder_factory,
            exporter=lambda *args:export_parquet(*args),fmt='das',title='FDA DAS4',suffix='_FDA0001.das',phase_key='fda_phase')
