from __future__ import annotations

from governed_harness.validators.command import CommandValidator
from governed_harness.validators.review import IndependentReviewValidator


class ValidatorRegistry:
    def create(self, validator_id: str) -> CommandValidator | IndependentReviewValidator:
        if validator_id == "review.independent":
            return IndependentReviewValidator()
        return CommandValidator(validator_id)
