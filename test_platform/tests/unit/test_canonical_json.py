from test_platform.domain.canonical_json import canonical_json, canonical_sha256


def test_canonical_json_sorts_object_keys_without_reordering_lists():
    value = {
        "z": [{"b": 2, "a": 1}, "second"],
        "a": "你好",
    }

    assert canonical_json(value) == '{"a":"你好","z":[{"a":1,"b":2},"second"]}'


def test_canonical_sha256_is_stable_for_equivalent_objects():
    left = {"lanes": [{"key": "candidate"}], "seed": 42}
    right = {"seed": 42, "lanes": [{"key": "candidate"}]}

    assert canonical_sha256(left) == canonical_sha256(right)
    assert canonical_sha256(left).startswith("sha256:")
