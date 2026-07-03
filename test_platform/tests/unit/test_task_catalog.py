from test_platform.domain.task_catalog import (
    TaskCatalogFilter,
    build_task_catalog_snapshot,
)


def test_catalog_filters_match_current_registry_taxonomy():
    snapshot = build_task_catalog_snapshot(
        filters=TaskCatalogFilter(suites=["wechat"], apps=["wechat"], difficulties=["L3"]),
        repository_revision="test-rev",
    )

    assert snapshot.items
    assert {item.suite for item in snapshot.items} == {"wechat"}
    assert all("wechat" in item.apps for item in snapshot.items)
    assert {item.difficulty for item in snapshot.items} == {"L3"}
    assert any(item.task_base_id == "wechat.BlacklistContact" for item in snapshot.items)


def test_registry_digest_is_stable_for_stable_metadata():
    first = build_task_catalog_snapshot(
        filters=TaskCatalogFilter(suites=["wechat"]),
        repository_revision="test-rev",
    )
    second = build_task_catalog_snapshot(
        filters=TaskCatalogFilter(suites=["wechat"]),
        repository_revision="test-rev",
    )

    assert first.digest == second.digest
    assert first.digest.startswith("sha256:")
