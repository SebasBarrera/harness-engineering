"""The generic review framework (#57): a panel of reviewers by domain over diff slices, a
layered rule catalog (built-in, language packs, project), a fixed output contract and a verdict
the harness recomputes. See ``docs/guides/review-panel.md``."""

from .cache import ReviewCache, global_key, reviewer_key
from .contract import ContractError, ReviewFinding, contract_text, normalize, verdict_of
from .diff import FileChange, Locations, diff_hash, parse_diff, reportable_locations
from .panel import (
    Invoker,
    PanelInputs,
    PanelReport,
    ReviewerAnswer,
    ReviewerCall,
    ReviewerOutcome,
    run_panel,
)
from .reviewers import (
    BUILTIN_REVIEWERS,
    Reviewer,
    load_reviewers,
    render_block,
    reviewer_domains,
    sync_blocks,
)
from .rules import Catalog, Rule, builtin_rules, merge, pack_rules, project_rules

__all__ = [
    "BUILTIN_REVIEWERS",
    "Catalog",
    "ContractError",
    "FileChange",
    "Invoker",
    "Locations",
    "PanelInputs",
    "PanelReport",
    "ReviewCache",
    "ReviewFinding",
    "Reviewer",
    "ReviewerAnswer",
    "ReviewerCall",
    "ReviewerOutcome",
    "Rule",
    "builtin_rules",
    "contract_text",
    "diff_hash",
    "global_key",
    "load_reviewers",
    "merge",
    "normalize",
    "pack_rules",
    "parse_diff",
    "project_rules",
    "render_block",
    "reportable_locations",
    "reviewer_domains",
    "reviewer_key",
    "run_panel",
    "sync_blocks",
    "verdict_of",
]
