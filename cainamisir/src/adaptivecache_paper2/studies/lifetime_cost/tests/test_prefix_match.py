from studies.lifetime_cost.pipeline.prefix_match import (
    cache_hit_ceiling,
    canonical_render,
    common_prefix_chars,
    hash_prefix_blocks,
)


def test_identical_messages_full_hit():
    a = [{"role": "system", "content": "hello"}, {"role": "user", "content": "hi"}]
    b = list(a)
    r = cache_hit_ceiling(a, b)
    assert r["hit_ratio_chars"] == 1.0
    assert r["hit_ratio_blocks"] == 1.0


def test_appended_message_keeps_prefix():
    a = [{"role": "system", "content": "x" * 5000}]
    b = a + [{"role": "user", "content": "new"}]
    r = cache_hit_ceiling(a, b, block_chars=1024)
    assert r["shared_blocks"] >= 4   # all of A's blocks should still hit


def test_rewritten_first_message_destroys_prefix():
    a = [{"role": "system", "content": "x" * 5000}]
    b = [{"role": "system", "content": "y" * 5000}]   # totally different
    r = cache_hit_ceiling(a, b, block_chars=1024)
    assert r["hit_ratio_blocks"] == 0.0


def test_canonical_render_stable():
    msgs = [{"role": "user", "content": "hello", "name": "vlad"}]
    assert canonical_render(msgs) == canonical_render(msgs)


def test_common_prefix_chars_basic():
    assert common_prefix_chars("abcd", "abef") == 2
    assert common_prefix_chars("", "abc") == 0
    assert common_prefix_chars("abc", "abc") == 3
