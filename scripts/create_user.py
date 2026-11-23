#!/usr/bin/env python3
"""
CLI script to create users with role 'admin' or 'viewer'.
Usage:
    python3 scripts/create_user.py --username admin --password secret --name "Plant Admin" --role admin
Or run interactively:
    python3 scripts/create_user.py
"""

import sys
import os
import argparse
import getpass

# Add parent directory to path so core imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.database import get_engine, initialize_database
from core.auth import create_user
from core.settings import initialize_settings


def main():
    parser = argparse.ArgumentParser(description="Create a user for the Recon Dashboard")
    parser.add_argument("--username", "-u", help="Username")
    parser.add_argument("--password", "-p", help="Password")
    parser.add_argument("--name", "-n", help="Full name")
    parser.add_argument("--role", "-r", choices=["admin", "viewer"], default="viewer", help="Role (admin/viewer)")
    parser.add_argument("--db", default="data/reconciliation.db", help="Path to SQLite DB")

    args = parser.parse_args()

    engine = get_engine(args.db)
    initialize_database(engine)
    initialize_settings(engine)

    username = args.username
    password = args.password
    name = args.name
    role = args.role

    if not username:
        username = input("Enter username: ").strip()
    if not name:
        name = input(f"Enter full name [{username}]: ").strip() or username
    if not password:
        password = getpass.getpass("Enter password: ").strip()
        confirm = getpass.getpass("Confirm password: ").strip()
        if password != confirm:
            print("Error: Passwords do not match.")
            sys.exit(1)
    if not args.role and "--role" not in sys.argv and "-r" not in sys.argv:
        role_input = input("Enter role (admin/viewer) [viewer]: ").strip().lower()
        if role_input in ("admin", "viewer"):
            role = role_input

    success, message = create_user(
        engine,
        username=username,
        password=password,
        full_name=name,
        role=role,
        created_by="cli",
    )

    if success:
        print(f"Success: {message} (role={role})")
    else:
        print(f"Error: {message}")
        sys.exit(1)


if __name__ == "__main__":
    main()

