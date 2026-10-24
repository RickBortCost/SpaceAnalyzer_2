"""RBF finalization after recording, preserving the original Parquet archive."""
from converted_recording import ConvertedRecording
from rbf import export_parquet


class RBFRecording(ConvertedRecording):
    def __init__(self, directory, version, capacity, recorder_factory):
        super().__init__(directory, version, capacity, recorder_factory,
                         exporter=lambda *args: export_parquet(*args), fmt='rbf',
                         title='RBF v3', suffix='_RBF0001.rbf', phase_key='rbf_phase')
