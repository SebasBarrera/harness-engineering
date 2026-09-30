from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DetectionResult:
    profile_id: str
    technology: str
    confidence: float
    evidence: tuple[str, ...]
    warnings: tuple[str, ...] = ()


class MarkerDetector:
    profile_id: str
    technology: str
    markers: tuple[tuple[str, float], ...]

    def detect(self, workspace: Path) -> DetectionResult:
        root = workspace.resolve(strict=True)
        evidence: list[str] = []
        score = 0.0
        for marker, weight in self.markers:
            if (root / marker).exists():
                evidence.append(marker)
                score += weight
        return DetectionResult(
            profile_id=self.profile_id,
            technology=self.technology,
            confidence=min(1.0, score),
            evidence=tuple(evidence),
        )


class PythonDetector(MarkerDetector):
    profile_id = "python_default"
    technology = "python"
    markers = (
        ("pyproject.toml", 0.80),
        ("requirements.txt", 0.45),
        ("setup.py", 0.35),
        ("pytest.ini", 0.30),
        ("tox.ini", 0.25),
    )


class NodeDetector(MarkerDetector):
    profile_id = "node_default"
    technology = "node"
    markers = (
        ("package.json", 0.80),
        ("package-lock.json", 0.35),
        ("pnpm-lock.yaml", 0.35),
        ("yarn.lock", 0.35),
    )

    def detect(self, workspace: Path) -> DetectionResult:
        result = super().detect(workspace)
        locks = [name for name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock") if (workspace / name).exists()]
        warnings = (f"ambiguous package managers: {', '.join(locks)}",) if len(locks) > 1 else ()
        return DetectionResult(
            profile_id=result.profile_id,
            technology=result.technology,
            confidence=result.confidence,
            evidence=result.evidence,
            warnings=warnings,
        )


def detect_profiles(workspace: Path) -> list[DetectionResult]:
    results = [PythonDetector().detect(workspace), NodeDetector().detect(workspace)]
    return sorted(results, key=lambda item: (item.confidence, item.profile_id), reverse=True)
