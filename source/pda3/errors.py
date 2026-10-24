"""Distinguish a model payload limit from data, disk or validation failures."""
class PDA3CapacityError(ValueError):
    def __init__(self,message,**details):
        super().__init__(message);self.details=details
