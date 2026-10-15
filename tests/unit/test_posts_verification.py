import json
from pathlib import Path

import pytest

from tjm_agent.posts import Post, PostsError, parse_posts
from tjm_agent.verification import verify_saved

FIXTURE = Path(__file__).parents[1] / "fixtures" / "posts.json"


def test_first_ten_posts_from_recorded_api_response():
    posts = parse_posts(json.loads(FIXTURE.read_text()), 10)
    assert [p.id for p in posts] == list(range(1, 11))
    assert posts[0].filename == "post_1.txt"
    assert posts[0].text.startswith("Title: sunt aut facere")
    assert "\n\nquia et suscipit\n" in posts[0].text


@pytest.mark.parametrize(
    "bad",
    [
        {"id": 1, "title": "", "body": "x"},
        {"id": "1", "title": "t", "body": "x"},
        {"id": 1, "title": "t"},
        {"id": 1, "title": "café", "body": "x"},
    ],
)
def test_invalid_posts_rejected(bad):
    with pytest.raises(PostsError):
        parse_posts([bad], 1)


def test_too_few_posts_rejected():
    with pytest.raises(PostsError):
        parse_posts([{"id": 1, "title": "t", "body": "b"}], 10)


def test_verify_saved(tmp_path):
    post = Post(1, "hello", "line one\nline two")
    f = tmp_path / post.filename
    assert not verify_saved(f, post.text).ok
    f.write_text(post.text)
    assert verify_saved(f, post.text).ok
    f.write_text(post.text + "\n")
    assert "trailing newline" in verify_saved(f, post.text).reason
    f.write_text("Title: Hello\n\nline one\nline two")
    assert "differs at char 7" in verify_saved(f, post.text).reason
    f.write_bytes(b"{\\rtf1\\ansi hello}")
    assert "rich text" in verify_saved(f, post.text).reason
