"""Read current review metadata through fixed GitHub API endpoints."""

import json
import re
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from remediation.intake import Review


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubJSON:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect())

    def __call__(self, path, data=None):
        if not path.startswith(("/repos/", "/graphql")) or "?" in path or "#" in path:
            raise ValueError("Unexpected API path")
        request = Request("https://api.github.com" + path,
                          data=None if data is None else json.dumps(data).encode(),
                          headers={"Authorization": "Bearer " + self.token(),
                                   "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json"})
        with self.opener.open(request, timeout=10) as response:
            body = response.read(2_097_153)
        if len(body) > 2_097_152:
            raise ValueError("API response too large")
        result = json.loads(body)
        if isinstance(result, dict) and result.get("errors"):
            raise ValueError("GitHub query failed")
        return result


THREADS = """
query($owner:String!,$name:String!,$number:Int!,$cursor:String) {
  repository(owner:$owner,name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100,after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { isOutdated isResolved comments(first:1) { nodes { databaseId } } }
      }
    }
  }
}
"""


class GitHubReviewSource:
    def __init__(self, api):
        self.api = api

    def current_review(self, repository, pr, comment):
        if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        if type(pr) is not int or pr < 1 or type(comment) is not int or comment < 1:
            raise ValueError("Invalid identifiers")
        root = "/repos/" + repository
        pull = self.api(root + "/pulls/" + str(pr))
        item = self.api(root + "/pulls/comments/" + str(comment))
        if item["id"] != comment or item["pull_request_url"] != "https://api.github.com" + root + "/pulls/" + str(pr):
            raise ValueError("Comment belongs to another PR")
        if item.get("in_reply_to_id") is not None:
            raise ValueError("Only root findings are supported")
        repo = self.api(root)
        branch = self.api(root + "/branches/" + quote(pull["head"]["ref"], safe=""))
        if type(branch["protected"]) is not bool:
            raise ValueError("Missing branch protection metadata")
        cursor = None
        owner, name = repository.split("/")
        for _ in range(10):
            result = self.api("/graphql", {"query": THREADS, "variables": {
                "owner": owner, "name": name, "number": pr, "cursor": cursor}})
            connection = result["data"]["repository"]["pullRequest"]["reviewThreads"]
            for thread in connection["nodes"]:
                if any(c["databaseId"] == comment for c in thread["comments"]["nodes"]):
                    if type(thread["isOutdated"]) is not bool or type(thread["isResolved"]) is not bool:
                        raise ValueError("Missing thread state")
                    return Review(
                        pull["head"]["sha"], pull["head"]["repo"]["full_name"], pull["head"]["ref"],
                        pull["state"] == "open" and pull["draft"] is False
                        and not branch["protected"] and pull["head"]["ref"] != repo["default_branch"],
                        item["commit_id"], item["user"]["id"], item["user"]["type"] == "Bot",
                        item["path"], item["line"], item["body"], thread["isOutdated"], thread["isResolved"])
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            if not page["endCursor"] or page["endCursor"] == cursor:
                raise ValueError("Invalid pagination")
            cursor = page["endCursor"]
        raise ValueError("Current root review thread not found within API budget")
