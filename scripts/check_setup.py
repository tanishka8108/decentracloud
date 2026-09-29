"""
check_setup.py — diagnose Google Cloud Storage configuration.

Run:  python scripts/check_setup.py

It prints the configured backend, checks credentials, checks that the
three bucket env vars are set, and probes each bucket for existence and
permission. Use it whenever uploads fail or the dashboard shows a
"storage setup needs attention" warning.
"""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE, ".env"))  # load BEFORE importing storage

import storage  # noqa: E402


def main():
    print("=" * 64)
    print("DecentraCloud — storage setup check")
    print("=" * 64)
    print("backend       : {}".format(storage.backend_name()))
    print("project       : {}".format(os.getenv("GOOGLE_CLOUD_PROJECT") or "(not set)"))
    print("credentials   : {}".format(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or
        "(unset — will use `gcloud auth application-default login` if available)"))

    issues = storage.config_issues()
    if issues:
        print("\nCONFIGURATION PROBLEMS:")
        for issue in issues:
            print("  [!] {}".format(issue))
    else:
        print("\nconfiguration : OK")

    if storage.BACKEND == "local":
        print("\nLocal folders:")
        for node in storage.describe_nodes():
            exists = os.path.isdir(node["target"])
            print("  {:<8} -> {} ({})".format(
                node["label"], node["target"], "exists" if exists else "will be created"))
        return

    # Live probe of the three buckets
    try:
        from google.cloud import storage as gcs
    except Exception as exc:  # noqa: BLE001
        print("\nCannot import google-cloud-storage: {}".format(exc))
        return

    try:
        client = storage._get_client()
    except Exception as exc:  # noqa: BLE001
        print("\nCannot create a Google Cloud client: {}".format(exc))
        return

    print("\nBucket probes:")
    for label, bucket_name in storage.GCS_NODES:
        if not bucket_name:
            print("  {:<8} -> NOT CONFIGURED (set its env var)".format(label))
            continue
        try:
            bucket = client.bucket(bucket_name)
            if not bucket.exists():
                print("  {:<8} -> MISSING bucket '{}' — create it (see README)".format(
                    label, bucket_name))
                continue
            bucket.reload()  # proves we have at least read permission
            print("  {:<8} -> OK  gs://{}".format(label, bucket_name))
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "403" in text or "Permission" in text or "permission" in text:
                # This probe itself needs storage.buckets.get (Storage Bucket
                # Viewer). The app only needs object permissions, so this is
                # just a diagnostic limitation — not necessarily an app error.
                print("  {:<8} -> bucket exists, but this probe lacks "
                      "'storage.buckets.get' (grant Storage Bucket Viewer "
                      "to silence this note): {}".format(label, text[:120]))
            else:
                print("  {:<8} -> ERROR: {}".format(label, exc))

    print("\nDone. Fix any [!] / MISSING / ERROR line above, then re-run this script.")


if __name__ == "__main__":
    main()
