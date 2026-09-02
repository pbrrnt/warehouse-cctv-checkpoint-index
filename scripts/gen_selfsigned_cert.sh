#!/usr/bin/env bash
# ============================================================
#  gen_selfsigned_cert.sh - create a self-signed TLS cert for the web UI (nginx)
#
#  Runs on the target server (Linux) before `docker compose --profile app up`.
#  See docs/06-pdpa-compliance.md section 7 (HTTPS on LAN, self-signed OK) and
#  docs/09-multi-site-runbook.md.
#
#  ASCII-only on purpose (no Thai) - shell scripts created on Windows can pick
#  up CRLF/encoding issues; keeping this pure ASCII + LF avoids that (same
#  spirit as the .ps1 BOM / alembic.ini lessons). Thai explanation lives in the
#  runbook instead.
#
#  NOTE: this is meant to run on the Linux server. Testing it under Git Bash on
#  Windows fails because MSYS rewrites the leading-slash `-subj "/CN=..."` into a
#  fake C:/... path. That does NOT happen on Linux. If you must test on Windows,
#  prefix the run with `MSYS_NO_PATHCONV=1`.
#
#  The cert lists every hostname/IP the web UI is reached by as a SAN, so the
#  browser trusts it for LAN access AND the Tailscale hostname. Pass each as an
#  argument (IP or DNS name - the script figures out which).
#
#  Usage:
#    scripts/gen_selfsigned_cert.sh <out_dir> <san1> [san2 ...]
#  Example:
#    scripts/gen_selfsigned_cert.sh /data/certs 192.168.1.20 wh01-index.tailXXXX.ts.net
# ============================================================
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <out_dir> <san1> [san2 ...]" >&2
  echo "  san = LAN IP and/or Tailscale hostname the web UI is reached by" >&2
  exit 2
fi

OUT_DIR="$1"
shift

CERT="$OUT_DIR/web.crt"
KEY="$OUT_DIR/web.key"
DAYS=3650  # ~10 years - re-issuing is manual and this is a private LAN cert

mkdir -p "$OUT_DIR"

# Build the SAN list. An entry that looks like an IPv4 address becomes IP:...,
# everything else becomes DNS:...
SAN_ENTRIES=""
for san in "$@"; do
  if [[ "$san" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    SAN_ENTRIES="${SAN_ENTRIES}IP:${san},"
  else
    SAN_ENTRIES="${SAN_ENTRIES}DNS:${san},"
  fi
done
SAN_ENTRIES="${SAN_ENTRIES%,}"  # trim trailing comma

# First SAN doubles as the certificate CN (older clients still read CN).
FIRST_SAN="$1"

echo "Generating self-signed cert:"
echo "  out:  $CERT , $KEY"
echo "  SANs: $SAN_ENTRIES"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$KEY" \
  -out "$CERT" \
  -days "$DAYS" \
  -subj "/CN=${FIRST_SAN}/O=Warehouse CCTV Index" \
  -addext "subjectAltName=${SAN_ENTRIES}"

chmod 600 "$KEY"
echo "done. mount \$OUT_DIR at /etc/nginx/certs (see docker-compose.yml web service)"
