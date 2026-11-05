"""Generate a v4 transcript with scripted consent/replies; never contact services."""
import argparse
from pathlib import Path

from generate_stale_check_demo import build
from stalecheck.stale_check_v4 import collect_transcript
from stalecheck.export import write_exclusive_json


def build_v4():
    return build(collector=collect_transcript, approve=lambda request: True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(write_exclusive_json(build_v4(), Path(args.output)))
