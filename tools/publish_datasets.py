#!/usr/bin/env python3
"""Split a regional build into one repository per dataset.

    python tools/publish_datasets.py                 # show the plan, write nothing
    python tools/publish_datasets.py --dist dist     # write the repo trees
    python tools/publish_datasets.py --dist dist --push

A build under `data/regional/` is one directory covering a whole AOI: one res-7
overview, one manifest and 72 res-8 region shards. That is the wrong unit to
publish. The unit is the **dataset**: the overview and each region shard become
their own repository, so a shard can be rebuilt, versioned, cached and served
without touching the other 71.

The split is mechanical because the viewer addresses every payload by URL
(`manifest.regions[].url`), not by path. This tool writes the URL-stamped
manifest and a `dataset.json` per repository; the payload bytes are copied
unchanged, so the build stays byte-identical.

Layout it produces:

    dist/
      h3-overview/                      manifest.json, overview_res7.bin.gz
        dataset.json  README.md
      h3-region-8444101ffffffff/        region_..._res8.bin.gz
        dataset.json  README.md
      ... 72 region repos

`--push` initialises a git repository in each directory and pushes it to
`<owner>/<repo>`. The repositories must already exist: this tool will not
create them, because creating 73 repositories is an administrative act with a
blast radius (and needs a token this tool does not have). `--emit-create`
writes the `gh repo create` script that makes them, so the two steps are
explicit.

Addressing is a template, not a hard-coded URL:

    --url-template 'https://raw.githubusercontent.com/7yl4r/{repo}/{ref}/{file}'

`{repo}`, `{ref}` and `{file}` are substituted per dataset. A Google Cloud
bucket is the same tool with a different template:

    --url-template 'https://storage.googleapis.com/h3lab-data/{repo}/{file}'

which is the point: moving the archive off GitHub later is a change of
template and a CORS policy, not a change of format or viewer.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

DEFAULT_ARCHIVE = os.path.join("data", "regional")
DEFAULT_DIST = "dist"
DEFAULT_OWNER = "7yl4r"
DEFAULT_TEMPLATE = "https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{file}"
DEFAULT_REF = "main"
DEFAULT_OVERVIEW_REPO = "h3-overview"
DEFAULT_REGION_PREFIX = "h3-region"
FORMAT = "h3lb/1"


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True, separators=(",", ":"), indent=None)
        fh.write("\n")


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:,.0f} B" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n:,.1f} GiB"


def repo_names(manifest, overview_repo, region_prefix):
    """-> (overview repo, {region: repo})"""
    regions = {r["region"]: f"{region_prefix}-{r['region']}"
               for r in manifest.get("regions", [])}
    return overview_repo, regions


def url_for(template, owner, repo, ref, filename):
    return (template.replace("{owner}", owner).replace("{repo}", repo)
            .replace("{ref}", ref).replace("{file}", filename))


def stamped_manifest(manifest, owner, overview_repo, regions, template, ref):
    """A copy of the manifest whose payloads are addressed absolutely."""
    out = json.loads(json.dumps(manifest))    # deep copy, keep key order
    ov = out.get("overview")
    if ov:
        ov["repo"] = overview_repo
        ov["url"] = url_for(template, owner, overview_repo, ref, ov["file"])
    for entry in out.get("regions", []):
        repo = regions[entry["region"]]
        entry["repo"] = repo
        entry["url"] = url_for(template, owner, repo, ref, entry["file"])
    out["publish"] = {
        "mode": "one-repository-per-dataset",
        "owner": owner,
        "ref": ref,
        "url_template": template,
        "overview_repo": overview_repo,
        "region_repo_prefix": "h3-region",
    }
    return out


def dataset_doc(entry, kind, repo, manifest):
    """The self-description each repository carries."""
    doc = {
        "format": FORMAT,
        "kind": kind,
        "repo": repo,
        "resolution": entry.get("res"),
        "nCells": entry.get("nCells"),
        "nPeriods": entry.get("nPeriods"),
        "payload": entry.get("file"),
        "bytes": entry.get("bytes"),
        "hash": entry.get("hash"),
        "bbox": entry.get("bbox"),
        "built": {
            "pipeline": "h3lab",
            "epoch": manifest.get("epoch"),
            "bin_days": manifest.get("bin_days"),
            "source": manifest.get("source"),
        },
    }
    if kind == "region":
        doc["region"] = entry.get("region")
        doc["lon"] = entry.get("lon")
        doc["lat"] = entry.get("lat")
    else:
        doc["name"] = manifest.get("name")
        doc["manifest"] = "manifest.json"
        doc["periods"] = len(manifest.get("periods") or [])
        doc["regions"] = len(manifest.get("regions") or [])
    return doc


def readme(kind, repo, doc, entry, manifest):
    if kind == "overview":
        what = (f"Res-{doc['resolution']} overview for **{doc['name']}**: "
                f"{doc['nCells']:,} cells across {doc['nPeriods']} periods.")
        index = ("This repository is also the **index**: `manifest.json` lists "
                 "every region repository and the absolute URL of its payload, "
                 "so a viewer needs only this one URL to start.\n\n")
    else:
        what = (f"Res-{doc['resolution']} cells for H3 res-4 region "
                f"`{doc['region']}`: {doc['nCells']:,} cells across "
                f"{doc['nPeriods']} periods.")
        index = ("This repository is one shard. Its neighbours are separate "
                 "repositories; `manifest.json` in the overview repository "
                 "lists them all.\n\n")
    return (
        f"# {repo}\n\n{what}\n\n{index}"
        f"Payload: `{doc['payload']}` ({human(doc['bytes'])}, "
        f"{FORMAT} block, gzipped). `dataset.json` describes it; the block's "
        f"own descriptor carries periods, bands and cells.\n\n"
        f"Built by [h3lab](https://github.com/7yl4r/h3lab) from "
        f"{manifest.get('source', 'Landsat Collection 2 Level-2')} via "
        f"{manifest.get('via', 'the Microsoft Planetary Computer STAC API')}. "
        f"{manifest.get('note', '')}\n\n"
        f"Address: `{entry.get('url', 'not stamped')}`\n"
    )


def plan(archive, overview_repo, regions):
    """-> (manifest, [(repo, kind, filename, bytes, entry)])"""
    manifest = read_json(os.path.join(archive, "manifest.json"))
    out = []
    ov = manifest.get("overview")
    if ov:
        out.append((overview_repo, "overview", ov["file"], ov["bytes"], ov))
    for entry in manifest.get("regions", []):
        out.append((regions[entry["region"]], "region", entry["file"],
                    entry["bytes"], entry))
    return manifest, out


def stage(archive, dist, manifest, items, owner, overview_repo, regions,
          template, ref):
    """Write one directory per dataset. Returns (repo, path, nfiles, bytes)."""
    stamped = stamped_manifest(manifest, owner, overview_repo, regions,
                               template, ref)
    if os.path.isdir(dist):
        shutil.rmtree(dist)
    os.makedirs(dist, exist_ok=True)

    written = []
    for repo, kind, filename, size, entry in items:
        target = os.path.join(dist, repo)
        os.makedirs(target, exist_ok=True)
        shutil.copy2(os.path.join(archive, filename),
                     os.path.join(target, filename))
        if kind == "overview":
            # The overview repository is the index, so it carries the
            # URL-stamped manifest.
            write_json(os.path.join(target, "manifest.json"), stamped)
        doc = dataset_doc(entry, kind, repo, manifest)
        write_json(os.path.join(target, "dataset.json"), doc)
        with open(os.path.join(target, "README.md"), "w", encoding="utf-8") as fh:
            fh.write(readme(kind, repo, doc, entry, manifest))
        written.append((repo, target, size))
    return written


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def git_push(target, repo, owner, message):
    """Initialise (if needed), commit and push one dataset repository."""
    if not os.path.isdir(os.path.join(target, ".git")):
        run(["git", "init", "-q", "-b", "main"], cwd=target)
    run(["git", "add", "-A"], cwd=target)
    diff = subprocess.run(["git", "status", "--porcelain"], cwd=target,
                          text=True, stdout=subprocess.PIPE)
    if diff.stdout.strip():
        run(["git", "commit", "-q", "-m", message], cwd=target)
    url = f"git@github.com:{owner}/{repo}.git"
    remotes = subprocess.run(["git", "remote"], cwd=target, text=True,
                             stdout=subprocess.PIPE).stdout.split()
    if "origin" in remotes:
        run(["git", "remote", "set-url", "origin", url], cwd=target)
    else:
        run(["git", "remote", "add", "origin", url], cwd=target)
    run(["git", "push", "-q", "-u", "origin", "main"], cwd=target)


def emit_create_script(path, items, owner):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    lines = ["#!/usr/bin/env bash",
             "# Create the dataset repositories, then push them.",
             "# Needs `gh` authenticated as an account that can create repos.",
             "set -euo pipefail",
             ""]
    for repo, _kind, _f, _b, _e in items:
        lines.append(f"gh repo create {owner}/{repo} --public "
                     f"--description 'h3lab dataset: {repo}' || true")
    lines.append("")
    lines.append("# then: python tools/publish_datasets.py --dist dist --push")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(path, 0o755)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE,
                    help=f"build output to split (default {DEFAULT_ARCHIVE})")
    ap.add_argument("--dist", default=None,
                    help="write the per-repo trees here (default: plan only)")
    ap.add_argument("--owner", default=DEFAULT_OWNER)
    ap.add_argument("--ref", default=DEFAULT_REF,
                    help="git ref the URLs point at (default main)")
    ap.add_argument("--url-template", default=DEFAULT_TEMPLATE,
                    help="payload URL pattern; {owner} {repo} {ref} {file}")
    ap.add_argument("--overview-repo", default=DEFAULT_OVERVIEW_REPO)
    ap.add_argument("--region-prefix", default=DEFAULT_REGION_PREFIX)
    ap.add_argument("--push", action="store_true",
                    help="git init/commit/push each staged repository")
    ap.add_argument("--emit-create", default=None,
                    help="write a gh repo create script to this path")
    ap.add_argument("--message", default="publish dataset",
                    help="commit message for --push")
    args = ap.parse_args()

    if not os.path.exists(os.path.join(args.archive, "manifest.json")):
        print(f"no manifest in {args.archive}; build the archive first",
              file=sys.stderr)
        return 2

    manifest = read_json(os.path.join(args.archive, "manifest.json"))
    _, regions = repo_names(manifest, args.overview_repo, args.region_prefix)
    manifest, items = plan(args.archive, args.overview_repo, regions)

    total = sum(size for _r, _k, _f, size, _e in items)
    largest = max(items, key=lambda x: x[3])
    print(f"archive: {args.archive}")
    print(f"  datasets: {len(items)}  ({len(items) - 1} regions + 1 overview)")
    print(f"  payload:  {total:,} B ({human(total)})")
    print(f"  largest:  {largest[2]} {largest[3]:,} B ({human(largest[3])})")
    print(f"  address:  {args.url_template}")
    print(f"            ref {args.ref}")
    print(f"  overview: {args.overview_repo}  (carries manifest.json)")
    print(f"  regions:  {args.region_prefix}-<res4 parent>")
    print()
    for repo, kind, filename, size, entry in items[:3]:
        print(f"  {kind:9} {repo:32} {filename:40} {human(size)}")
    if len(items) > 5:
        print(f"  ... {len(items) - 5} more ...")
    for repo, kind, filename, size, entry in items[-2:]:
        print(f"  {kind:9} {repo:32} {filename:40} {human(size)}")

    if args.emit_create:
        emit_create_script(args.emit_create, items, args.owner)
        print(f"\nwrote {args.emit_create}")

    if not args.dist:
        print("\nplan only: pass --dist dist to write the repository trees")
        return 0

    written = stage(args.archive, args.dist, manifest, items, args.owner,
                    args.overview_repo, regions, args.url_template, args.ref)
    print(f"\nwrote {len(written)} repositories under {args.dist}:")
    for repo, target, size in written[:3]:
        print(f"  {target}/  ({human(size)} payload)")
    if len(written) > 3:
        print(f"  ... {len(written) - 3} more ...")

    if not args.push:
        print("\nstaged only: pass --push to commit and push")
        return 0

    for i, (repo, target, _size) in enumerate(written, 1):
        print(f"[{i}/{len(written)}] push {repo}")
        try:
            git_push(target, repo, args.owner, args.message)
        except subprocess.CalledProcessError as exc:
            print(f"  failed: {exc.stdout.strip() if exc.stdout else exc}",
                  file=sys.stderr)
            print(f"  create it first: gh repo create {args.owner}/{repo} --public",
                  file=sys.stderr)
            return 1
    print(f"\npushed {len(written)} repositories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
