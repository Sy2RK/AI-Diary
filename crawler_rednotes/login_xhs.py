import argparse
import time
import threading
from pathlib import Path


def main():
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Missing dependency 'playwright'. Install it with: pip install playwright") from exc

    parser = argparse.ArgumentParser(description="Login to Xiaohongshu and save Playwright storage_state.json")
    parser.add_argument(
        "-o",
        "--output",
        default=str(Path(__file__).resolve().parent / "storage_state.json"),
        help="Output storage_state.json path (default: crawler_rednotes/storage_state.json)",
    )
    args = parser.parse_args()

    storage_path = Path(args.output)
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    print("Opening browser... Please login to Xiaohongshu manually.")
    print("After login, press Enter in this terminal to save the session (recommended).")
    print("You can also close the browser window to trigger saving, but it may fail if the browser exits too fast.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context()
        page = context.new_page()
        try:
            try:
                page.goto("https://www.xiaohongshu.com/", wait_until="networkidle", timeout=120000)
            except Exception as exc:
                print(f"Initial load warning (will continue): {exc}")
                print("If the page loaded in browser, ignore this warning and continue login.")
            print("Login in browser. When done, press Enter here to save the session.")

            done = threading.Event()

            def _wait_for_enter() -> None:
                try:
                    input("Press Enter to save storage_state.json ... ")
                except EOFError:
                    pass
                done.set()

            threading.Thread(target=_wait_for_enter, daemon=True).start()
            page.on("close", lambda: done.set())

            # Wait until user confirms or browser/page is closed.
            while not done.is_set() and browser.is_connected():
                time.sleep(0.5)

            # Save storage state if possible (best-effort).
            try:
                context.storage_state(path=str(storage_path))
                print(f"Saved login state to {storage_path.resolve()}")
            except Exception as exc:
                print(f"Could not save storage state: {exc}")
                print("Tip: rerun login and press Enter in terminal after you finish logging in (before closing browser).")
        except Exception as exc:
            print(f"Login flow encountered an error: {exc}")
        finally:
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
