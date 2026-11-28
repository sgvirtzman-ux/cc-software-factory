"""Issue trackers for the factory: GitHub Issues or Jira Cloud.

The queue, the issue text, comments, labels and the "merged" hand-off live in
the tracker. Code always lives on GitHub: branches, PRs and merges stay with
`gh` in factory/bin/job, whatever tracker config.json names.

Every adapter returns issues in one shape:

  {"id": 12 | "PROJ-12", "ref": "#12" | "PROJ-12", "title": str, "body": str,
   "author": {"id": str, "name": str}, "labels": [str], "open": bool, "url": str,
   "comments": [{"author": {...}, "created": str, "body": str, "factory": bool}]}

`factory` marks comments the factory posted itself, so a reply from the
author or the operator can be told apart from the factory's own question.
"""

import base64
import json
import os
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

MARKER = "<!-- factory -->"  # GitHub: first line of every factory comment


class TrackerError(Exception):
    pass


def load(config, repo):
    tracker = config.get("tracker") or {"type": "github"}
    kind = tracker.get("type", "github")
    if kind == "github":
        return GitHub(repo, tracker.get("repo"))
    if kind == "jira":
        return Jira(tracker, config["labels"])
    raise TrackerError(f"config.json tracker.type is {kind!r}; use github or jira")


# ---- GitHub Issues ---------------------------------------------------------


class GitHub:
    kind = "github"

    def __init__(self, repo, slug=None):
        self.repo = repo
        self.slug = slug  # owner/name; needed when the factory isn't inside that repo
        self._me = None

    def _gh(self, *args):
        if self.slug and args[0] == "issue":
            args = (*args, "--repo", self.slug)
        res = subprocess.run(["gh", *args], cwd=self.repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode != 0:
            raise TrackerError(f"`gh {' '.join(args)}` failed: {(res.stderr or res.stdout).strip()}")
        return res.stdout

    def parse_id(self, text):
        text = text.strip().lstrip("#")
        return int(text) if text.isdigit() else None

    def ref(self, issue_id):
        return f"#{issue_id}"

    def sort_key(self, issue_id):
        return int(issue_id)

    def pr_link(self, issue):
        """(PR title prefix, first line of the PR body)."""
        return "", f"Closes #{issue['id']}"

    def me(self):
        if self._me is None:
            login = self._gh("api", "user", "--jq", ".login").strip()
            self._me = {"id": login, "name": login}
        return self._me

    def fetch(self, issue_id):
        i = json.loads(self._gh("issue", "view", str(issue_id), "--json",
                                "number,title,body,author,labels,state,url,comments"))
        return {
            "id": i["number"], "ref": f"#{i['number']}", "title": i["title"], "body": i["body"] or "",
            "author": {"id": i["author"]["login"], "name": i["author"]["login"]},
            "labels": [l["name"] for l in i["labels"]], "open": i["state"] == "OPEN", "url": i["url"],
            "comments": [{
                "author": {"id": c["author"]["login"], "name": c["author"]["login"]},
                "created": c["createdAt"],
                "body": c["body"].removeprefix(MARKER),
                "factory": c["body"].startswith(MARKER),
            } for c in i["comments"]],
        }

    def list_queue(self, label):
        issues = json.loads(self._gh("issue", "list", "--label", label, "--state", "open",
                                     "--limit", "200", "--json", "number,title"))
        return [{"id": i["number"], "title": i["title"]} for i in issues]

    def comment(self, issue_id, body, factory=True):
        self._gh("issue", "comment", str(issue_id), "--body", f"{MARKER}\n{body}" if factory else body)

    def add_label(self, issue_id, label):
        self._gh("issue", "edit", str(issue_id), "--add-label", label)

    def remove_labels(self, issue_id, labels):
        self._gh("issue", "edit", str(issue_id), "--remove-label", ",".join(labels))

    def create(self, title, body, labels):
        flags = [a for label in labels for a in ("--label", label)]
        url = self._gh("issue", "create", *flags, "--title", title, "--body", body).strip()
        return int(url.rstrip("/").rsplit("/", 1)[1])

    def relate(self, issue_id, other_id):
        """The new issue's body names #other, and GitHub links the two from that."""
        return None

    def make_ready(self, issue_id):
        """GitHub's queue is the label alone."""
        return None

    def on_started(self, job):
        """GitHub issues have no workflow status to move."""
        return None

    def on_merged(self, job, how):
        """A GitHub PR's `Closes #n` closes the issue. Without one (git mode),
        say how it merged and close the issue here."""
        if job.get("pr"):
            return None
        self.comment(job["issue"], f"### Factory: merged\n\n{how}")
        self._gh("issue", "close", str(job["issue"]))
        return None

    def merge_followup(self):
        return " and closes this issue"

    def on_rejected(self, job):
        """The label comes off; there's no status to put back."""
        return None

    def notify_escalation(self, job):
        """The PR links the issue, so the PR comment is enough."""
        return None

    def check(self, label):
        return [f"GitHub user: {self.me()['id']}",
                f"open issues labeled {label}: {len(self.list_queue(label))}"]


# ---- Jira Cloud ------------------------------------------------------------


KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
FACTORY_PROPERTY = "factory"  # Jira: entity property set on every factory comment


class Jira:
    """Jira Cloud through REST API v3, with an API token.

    Auth comes from the environment, never from config.json: JIRA_EMAIL and
    JIRA_API_TOKEN (an Atlassian API token for that account). Comments and
    labels are made as that account, which is the factory's operator.
    """

    kind = "jira"

    def __init__(self, tracker, labels):
        missing = [k for k in ("site", "project", "merged_status") if not tracker.get(k)]
        if missing:
            raise TrackerError(f"config.json tracker is missing {', '.join(missing)}")
        self.site = tracker["site"].rstrip("/")
        self.project = tracker["project"]
        self.issue_type = tracker.get("issue_type", "Task")
        self.link_type = tracker.get("link_type", "Relates")
        self.merged_status = tracker["merged_status"]
        self.ready_status = tracker.get("ready_status")
        self.start_status = tracker.get("start_status")
        base = f'project = "{self.project}" AND labels = "{labels["queue"]}"'
        if self.ready_status:
            # Issues ready for work, plus the ones the factory already started.
            statuses = [self.ready_status] + ([self.start_status] if self.start_status else [])
            where = "status in (" + ", ".join(f'"{x}"' for x in statuses) + ")"
        else:
            where = f'statusCategory != Done AND status != "{self.merged_status}"'
        self.queue_jql = tracker.get("queue_jql") or f"{base} AND {where} ORDER BY created ASC"
        self._me = None

    # -- HTTP

    def _auth(self):
        email, token = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
        if not email or not token:
            raise TrackerError("set JIRA_EMAIL and JIRA_API_TOKEN in the environment "
                               "(see INSTALL.md: Jira)")
        return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    def _req(self, method, path, body=None, query=None):
        url = self.site + path + ("?" + urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        req = Request(url, data=data, method=method, headers={
            "Authorization": self._auth(), "Accept": "application/json",
            "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=30) as res:
                raw = res.read()
        except HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                err = json.loads(detail)
                detail = "; ".join(err.get("errorMessages", []) + [f"{k}: {v}" for k, v in err.get("errors", {}).items()]) or detail
            except ValueError:
                pass
            raise TrackerError(f"Jira {method} {path}: HTTP {e.code}: {detail.strip()[:500]}")
        except URLError as e:
            raise TrackerError(f"Jira {method} {path}: {e.reason}")
        return json.loads(raw) if raw else None

    def _issue_path(self, key, suffix=""):
        return f"/rest/api/3/issue/{quote(key)}{suffix}"

    # -- ids

    def parse_id(self, text):
        text = text.strip()
        return text.upper() if KEY.match(text) else None

    def ref(self, issue_id):
        return issue_id

    def sort_key(self, issue_id):
        project, _, n = issue_id.rpartition("-")
        return (project, int(n))

    def pr_link(self, issue):
        return f"{issue['id']}: ", f"Jira: [{issue['id']}]({issue['url']})"

    # -- reads

    def me(self):
        if self._me is None:
            m = self._req("GET", "/rest/api/3/myself")
            self._me = {"id": m["accountId"], "name": m.get("displayName") or m["accountId"]}
        return self._me

    def fetch(self, key):
        i = self._req("GET", self._issue_path(key),
                      query={"fields": "summary,description,reporter,labels,status"})
        f = i["fields"]
        reporter = f.get("reporter") or {}
        comments, start = [], 0
        while True:
            page = self._req("GET", self._issue_path(key, "/comment"), query={
                "startAt": start, "maxResults": 100, "orderBy": "created", "expand": "properties"})
            comments += page.get("comments", [])
            start += len(page.get("comments", []))
            if not page.get("comments") or start >= page.get("total", 0):
                break
        me = self.me()["id"]

        def is_factory(c, text):
            props = {p.get("key") for p in c.get("properties") or []}
            # The property is the marker. The heading check covers a Jira that
            # drops properties: factory comments all open with "Factory".
            return FACTORY_PROPERTY in props or (
                (c.get("author") or {}).get("accountId") == me and text.lstrip("# ").startswith("Factory"))

        out = []
        for c in comments:
            text = adf_to_text(c.get("body"))
            author = c.get("author") or {}
            out.append({"author": {"id": author.get("accountId"), "name": author.get("displayName", "unknown")},
                        "created": c.get("created", ""), "body": text, "factory": is_factory(c, text)})
        return {
            "id": i["key"], "ref": i["key"], "title": f.get("summary") or "",
            "body": adf_to_text(f.get("description")),
            "author": {"id": reporter.get("accountId"), "name": reporter.get("displayName", "unknown")},
            "labels": f.get("labels") or [],
            "open": (f.get("status") or {}).get("statusCategory", {}).get("key") != "done",
            "url": f"{self.site}/browse/{i['key']}",
            "comments": out,
        }

    def list_queue(self, label):
        issues, token = [], None
        while len(issues) < 200:
            body = {"jql": self.queue_jql, "fields": ["summary"], "maxResults": 100}
            if token:
                body["nextPageToken"] = token
            page = self._req("POST", "/rest/api/3/search/jql", body=body)
            issues += [{"id": i["key"], "title": i["fields"].get("summary") or ""} for i in page.get("issues", [])]
            token = page.get("nextPageToken")
            if not token or page.get("isLast", True):
                break
        return issues

    # -- writes

    def comment(self, key, body, factory=True):
        payload = {"body": md_to_adf(body)}
        if factory:
            payload["properties"] = [{"key": FACTORY_PROPERTY, "value": {"factory": True}}]
        self._req("POST", self._issue_path(key, "/comment"), body=payload)

    def add_label(self, key, label):
        self._req("PUT", self._issue_path(key), body={"update": {"labels": [{"add": label}]}})

    def remove_labels(self, key, labels):
        self._req("PUT", self._issue_path(key), body={"update": {"labels": [{"remove": l} for l in labels]}})

    def create(self, title, body, labels):
        res = self._req("POST", "/rest/api/3/issue", body={"fields": {
            "project": {"key": self.project}, "summary": title[:255], "description": md_to_adf(body),
            "issuetype": {"name": self.issue_type}, "labels": list(labels)}})
        return res["key"]

    def relate(self, key, other):
        """Link two issues with link_type (default Relates). Returns None, or why it couldn't."""
        try:
            self._req("POST", "/rest/api/3/issueLink", body={
                "type": {"name": self.link_type}, "inwardIssue": {"key": key}, "outwardIssue": {"key": other}})
        except TrackerError as e:
            return f"couldn't link {key} to {other} ({self.link_type!r}; set tracker.link_type to a link type "\
                   f"this site has): {e}"
        return None

    def status(self, key):
        issue = self._req("GET", self._issue_path(key), query={"fields": "status"})
        return (issue["fields"].get("status") or {}).get("name", "")

    def transition_to(self, key, status):
        """Move the issue to `status`. Returns None on success, or why it couldn't."""
        if self.status(key).casefold() == status.casefold():
            return None
        options = self._req("GET", self._issue_path(key, "/transitions"))["transitions"]
        match = [t for t in options if t.get("to", {}).get("name", "").casefold() == status.casefold()
                 or t.get("name", "").casefold() == status.casefold()]
        if not match:
            names = ", ".join(sorted({t.get("to", {}).get("name", t.get("name", "?")) for t in options})) or "none"
            return f"no transition to {status!r} from its current status (available: {names})"
        self._req("POST", self._issue_path(key, "/transitions"), body={"transition": {"id": match[0]["id"]}})
        return None

    def make_ready(self, key):
        """Move an issue the factory filed to ready_status, if one is set, so the
        queue sees it. Returns None, or why it couldn't."""
        if not self.ready_status:
            return None
        try:
            why = self.transition_to(key, self.ready_status)
        except TrackerError as e:
            why = str(e)
        return why and f"move it to {self.ready_status} by hand for the factory to pick it up: {why}"

    def on_started(self, job):
        """Move the issue to start_status, if one is set, once intake accepts it.

        Returns None on success, or why it couldn't; the job goes on either way.
        """
        if not self.start_status:
            return None
        try:
            return self.transition_to(job["issue"], self.start_status)
        except TrackerError as e:
            return str(e)

    def on_rejected(self, job):
        """Put an issue the factory started back in ready_status.

        Only when it's still in start_status: if a person has moved it since,
        that was their call. Returns None, or why the move failed.
        """
        if not (self.ready_status and self.start_status):
            return None
        try:
            if self.status(job["issue"]).casefold() != self.start_status.casefold():
                return None
            return self.transition_to(job["issue"], self.ready_status)
        except TrackerError as e:
            return str(e)

    def on_merged(self, job, how):
        """Say how it merged and move the issue to merged_status.

        Returns a warning string when the move failed; the merge itself stands.
        """
        try:
            why = self.transition_to(job["issue"], self.merged_status)
        except TrackerError as e:
            why = str(e)
        note = (f"Moved to {self.merged_status}." if why is None
                else f"Couldn't move it to {self.merged_status}: {why}")
        self.comment(job["issue"], f"### Factory: merged\n\n{how} {note}")
        return why

    def merge_followup(self):
        return f" and moves this ticket to {self.merged_status}"

    def notify_escalation(self, job):
        """People watch Jira, not the PR: point them at it."""
        if job.get("pr"):
            self.comment(job["issue"], f"### Factory: needs a human\n\n"
                                       f"The decision is on [PR #{job['pr']}]({job['pr_url']}).")

    def check(self, label):
        lines = [f"Jira user: {self.me()['name']} ({self.me()['id']})"]
        statuses = self._req("GET", f"/rest/api/3/project/{quote(self.project)}/statuses")
        names = {s["name"].casefold() for t in statuses for s in t.get("statuses", [])}
        for setting in ("ready_status", "start_status", "merged_status"):
            status = getattr(self, setting)
            if status is None:
                continue
            if status.casefold() not in names:
                raise TrackerError(f"{setting}: project {self.project} has no status named {status!r}")
            lines.append(f"{setting}: {status!r} exists in {self.project}")
        lines.append(f"queue JQL: {self.queue_jql}")
        lines.append(f"issues in the queue: {len(self.list_queue(label))}")
        return lines


# ---- Atlassian Document Format (ADF) ---------------------------------------
# Jira Cloud's REST API v3 takes and returns rich text as ADF JSON. The factory
# writes Markdown, so these convert the subset it uses: headings, paragraphs,
# bullet and numbered lists, block quotes, fenced code, `code`, **bold**, links.


INLINE = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)")
BLOCK_START = re.compile(r"^(#{1,6}\s|```|>|\s*[-*+]\s|\s*\d+[.)]\s)")


def _inline(text):
    nodes, pos = [], 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            nodes.append({"type": "text", "text": text[pos:m.start()]})
        code, bold, label, href = m.groups()
        if code is not None:
            nodes.append({"type": "text", "text": code, "marks": [{"type": "code"}]})
        elif bold is not None:
            nodes.append({"type": "text", "text": bold, "marks": [{"type": "strong"}]})
        else:
            nodes.append({"type": "text", "text": label, "marks": [{"type": "link", "attrs": {"href": href}}]})
        pos = m.end()
    if pos < len(text):
        nodes.append({"type": "text", "text": text[pos:]})
    return [n for n in nodes if n["text"]]


def _paragraph(lines):
    content = []
    for k, line in enumerate(lines):
        if k:
            content.append({"type": "hardBreak"})
        content += _inline(line.strip())
    return {"type": "paragraph", "content": content}


def _blocks(lines):
    blocks, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # the closing fence
            block = {"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(code)}] if any(code) else []}
            if lang:
                block["attrs"] = {"language": lang}
            blocks.append(block)
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            blocks.append({"type": "heading", "attrs": {"level": len(m.group(1))}, "content": _inline(m.group(2).strip())})
            i += 1
            continue
        if line.startswith(">"):
            quoted = []
            while i < len(lines) and lines[i].startswith(">"):
                quoted.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            inner = _blocks(quoted) or [{"type": "paragraph", "content": []}]
            blocks.append({"type": "blockquote", "content": inner})
            continue
        for kind, pattern in (("bulletList", r"^\s*[-*+]\s+(.*)$"), ("orderedList", r"^\s*\d+[.)]\s+(.*)$")):
            if re.match(pattern, line):
                items = []
                while i < len(lines) and re.match(pattern, lines[i]):
                    item = [re.match(pattern, lines[i]).group(1)]
                    i += 1
                    # Indented lines that aren't list items continue the item.
                    while (i < len(lines) and lines[i].startswith(("  ", "\t")) and lines[i].strip()
                           and not re.match(r"^\s*([-*+]|\d+[.)])\s", lines[i])):
                        item.append(lines[i])
                        i += 1
                    items.append({"type": "listItem", "content": [_paragraph(item)]})
                blocks.append({"type": kind, "content": items})
                break
        else:
            para = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not BLOCK_START.match(lines[i]):
                para.append(lines[i])
                i += 1
            blocks.append(_paragraph(para))
    return blocks


def md_to_adf(markdown):
    blocks = _blocks(markdown.replace("\r\n", "\n").split("\n"))
    return {"type": "doc", "version": 1, "content": blocks or [{"type": "paragraph", "content": []}]}


def adf_to_text(node):
    """ADF → readable Markdown-ish text, for issue.md."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    out = _text(node)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _text(node):
    kind = node.get("type")
    kids = node.get("content") or []
    attrs = node.get("attrs") or {}
    inner = "".join(_text(k) for k in kids)
    if kind == "text":
        text = node.get("text", "")
        for mark in node.get("marks") or []:
            href = (mark.get("attrs") or {}).get("href")
            if mark.get("type") == "link" and href and href != text:
                text = f"{text} ({href})"
            elif mark.get("type") == "code":
                text = f"`{text}`"
        return text
    if kind == "hardBreak":
        return "\n"
    if kind in {"mention", "emoji", "status"}:
        return attrs.get("text", "")
    if kind in {"inlineCard", "blockCard", "embedCard"}:
        return attrs.get("url", "") + ("\n\n" if kind != "inlineCard" else "")
    if kind == "paragraph":
        return inner + "\n\n"
    if kind == "heading":
        return "#" * int(attrs.get("level", 1)) + " " + inner + "\n\n"
    if kind == "bulletList":
        return "".join("- " + _text(k).strip().replace("\n", "\n  ") + "\n" for k in kids) + "\n"
    if kind == "orderedList":
        start = int(attrs.get("order", 1))
        return "".join(f"{start + n}. " + _text(k).strip().replace("\n", "\n   ") + "\n"
                       for n, k in enumerate(kids)) + "\n"
    if kind == "codeBlock":
        return "```\n" + inner + "\n```\n\n"
    if kind == "blockquote":
        return "".join("> " + line + "\n" for line in inner.strip().splitlines()) + "\n"
    if kind == "rule":
        return "---\n\n"
    if kind == "tableRow":
        return "| " + " | ".join(_text(k).strip().replace("\n", " ") for k in kids) + " |\n"
    if kind == "table":
        return inner + "\n"
    return inner
