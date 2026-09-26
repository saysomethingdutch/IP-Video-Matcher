"""One background entry point for both WO-008 batches and blind material."""
from .wo008_runner import main as evidence_main
from .wo008_finalize import main as finalize_main


if __name__ == "__main__":
    evidence_main()
    finalize_main()
