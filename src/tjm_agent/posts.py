"""Fetch and validate the JSONPlaceholder posts; build the exact text to type."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Post:
    id: int
    title: str
    body: str

    @property
    def filename(self) -> str:
        return f"post_{self.id}.txt"

    @property
    def text(self) -> str:
        return f"Title: {self.title}\n\n{self.body}"


class PostsError(RuntimeError):
    pass


def parse_posts(data: object, limit: int) -> list[Post]:
    if not isinstance(data, list):
        raise PostsError(f"expected a JSON list, got {type(data).__name__}")
    posts = []
    for i, item in enumerate(data[:limit]):
        if not isinstance(item, dict):
            raise PostsError(f"item {i} is not an object")
        pid, title, body = item.get("id"), item.get("title"), item.get("body")
        if not isinstance(pid, int) or pid < 0:
            raise PostsError(f"item {i}: invalid id {pid!r}")
        if not isinstance(title, str) or not title.strip():
            raise PostsError(f"post {pid}: missing title")
        if not isinstance(body, str) or not body.strip():
            raise PostsError(f"post {pid}: missing body")
        post = Post(pid, title, body)
        if not post.text.isascii():
            raise PostsError(f"post {pid}: non-ASCII text cannot be typed reliably")
        posts.append(post)
    if len(posts) < limit:
        raise PostsError(f"API returned {len(posts)} posts, need {limit}")
    if len({p.id for p in posts}) != len(posts):
        raise PostsError("duplicate post ids")
    return posts


def fetch_posts(url: str, limit: int = 10, retries: int = 3) -> list[Post]:
    last: Exception | None = None
    for _ in range(retries):
        try:
            r = httpx.get(url, timeout=15)
            r.raise_for_status()
            return parse_posts(r.json(), limit)
        except (httpx.HTTPError, ValueError) as e:
            last = e
    raise PostsError(f"could not fetch posts from {url}: {last}")
