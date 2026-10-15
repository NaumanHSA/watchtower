"""MkDocs hook: keep links from the guides to the rest of the repository working on the site.

The guides link to code and top-level files with relative paths such as
``../deploy/compose`` so they work when read on GitHub. Those files are not part of the
site, so on the site the links are pointed at the same paths on GitHub instead.
"""

import os
import posixpath
import re

REPO = "https://github.com/NaumanHSA/watchtower"
BRANCH = "main"

# [text](../path#anchor) — a Markdown link whose target leaves docs/.
_LINK = re.compile(r"\]\((?P<path>\.\./[^)\s#]+)(?P<anchor>#[^)\s]*)?\)")


def _to_github(match: re.Match, page_dir: str, root: str) -> str:
    target = posixpath.normpath(posixpath.join("docs", page_dir, match.group("path")))
    if target == "docs" or target.startswith("docs/"):
        return match.group(0)  # still inside docs/: MkDocs resolves it
    kind = "tree" if os.path.isdir(os.path.join(root, target)) else "blob"
    return f"]({REPO}/{kind}/{BRANCH}/{target}{match.group('anchor') or ''})"


def on_page_markdown(markdown, page, config, files):
    page_dir = posixpath.dirname(page.file.src_uri)
    root = os.path.dirname(config.config_file_path)
    return _LINK.sub(lambda m: _to_github(m, page_dir, root), markdown)
