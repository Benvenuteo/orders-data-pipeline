"""Safe errors: messages may be logged without exposing credentials or payloads."""


class PipelineError(Exception):
    """A failure whose message is safe to expose to an operator."""


class SourceProtocolError(PipelineError):
    """The source violated pagination, identity, or change-log guarantees."""


class SourceUnavailable(PipelineError):
    """The source could not be reached within the configured retry budget."""


class ValidationRejected(PipelineError):
    """At least one business record failed validation."""


class ConcurrentRun(PipelineError):
    """Another process owns the target database application lock."""


class CheckpointConflict(PipelineError):
    """The source identity or expected checkpoint does not match the target."""


class SimulatedFailure(PipelineError):
    """An explicitly requested failure before transaction commit."""
