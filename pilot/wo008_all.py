"""Evidence-only entry point. Run wo008_finalize separately after D-043 pilot validation."""
from .wo008_runner import main as evidence_main


if __name__ == "__main__":
    evidence_main()
