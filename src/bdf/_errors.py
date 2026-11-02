"""Custom error classes."""


class BDFValidationError(Exception):
    """Raised when a DataFrame fails BDF validation."""


class BDFMetadataError(Exception):
    """Raised when a metadata sidecar exists and cannot be restored."""


class BDFMetadataWarning(UserWarning):
    """Warned when a metadata sidecar is restored with values its BattINFO schema could not check, or refused."""
