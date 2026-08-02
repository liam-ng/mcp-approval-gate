import pytest

from app.core.canonical_json import canonicalize, parameters_hash


def test_key_order_is_irrelevant():
    a = {"InstanceType": "t3.micro", "MaxCount": 1, "MinCount": 1}
    b = {"MinCount": 1, "MaxCount": 1, "InstanceType": "t3.micro"}
    assert canonicalize(a) == canonicalize(b)
    assert parameters_hash(a) == parameters_hash(b)


def test_nested_structures_are_stable():
    a = {"TagSpecifications": [{"ResourceType": "instance", "Tags": [{"Key": "team", "Value": "gti"}]}]}
    b = {"TagSpecifications": [{"Tags": [{"Value": "gti", "Key": "team"}], "ResourceType": "instance"}]}
    assert parameters_hash(a) == parameters_hash(b)


def test_compact_output_no_whitespace():
    assert canonicalize({"a": [1, 2], "b": "x"}) == '{"a":[1,2],"b":"x"}'


def test_unicode_not_escaped_but_stable():
    assert canonicalize({"name": "香港"}) == '{"name":"香港"}'
    assert parameters_hash({"name": "香港"}) == parameters_hash({"name": "香港"})


def test_value_changes_change_the_hash():
    assert parameters_hash({"InstanceIds": ["i-1"]}) != parameters_hash({"InstanceIds": ["i-2"]})


def test_nan_rejected():
    with pytest.raises(ValueError):
        canonicalize({"x": float("nan")})


def test_list_order_is_significant():
    assert parameters_hash({"ids": ["a", "b"]}) != parameters_hash({"ids": ["b", "a"]})
