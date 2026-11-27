"""Make the operator panel's password, its hash and the session secret (run on the operator's own PC).

    python tools/make_admin_password.py            # generate a random 32-character password
    python tools/make_admin_password.py --choose   # type your own (16-128 printable ASCII characters)

Prints the password once (put it in your password manager) and the two values to paste into Coolify's env vars:
WEB_ADMIN_PASSWORD_HASH and WEB_SESSION_SECRET. The hash is hex and ':' only, so nothing gets interpolated.
"""
import argparse
import getpass
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_admin import hash_password, valid_password_text  # noqa: E402


def make(password: str | None = None) -> dict:
    if password is None:
        password = secrets.token_urlsafe(24)
    if not valid_password_text(password):
        raise ValueError("The password must be 16 to 128 printable ASCII characters (spaces are fine).")
    return {"password": password, "hash": hash_password(password), "secret": secrets.token_hex(32)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--choose", action="store_true", help="type your own password instead of generating one")
    args = parser.parse_args(argv)
    password = None
    if args.choose:
        password = getpass.getpass("Password (16-128 characters): ")
        if password != getpass.getpass("Again: "):
            print("The two passwords differ.", file=sys.stderr)
            return 1
    try:
        values = make(password)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not args.choose:
        print(f"Password (shown once, save it now): {values['password']}")
    print(f"WEB_ADMIN_PASSWORD_HASH={values['hash']}")
    print(f"WEB_SESSION_SECRET={values['secret']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
