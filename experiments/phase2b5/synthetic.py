"""Protocol-fixed synthetic candidate streams for Phase 2B-5."""

from __future__ import annotations

from dataclasses import dataclass

from ipvm.models import CandidateMatch


FRAME_COUNT = 16
FRAME_INTERVAL = 0.5
TOP_K = 8
FIXTURE_NAMES = (
    "EXTRACTION_LOSS",
    "CLEAN_1_0X",
    "CLEAN_0_8X",
    "CLEAN_1_25X",
    "TWO_STRONG_RIVALS",
    "COHERENT_WRONG_LINE",
    "PARALLEL_COMPETITOR",
    "DUPLICATE_TIMESTAMPS",
    "LOCAL_PLATEAU",
)


@dataclass(frozen=True)
class SyntheticFixture:
    name: str
    candidates: tuple[CandidateMatch, ...]
    truth: dict[str, float | str]


def _grid(value: float) -> float:
    return round(value / FRAME_INTERVAL) * FRAME_INTERVAL


def _truth(speed: float = 1.0) -> dict[str, float | str]:
    return {
        "accused_start": 0.0,
        "accused_end": 8.0,
        "source_episode": "TRUE",
        "source_start": 100.0,
        "source_end": 100.0 + 8.0 * speed,
        "speed_ratio": speed,
    }


def _fillers(frame_index: int, accused_time: float, count: int) -> list[CandidateMatch]:
    return [
        CandidateMatch(
            accused_time=accused_time,
            source_episode=f"FILLER_{frame_index:02d}_{slot:02d}",
            source_time=_grid(300.0 + 10.0 * slot + accused_time),
            visual_score=0.70,
        )
        for slot in range(count)
    ]


def _ordered(per_frame: list[list[CandidateMatch]]) -> tuple[CandidateMatch, ...]:
    output: list[CandidateMatch] = []
    for frame in per_frame:
        output.extend(sorted(frame, key=lambda item: item.visual_score, reverse=True))
    if any(len(frame) != TOP_K for frame in per_frame):
        raise ValueError("every synthetic frame must contain exactly top-k 8")
    return tuple(output)


def _extraction_loss() -> SyntheticFixture:
    per_frame: list[list[CandidateMatch]] = []
    for index in range(FRAME_COUNT):
        accused = index * FRAME_INTERVAL
        offset = 1.5 if index < 4 else 1.0 if index < 8 else 0.5
        frame = [
            CandidateMatch(accused, "TRUE", 100.0 + accused + offset, 0.9530),
            CandidateMatch(accused, "TRUE", 100.0 + accused, 0.9524),
            *_fillers(index, accused, 6),
        ]
        per_frame.append(frame)
    return SyntheticFixture("EXTRACTION_LOSS", _ordered(per_frame), _truth())


def _base_line(name: str, speed: float, *, wrong_only: bool = False) -> SyntheticFixture:
    per_frame: list[list[CandidateMatch]] = []
    for index in range(FRAME_COUNT):
        accused = index * FRAME_INTERVAL
        episode = "RIVAL" if wrong_only else "TRUE"
        start = 200.0 if wrong_only else 100.0
        frame = [
            CandidateMatch(accused, episode, _grid(start + speed * accused), 0.96),
            *_fillers(index, accused, 7),
        ]
        per_frame.append(frame)
    return SyntheticFixture(name, _ordered(per_frame), _truth(speed if not wrong_only else 1.0))


def _two_line(name: str, rival_score: float) -> SyntheticFixture:
    per_frame: list[list[CandidateMatch]] = []
    for index in range(FRAME_COUNT):
        accused = index * FRAME_INTERVAL
        frame = [
            CandidateMatch(accused, "TRUE", 100.0 + accused, 0.96),
            CandidateMatch(accused, "RIVAL", 200.0 + accused, rival_score),
            *_fillers(index, accused, 6),
        ]
        per_frame.append(frame)
    return SyntheticFixture(name, _ordered(per_frame), _truth())


def _plateau(name: str, active: set[int]) -> SyntheticFixture:
    per_frame: list[list[CandidateMatch]] = []
    for index in range(FRAME_COUNT):
        accused = index * FRAME_INTERVAL
        frame = [CandidateMatch(accused, "TRUE", 100.0 + accused, 0.96)]
        if index in active:
            frame.append(CandidateMatch(accused, "TRUE", 120.0, 0.94))
        frame.extend(_fillers(index, accused, TOP_K - len(frame)))
        per_frame.append(frame)
    return SyntheticFixture(name, _ordered(per_frame), _truth())


def build_fixture(name: str) -> SyntheticFixture:
    if name == "EXTRACTION_LOSS":
        return _extraction_loss()
    if name == "CLEAN_1_0X":
        return _base_line(name, 1.0)
    if name == "CLEAN_0_8X":
        return _base_line(name, 0.8)
    if name == "CLEAN_1_25X":
        return _base_line(name, 1.25)
    if name == "TWO_STRONG_RIVALS":
        return _two_line(name, 0.96)
    if name == "COHERENT_WRONG_LINE":
        return _base_line(name, 1.0, wrong_only=True)
    if name == "PARALLEL_COMPETITOR":
        return _two_line(name, 0.955)
    if name == "DUPLICATE_TIMESTAMPS":
        return _plateau(name, set(range(0, 16, 2)))
    if name == "LOCAL_PLATEAU":
        return _plateau(name, set(range(4, 12)))
    raise ValueError(f"unknown synthetic fixture: {name}")
