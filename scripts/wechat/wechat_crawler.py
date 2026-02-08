import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
import yaml

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler_common.config_entry import DEFAULT_PROJECT_CONFIG, resolve_legacy_config_for_cli

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - helpful runtime hint
    print("Missing dependency 'beautifulsoup4'. Install it with: pip install beautifulsoup4", file=sys.stderr)
    sys.exit(1)

# WeChat article list API (requires cookie/token/fakeid)
APPMSG_URL = "https://mp.weixin.qq.com/cgi-bin/appmsg"
MIN_REQUEST_INTERVAL = 12  # seconds between any two HTTP requests
REQUEST_TIMEOUT_SEC = 30
_last_request_ts = 0.0


def flatten_whitespace(text: str) -> str:
    """Collapse whitespace to keep content on one line."""
    return re.sub(r"\s+", " ", text).strip()


def delayed_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """Throttle GET requests to maintain a minimum interval."""
    global _last_request_ts
    kwargs.setdefault("timeout", REQUEST_TIMEOUT_SEC)
    now = time.time()
    wait = MIN_REQUEST_INTERVAL - (now - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    resp = session.get(url, **kwargs)
    _last_request_ts = time.time()
    return resp


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate basic credentials and normalize multiple account entries."""
    cfg: Dict[str, Any] = {}
    for key in ("cookie", "user_agent", "token"):
        val = raw.get(key)
        if val is None:
            raise ValueError(f"Missing `{key}` in config file")
        if isinstance(val, str):
            val = val.strip().strip("\"").strip("'")
        cfg[key] = str(val)

    accounts_raw = raw.get("accounts") or []
    if not accounts_raw:
        raise ValueError("Missing `accounts` list in config file")

    accounts: List[Dict[str, str]] = []
    for idx, acc in enumerate(accounts_raw, start=1):
        if not isinstance(acc, dict):
            raise ValueError(f"accounts[{idx}] must be a mapping with name/fakeid")
        name = str(acc.get("name", "")).strip().strip("\"").strip("'")
        fakeid_val = acc.get("fakeid")
        if fakeid_val is None:
            raise ValueError(f"accounts[{idx}] is missing `fakeid`")
        fakeid = str(fakeid_val).strip().strip("\"").strip("'").rstrip("&")
        if not fakeid:
            raise ValueError(f"accounts[{idx}] has empty `fakeid` after trimming")
        accounts.append({"name": name or f"account_{idx}", "fakeid": fakeid})

    cfg["accounts"] = accounts
    since_raw = raw.get("since")  # Optional: YYYY-MM-DD HH:MM:SS
    if since_raw is not None:
        cfg["since"] = str(since_raw).strip().strip("\"").strip("'")

    return cfg


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return normalize_config(data)


def build_session(cfg: Dict[str, Any]) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": cfg["user_agent"],
            "Cookie": cfg["cookie"],
            "Referer": f"https://mp.weixin.qq.com/cgi-bin/appmsg?token={cfg['token']}&lang=zh_CN",
            "Host": "mp.weixin.qq.com",
            "Accept": "application/json, text/plain, */*",
        }
    )
    return session


def interpret_auth_error(err_msg: str) -> str:
    msg = (err_msg or "").lower()
    if "token" in msg:
        return "token may be expired/invalid"
    if "cookie" in msg or "login" in msg or "session" in msg or "credential" in msg:
        return "cookie or login session may be expired"
    if "fakeid" in msg or "bizuin" in msg:
        return "fakeid may be incorrect"
    return "credential may be expired or params are invalid"


def fetch_article_page(session: requests.Session, url: str) -> str:
    """Fetch article HTML with simple retries to mitigate transient 5xx errors."""
    for attempt in range(3):
        resp = delayed_get(session, url)
        if resp.status_code == 200:
            return resp.text
        # Retry only on transient server/network errors
        if resp.status_code >= 500 and attempt < 2:
            continue
        break
    raise RuntimeError(f"Failed to fetch article page ({resp.status_code}): {resp.text[:200]}")


def parse_article(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    name_node = soup.select_one("#js_name") or soup.select_one(".profile_nickname")
    account_name = name_node.get_text(strip=True) if name_node else ""

    content_node = soup.select_one("#js_content")
    raw_content = content_node.get_text("\n", strip=True) if content_node else ""
    content_text = flatten_whitespace(raw_content)

    return account_name, content_text


def build_article_from_msg(session: requests.Session, msg: Dict[str, Any], attempts: int = 3) -> Dict[str, str]:
    """Fetch and validate article fields; retry if any required field is missing."""
    link = msg.get("link", msg.get("content_url", ""))
    if not link:
        raise RuntimeError("Latest article does not include a link/content_url.")

    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            html = fetch_article_page(session, link)
            account_name, content_text = parse_article(html)
            title = msg.get("title", "")
            create_time = datetime.fromtimestamp(int(msg.get("create_time", 0))).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if not (title and link and account_name and content_text):
                raise RuntimeError("Missing required field after fetch/parse")
            return {
                "title": title,
                "link": link,
                "create_time": create_time,
                "account_name": account_name,
                "content": content_text,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    raise RuntimeError(f"Failed to build article after {attempts} attempts: {last_error}")


def fetch_articles(
    session: requests.Session,
    cfg: Dict[str, Any],
    fakeid: str,
    since_ts: int | None,
    account_name: str,
) -> List[Dict[str, str]]:
    """
    Fetch articles. If since_ts is None, only return the latest one.
    Otherwise paginate until reaching articles older than since_ts.
    """
    if since_ts is None:
        params = {
            "action": "list_ex",
            "begin": 0,
            "count": 1,
            "fakeid": fakeid,
            "type": 9,
            "token": cfg["token"],
            "lang": "zh_CN",
            "f": "json",
        }
        resp = delayed_get(session, APPMSG_URL, params=params)
        if resp.status_code in (401, 403):
            raise RuntimeError("HTTP auth failed (cookie or token may be expired)")
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        data: Dict[str, Any] = resp.json()
        base_resp = data.get("base_resp") or {}
        ret = base_resp.get("ret")
        if ret not in (0, "0", None):
            err_msg = base_resp.get("err_msg", "")
            hint = interpret_auth_error(err_msg)
            raise RuntimeError(f"ret={ret}, msg={err_msg} ({hint})")

        app_msg_list = data.get("app_msg_list") or []
        if not app_msg_list:
            raise RuntimeError("No articles found for the provided fakeid/token combination.")

        msg = app_msg_list[0]
        print(f"[{account_name}] fetching latest article...")
        article = build_article_from_msg(session, msg)
        return [article]

    articles: List[Dict[str, str]] = []
    begin = 0
    count = 10
    processed = 0
    while True:
        params = {
            "action": "list_ex",
            "begin": begin,
            "count": count,
            "fakeid": fakeid,
            "type": 9,
            "token": cfg["token"],
            "lang": "zh_CN",
            "f": "json",
        }
        resp = delayed_get(session, APPMSG_URL, params=params)
        if resp.status_code in (401, 403):
            raise RuntimeError("HTTP auth failed (cookie or token may be expired)")
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        data: Dict[str, Any] = resp.json()
        base_resp = data.get("base_resp") or {}
        ret = base_resp.get("ret")
        if ret not in (0, "0", None):
            err_msg = base_resp.get("err_msg", "")
            hint = interpret_auth_error(err_msg)
            raise RuntimeError(f"ret={ret}, msg={err_msg} ({hint})")

        app_msg_list = data.get("app_msg_list") or []
        if not app_msg_list:
            break

        for idx, msg in enumerate(app_msg_list, start=1):
            processed += 1
            print(f"[{account_name}] fetching article #{processed} (page_offset={begin}, idx={idx})")
            create_time = int(msg.get("create_time", 0))
            if create_time <= since_ts:
                return articles  # older than cutoff; stop pagination
            try:
                article = build_article_from_msg(session, msg)
                articles.append(article)
            except Exception:
                print(f"[{account_name}] skipped an article due to fetch/parse error")
                continue

        if len(app_msg_list) < count:
            break
        begin += count

    return articles


def write_json(articles: List[Dict[str, str]], json_path: str) -> None:
    path = Path(json_path)
    path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")


def filter_articles_by_since(articles: List[Dict[str, str]], since_ts: int | None) -> List[Dict[str, str]]:
    if since_ts is None:
        return articles
    filtered: List[Dict[str, str]] = []
    for art in articles:
        create_time = art.get("create_time", "")
        try:
            ts = int(datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S").timestamp())
        except Exception:
            continue
        if ts > since_ts:
            filtered.append(art)
    return filtered


def parse_since(since: str | None) -> int | None:
    if not since:
        return None
    try:
        dt = datetime.strptime(since.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError("Invalid since format, expected YYYY-MM-DD HH:MM:SS") from exc
    return int(dt.timestamp())


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch latest WeChat articles for multiple accounts")
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_PROJECT_CONFIG,
        help="配置入口（统一配置 configs/config.yaml 或 legacy wechat.yaml）",
    )
    parser.add_argument("--profile", default="", help="配置环境(profile)，仅统一配置生效")
    parser.add_argument(
        "-o",
        "--csv",
        default="",
        help="JSON output path (default: paper.json in current folder)",
    )
    args = parser.parse_args()

    try:
        cfg_path = resolve_legacy_config_for_cli(args.config, kind="wechat", profile=args.profile)
        cfg = load_config(cfg_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to read config: {exc}", file=sys.stderr)
        sys.exit(1)

    # since is read from config; adjust default here if needed
    since_value = cfg.get("since", "")
    try:
        since_ts = parse_since(since_value)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    # Resolve JSON output path; default to a stable name in current folder
    json_path = args.csv or "paper.json"

    session = build_session(cfg)

    success = 0
    all_articles: List[Dict[str, str]] = []
    print(f"Loaded {len(cfg['accounts'])} account(s). Start crawling...")
    for account in cfg["accounts"]:
        name = account["name"]
        print(f"[{name}] requesting article list...")
        try:
            articles = fetch_articles(session, cfg, account["fakeid"], since_ts, name)
            if not articles:
                print(f"[{name}] no articles after cutoff; skipped")
                continue
            filtered = filter_articles_by_since(articles, since_ts)
            if not filtered:
                print(f"[{name}] no articles after filter; skipped")
                continue
            all_articles.extend(filtered)
            success += 1
            print(f"[{name}] queued {len(filtered)} article(s) for JSON -> {json_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] failed: {exc}", file=sys.stderr)

    if all_articles:
        write_json(all_articles, json_path)
        print(f"Saved {len(all_articles)} article(s) to JSON -> {json_path}")

    if success == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
