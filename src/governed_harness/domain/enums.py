from __future__ import annotations

from enum import StrEnum


class ResultStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    ERROR = "ERROR"
    INCONCLUSIVE = "INCONCLUSIVE"
    INTERRUPTED = "INTERRUPTED"
    """The harness stopped while the phase ran (``governance.workspaceLease``); ``run
    continue`` recovers the run and runs the phase again."""

    @property
    def terminal(self) -> bool:
        return self not in {ResultStatus.PENDING, ResultStatus.RUNNING}

    @property
    def successful(self) -> bool:
        return self in {ResultStatus.PASSED, ResultStatus.NOT_APPLICABLE}


class PhaseId(StrEnum):
    INTENT = "INTENT"
    DISCOVERY = "DISCOVERY"
    SPECIFICATION = "SPECIFICATION"
    PLANNING = "PLANNING"
    IMPLEMENTATION = "IMPLEMENTATION"
    VERIFICATION = "VERIFICATION"
    INDEPENDENT_REVIEW = "INDEPENDENT_REVIEW"
    DECISION = "DECISION"
    CLOSURE = "CLOSURE"
    RETROSPECTIVE = "RETROSPECTIVE"


class ActorType(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    TOOL = "TOOL"
    PLUGIN = "PLUGIN"
    HARNESS = "HARNESS"
    CI = "CI"


class ErrorKind(StrEnum):
    HARNESS_ERROR = "HARNESS_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    PROJECT_ERROR = "PROJECT_ERROR"
    PREEXISTING_ERROR = "PREEXISTING_ERROR"
    INTRODUCED_ERROR = "INTRODUCED_ERROR"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    SECURITY_ERROR = "SECURITY_ERROR"


class ValidationKind(StrEnum):
    SUCCESS = "SUCCESS"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    PROJECT_ERROR = "PROJECT_ERROR"
    PREEXISTING_ERROR = "PREEXISTING_ERROR"
    INTRODUCED_ERROR = "INTRODUCED_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    INCONCLUSIVE = "INCONCLUSIVE"


class FindingSeverity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DecisionKind(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    APPROVE_EXCEPTION = "APPROVE_EXCEPTION"


class RecommendationDecision(StrEnum):
    ACCEPT = "ACCEPT"
    EDIT = "EDIT"
    REJECT = "REJECT"


class EvidenceKind(StrEnum):
    INTENT = "INTENT"
    CONFIGURATION = "CONFIGURATION"
    PLAN = "PLAN"
    CHANGESET = "CHANGESET"
    COMMAND = "COMMAND"
    TEST_REPORT = "TEST_REPORT"
    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    REVIEW = "REVIEW"
    GATE = "GATE"
    HUMAN_DECISION = "HUMAN_DECISION"
    TRACE = "TRACE"
    METRIC = "METRIC"
    RETROSPECTIVE = "RETROSPECTIVE"
    OTHER = "OTHER"


class MemoryLevel(StrEnum):
    NORMATIVE = "NORMATIVE"
    PROJECT = "PROJECT"
    TASK = "TASK"
    EPHEMERAL = "EPHEMERAL"
    RETROSPECTIVE = "RETROSPECTIVE"


class CapabilityName(StrEnum):
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    PROCESS_EXECUTE = "process.execute"
    NETWORK_CONNECT = "network.connect"
    GIT_READ = "git.read"
    GIT_STAGE = "git.stage"
    GIT_COMMIT = "git.commit"
    GIT_PUSH = "git.push"
    SECRETS_USE = "secrets.use"
    MCP_INVOKE = "mcp.invoke"
    ARTIFACT_PUBLISH = "artifact.publish"
    APPROVAL_REQUEST = "approval.request"


class VerificationLevel(StrEnum):
    """Rungs of the verification ladder (since 1.1, ``verification.ladder``): what kind of
    evidence shows that an acceptance criterion holds. A higher rung is stronger evidence of
    the behaviour a person asked for; a rung is reached only by evidence recorded for the
    criterion, never by omission."""

    L0 = "L0"
    """Static: the mandatory validators and the harness's deterministic checks passed."""
    L1 = "L1"
    """Unit: a passing test of the repository names the criterion."""
    L2 = "L2"
    """Integration with the repository's own doubles (fakes, in-memory services)."""
    L3 = "L3"
    """Executable behaviour: a declared probe ran the program and its assertions held."""
    L4 = "L4"
    """External environment: CI, staging or a device lab, through attached evidence."""
    L5 = "L5"
    """Human: a person checked it and ticked it in DECISION."""

    @property
    def rank(self) -> int:
        return int(self.value[1:])


class MetricQuality(StrEnum):
    OBSERVED = "OBSERVED"
    REPORTED = "REPORTED"
    DERIVED = "DERIVED"
    ESTIMATED = "ESTIMATED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
