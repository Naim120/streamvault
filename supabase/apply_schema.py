#!/usr/bin/env python3
"""
StreamVault Supabase Database Schema Initializer & Validator
=============================================================
Automates the execution of supabase/setup.sql against a remote Supabase project
using either:
  1) Supabase Management API (Access Token sbp_...)
  2) Direct / Pooler PostgreSQL connection (Database Password or Connection URI)
And verifies schema readiness via the PostgREST API.
"""

import sys
import os
import argparse
import ssl
import json
import urllib.parse
from pathlib import Path

# Attempt importing required libraries
try:
    import httpx
except ImportError:
    httpx = None

try:
    import pg8000.native
except ImportError:
    pg8000 = None


def extract_project_ref(url_or_ref: str) -> str:
    """Extract 20-character project ref from a Supabase URL or raw ref."""
    clean = url_or_ref.strip()
    if "://" in clean:
        parsed = urllib.parse.urlparse(clean)
        host = parsed.netloc or parsed.path
        return host.split(".")[0]
    return clean.split(".")[0]


def verify_tables(supabase_url: str, apikey: str) -> bool:
    """Check if public.system_settings exists in the Supabase schema cache."""
    clean_url = supabase_url.rstrip("/")
    target = f"{clean_url}/rest/v1/system_settings?select=id&limit=1"
    headers = {
        "apikey": apikey,
        "Authorization": f"Bearer {apikey}"
    }

    if httpx:
        try:
            with httpx.Client(timeout=6.0) as client:
                res = client.get(target, headers=headers)
                return res.status_code == 200
        except Exception:
            return False
    else:
        import urllib.request
        try:
            req = urllib.request.Request(target, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                return resp.status == 200
        except Exception:
            return False


def run_via_management_api(project_ref: str, token: str, sql_content: str) -> tuple[bool, str]:
    """Execute raw SQL via Supabase Management API."""
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {"query": sql_content}

    if httpx:
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    return True, "Executed successfully via Supabase Management API."
                return False, f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return False, f"Request error: {e}"
    else:
        import urllib.request
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                if resp.status in (200, 201):
                    return True, "Executed successfully via Supabase Management API."
                return False, f"HTTP {resp.status}"
        except Exception as e:
            return False, f"Request error: {e}"


COMMON_REGIONS = [
    "ap-northeast-1", "ap-southeast-1", "ap-south-1",
    "us-east-1", "us-west-1", "eu-central-1", "eu-west-1",
    "ca-central-1", "ap-southeast-2", "sa-east-1",
    "ap-northeast-2", "eu-west-2", "eu-west-3"
]


def detect_supabase_region(project_ref: str) -> str:
    """Attempt detecting Supabase project region from db host IP geolocation."""
    try:
        import socket
        info = socket.getaddrinfo(f"db.{project_ref}.supabase.co", 5432)
        ip = info[0][4][0]
        if ":" in ip: # IPv6
            import urllib.request
            req = urllib.request.Request(f"https://ipinfo.io/{ip}/json", headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                country = data.get("country", "").upper()
                city = data.get("city", "")
                if country == "JP" or "Tokyo" in city:
                    return "ap-northeast-1"
                if country == "KR" or "Seoul" in city:
                    return "ap-northeast-2"
                if country == "SG" or "Singapore" in city:
                    return "ap-southeast-1"
                if country == "IN" or "Mumbai" in city:
                    return "ap-south-1"
                if country == "DE" or "Frankfurt" in city:
                    return "eu-central-1"
                if country == "IE" or "Dublin" in city:
                    return "eu-west-1"
                if country == "GB" or "London" in city:
                    return "eu-west-2"
                if country == "US":
                    region_name = data.get("region", "")
                    if "California" in region_name or "Oregon" in region_name:
                        return "us-west-1"
                    return "us-east-1"
    except Exception:
        pass
    return ""


def run_via_postgres_connection(conn_str_or_pwd: str, project_ref: str, sql_content: str) -> tuple[bool, str]:
    """Execute SQL via PostgreSQL connection using pg8000 or psql."""
    if not pg8000:
        return False, "pg8000 python library is not installed in the current environment."

    # Parse connection parameters
    is_uri = conn_str_or_pwd.startswith("postgres://") or conn_str_or_pwd.startswith("postgresql://")
    if is_uri:
        parsed = urllib.parse.urlparse(conn_str_or_pwd)
        user = parsed.username or "postgres"
        password = parsed.password or ""
        host = parsed.hostname or f"db.{project_ref}.supabase.co"
        port = parsed.port or 5432
        database = parsed.path.lstrip("/") or "postgres"
    else:
        user = "postgres"
        password = conn_str_or_pwd.strip()
        host = f"db.{project_ref}.supabase.co"
        port = 5432
        database = "postgres"

    # SSL Context required for Supabase
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # Step 1: Attempt direct connection (shorter timeout to fail fast on IPv6-unreachable networks)
    direct_timeout = 8 if not is_uri else 25
    try:
        print(f"Connecting to Postgres at {host}:{port}...")
        conn = pg8000.native.Connection(
            user=user,
            password=password,
            host=host,
            port=port,
            database=database,
            ssl_context=ctx,
            timeout=direct_timeout
        )
        print("Connected! Executing schema script (this may take 5-15 seconds)...")
        conn.run(sql_content)
        conn.close()
        return True, "Executed successfully via PostgreSQL direct connection."
    except Exception as e:
        direct_err = str(e)
        print(f"[NOTE] Direct connection to {host}:{port} unavailable: {direct_err}")

    # If this was already a custom pooler URI or explicit host, return the error
    if is_uri and "pooler.supabase.com" in host:
        return False, f"PostgreSQL pooler error: {direct_err}"

    # Step 2: Automatic IPv4 Supavisor connection pooler fallback
    print("Attempting automatic IPv4 Supavisor connection pooler fallback...")
    detected_region = detect_supabase_region(project_ref)
    candidate_regions = ([detected_region] if detected_region else []) + [r for r in COMMON_REGIONS if r != detected_region]

    for region in candidate_regions:
        pooler_host = f"aws-0-{region}.pooler.supabase.com"
        pooler_user = f"postgres.{project_ref}"
        try:
            print(f"Trying IPv4 connection pooler ({region})...")
            conn = pg8000.native.Connection(
                user=pooler_user,
                password=password,
                host=pooler_host,
                port=5432,
                database="postgres",
                ssl_context=ctx,
                timeout=5
            )
            print(f"Connected successfully via {region} pooler! Executing schema script...")
            conn.run(sql_content)
            conn.close()
            return True, f"Executed successfully via Supabase {region} connection pooler."
        except Exception:
            continue

    return False, (
        f"Could not connect to database via direct host ({direct_err}) or candidate IPv4 poolers.\n"
        "Please check your database password or provide your exact Supabase Session Pooler connection string\n"
        "(Supabase Dashboard -> Connect -> Session mode, e.g. postgres://postgres.<ref>:<pwd>@aws-<region>.pooler.supabase.com:5432/postgres)."
    )


def main():
    parser = argparse.ArgumentParser(description="StreamVault Supabase Database Schema Initializer")
    parser.add_argument("--url", default="", help="Supabase project URL (e.g. https://xyz.supabase.co)")
    parser.add_argument("--ref", default="", help="Supabase project reference ID")
    parser.add_argument("--password", default="", help="Database password or connection URI")
    parser.add_argument("--token", default="", help="Supabase Management Personal Access Token (sbp_...)")
    parser.add_argument("--sql-file", default="", help="Path to setup.sql file")
    parser.add_argument("--verify-only", action="store_true", help="Only verify existing tables and exit")
    parser.add_argument("--apikey", default="", help="Supabase service role or anon key for verification")

    args = parser.parse_args()

    project_ref = args.ref or extract_project_ref(args.url)
    if not project_ref:
        print("[ERROR] Please provide --url or --ref.", file=sys.stderr)
        sys.exit(1)

    supabase_url = args.url or f"https://{project_ref}.supabase.co"
    apikey = args.apikey or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""

    if args.verify_only:
        if not apikey:
            print("[ERROR] Verification requires --apikey or SUPABASE_SERVICE_ROLE_KEY env.", file=sys.stderr)
            sys.exit(1)
        if verify_tables(supabase_url, apikey):
            print("VERIFIED")
            sys.exit(0)
        else:
            print("NOT_FOUND")
            sys.exit(1)

    # Determine SQL file path
    base_dir = Path(__file__).resolve().parent.parent
    sql_path = Path(args.sql_file) if args.sql_file else base_dir / "supabase" / "setup.sql"
    if not sql_path.exists():
        print(f"[ERROR] SQL setup file not found at: {sql_path}", file=sys.stderr)
        sys.exit(1)

    with open(sql_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # Determine execution strategy
    token = args.token or (args.password if args.password.startswith("sbp_") else "")
    password = args.password if not args.password.startswith("sbp_") else ""

    success = False
    details = ""

    if token:
        print(f"Executing schema via Supabase Management API for project '{project_ref}'...")
        success, details = run_via_management_api(project_ref, token, sql_content)
    elif password:
        print(f"Executing schema via PostgreSQL connection for project '{project_ref}'...")
        success, details = run_via_postgres_connection(password, project_ref, sql_content)
    else:
        print("[ERROR] Must provide --password, full connection URI, or --token.", file=sys.stderr)
        sys.exit(1)

    if success:
        print(f"[SUCCESS] {details}")
        # Verify tables if apikey provided
        if apikey:
            print("Verifying schema cache...")
            if verify_tables(supabase_url, apikey):
                print("[VERIFIED] Core StreamVault tables (system_settings, etc.) detected!")
            else:
                print("[NOTE] Schema executed. Schema cache may take a few seconds to refresh.")
        sys.exit(0)
    else:
        print(f"[FAILED] {details}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

