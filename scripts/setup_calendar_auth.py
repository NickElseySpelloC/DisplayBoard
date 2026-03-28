#!/usr/bin/env python3
"""One-time Google Calendar OAuth setup for DisplayBoard.

Run this script once per Google account on a machine with a web browser.
It will open Google's consent page, and save an OAuth token file that the
main application uses for headless / SSH-deployed operation.

Usage:
    python src/setup_calendar_auth.py --account <name>
    python src/setup_calendar_auth.py --account Personal --credentials google_credentials.json --tokens-dir tokens

Steps:
  1. Download your OAuth 2.0 client secrets JSON from Google Cloud Console:
       APIs & Services → Credentials → Create Credentials → OAuth client ID
       (Application type: Desktop app)
     Save it as 'google_credentials.json' in the project root.
  2. Run this script for each account configured in config.yaml.
  3. Copy the generated tokens/<name>.json file to the same path on the server.
     The main app will auto-refresh the token silently from then on.
"""

import argparse
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Authorise a Google Calendar account for DisplayBoard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--account",
        required=True,
        help="Account name — must match the 'Name' field in config.yaml TopicCalendar.Accounts",
    )
    parser.add_argument(
        "--credentials",
        default="google_credentials.json",
        help="Path to the OAuth client secrets JSON downloaded from Google Cloud Console (default: google_credentials.json)",
    )
    parser.add_argument(
        "--tokens-dir",
        default="tokens",
        help="Directory to store the resulting token file (default: tokens/)",
    )
    args = parser.parse_args()

    creds_path = Path(args.credentials)
    if not creds_path.exists():
        print(f"ERROR: Credentials file not found: {creds_path}", file=sys.stderr)
        print(
            "Download it from Google Cloud Console → APIs & Services → Credentials.\n"
            "Create an OAuth Client ID (Desktop app) and save the JSON here.",
            file=sys.stderr,
        )
        sys.exit(1)

    tokens_dir = Path(args.tokens_dir)
    tokens_dir.mkdir(parents=True, exist_ok=True)
    token_path = tokens_dir / f"{args.account}.json"

    print(f"Authorising Google Calendar account: '{args.account}'")
    print("A browser window will open. Sign in and grant calendar read access.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    print(f"\nAuthorisation successful. Token saved to: {token_path}")
    print()
    print("If running on a remote server, copy this file to the same path on the server:")
    print(f"  scp {token_path} user@server:{token_path}")


if __name__ == "__main__":
    main()
