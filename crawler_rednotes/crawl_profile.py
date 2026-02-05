import sys

from .cli import main as cli_main


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m crawler_rednotes crawl <profile_url>")
        print("Legacy usage: python -m crawler_rednotes.crawl_profile <profile_url>")
        return 2
    profile_url = sys.argv[1]
    sys.argv = [sys.argv[0], "crawl", profile_url]
    return int(cli_main())


if __name__ == "__main__":
    raise SystemExit(main())
