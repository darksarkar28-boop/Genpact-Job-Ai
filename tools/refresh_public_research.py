"""Refresh an employer's public research from an operator-reviewed JSON document.

Run as the web service operator, with the same JOBWEB_DATA_DIR as Django.
Never pass candidate profiles, private interview notes or application material.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from tools.shared_research_cache import ResearchCache


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("employer_id", help="Verified registry namespace and ID, e.g. IN-MCA:REGISTRY-ID")
    args = parser.parse_args()
    directory = Path(os.environ.get("JOBWEB_DATA_DIR", Path.home() / ".genpact-job-ai"))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache = ResearchCache(directory / "public_research.sqlite3")

    def read_sources(_):
        raw = sys.stdin.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("research document too large")
        return json.loads(raw)

    try:
        result = cache.get_or_refresh(args.employer_id, read_sources)
    except (ValueError, TypeError):
        parser.error("invalid registry key or public research document")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
