from monitorr import store
from monitorr.engine.policy import set_dry_run

TVDB = 999


async def test_pending_previews_are_deduplicated() -> None:
    # Tres ciclos de grace/sync en dry-run sobre el mismo episodio → una sola fila pendiente.
    for _ in range(3):
        await store.record_deletion(TVDB, 1, 2, "E2", 202, "grace_watched", dry_run=True)

    pending = await store.list_deletions(dry_run=True)
    assert len(pending) == 1
    assert (pending[0].season, pending[0].episode) == (1, 2)
    # El upsert refresca los metadatos (último motivo gana).
    assert pending[0].reason == "grace_watched"


async def test_real_deletion_clears_matching_preview_and_keeps_history() -> None:
    await store.record_deletion(TVDB, 1, 2, "E2", 202, "keep", dry_run=True)
    await store.record_deletion(TVDB, 1, 2, "E2", 202, "keep", dry_run=False)

    assert await store.list_deletions(dry_run=True) == []  # preview retirado
    done = await store.list_deletions(dry_run=False)
    assert {(d.season, d.episode) for d in done} == {(1, 2)}


async def test_real_deletions_remain_append_only() -> None:
    await store.record_deletion(TVDB, 1, 2, "E2", 202, "keep", dry_run=False)
    await store.record_deletion(TVDB, 1, 2, "E2", 202, "grace_watched", dry_run=False)

    done = await store.list_deletions(dry_run=False)
    assert len(done) == 2  # el historial real no se deduplica


async def test_clear_pending_deletions() -> None:
    await store.record_deletion(TVDB, 1, 1, "Pilot", 201, "keep", dry_run=True)
    await store.record_deletion(TVDB, 1, 2, "E2", 202, "keep", dry_run=True)

    await store.clear_pending_deletions()

    assert await store.list_deletions(dry_run=True) == []


async def test_disabling_dry_run_clears_pending() -> None:
    await store.record_deletion(TVDB, 1, 2, "E2", 202, "keep", dry_run=True)

    await set_dry_run(False)

    assert await store.list_deletions(dry_run=True) == []
