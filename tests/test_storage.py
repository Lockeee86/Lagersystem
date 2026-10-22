import pytest

from app import storage
from app.models import Box, Movement
from app.storage import StorageError


def boxes(db, seed, n):
    created = storage.create_boxes(n, seed["bt"].id, seed["worker"])
    db.session.commit()
    return created


def test_codes_are_sequential(db, seed):
    a = boxes(db, seed, 3)
    b = boxes(db, seed, 2)
    assert [x.code for x in a + b] == ["K-0001", "K-0002", "K-0003", "K-0004", "K-0005"]


@pytest.mark.parametrize("raw,expected", [
    ("K-0012", "K-0012"), ("k12", "K-0012"), ("12", "K-0012"), (" K-12 ", "K-0012"),
    ("https://lager.example/kiste/K-0007", "K-0007"), ("hallo", None), ("", None),
])
def test_normalize_code(raw, expected):
    assert storage.normalize_code(raw) == expected


def test_store_puts_box_on_top(db, seed):
    wh = seed["wh"]
    a, b, c = boxes(db, seed, 3)
    for box in (a, b, c):
        storage.store_box(box, wh.id, 3, 7, seed["worker"])
    db.session.commit()
    assert [x.level for x in (a, b, c)] == [1, 2, 3]
    assert c.location == "C-07-3"


def test_stack_max_height(db, seed):
    wh = seed["wh"]
    created = boxes(db, seed, 6)
    for box in created[:5]:
        storage.store_box(box, wh.id, 1, 1, seed["worker"])
    with pytest.raises(StorageError, match="voll"):
        storage.store_box(created[5], wh.id, 1, 1, seed["worker"])


def test_invalid_position(db, seed):
    (box,) = boxes(db, seed, 1)
    with pytest.raises(StorageError):
        storage.store_box(box, seed["wh"].id, 11, 1, seed["worker"])
    with pytest.raises(StorageError):
        storage.store_box(box, seed["wh"].id, 1, 16, seed["worker"])


def test_remove_from_middle_shifts_boxes_down(db, seed):
    wh = seed["wh"]
    a, b, c, d = boxes(db, seed, 4)
    for box in (a, b, c, d):
        storage.store_box(box, wh.id, 2, 2, seed["worker"])
    db.session.commit()
    assert [x.code for x in storage.boxes_above(b)] == [d.code, c.code]

    shifted = storage.remove_box(b, seed["worker"], empty=True)
    db.session.commit()
    assert {x.code for x in shifted} == {c.code, d.code}
    assert not b.is_stored and b.fill == 0
    assert [x.code for x in storage.stack(wh.id, 2, 2)] == [a.code, c.code, d.code]
    assert [x.level for x in storage.stack(wh.id, 2, 2)] == [1, 2, 3]
    assert Movement.query.filter_by(action="nachgerueckt").count() == 2


def test_move_box(db, seed):
    wh = seed["wh"]
    a, b, c = boxes(db, seed, 3)
    storage.store_box(a, wh.id, 1, 1, seed["worker"])
    storage.store_box(b, wh.id, 1, 1, seed["worker"])
    storage.store_box(c, wh.id, 4, 4, seed["worker"])
    db.session.commit()

    storage.move_box(a, wh.id, 4, 4, seed["worker"])
    db.session.commit()
    assert a.location == "D-04-2"
    assert b.level == 1  # rutscht nach
    move = Movement.query.filter_by(box_id=a.id, action="umgelagert").one()
    assert move.from_location == "Kartoffellager A-01-1"
    assert move.to_location == "Kartoffellager D-04-2"

    with pytest.raises(StorageError, match="bereits"):
        storage.move_box(a, wh.id, 4, 4, seed["worker"])


def test_move_to_full_stack_keeps_box_in_place(db, seed):
    wh = seed["wh"]
    created = boxes(db, seed, 6)
    for box in created[:5]:
        storage.store_box(box, wh.id, 5, 5, seed["worker"])
    storage.store_box(created[5], wh.id, 6, 6, seed["worker"])
    db.session.commit()
    with pytest.raises(StorageError, match="voll"):
        storage.move_box(created[5], wh.id, 5, 5, seed["worker"])
    assert created[5].location == "F-06-1"


def test_contents_and_fill(db, seed):
    (box,) = boxes(db, seed, 1)
    assert storage.update_contents(box, seed["worker"], seed["belana"].id, None, seed["plot"].id, "35-55", 100, None)
    db.session.commit()
    assert box.weight_kg == 400
    assert storage.set_fill(box, 50, seed["worker"])
    assert box.weight_kg == 200
    assert not storage.set_fill(box, 50, seed["worker"])
    with pytest.raises(StorageError):
        storage.set_fill(box, 33, seed["worker"])


def test_unique_position_constraint(db, seed):
    a, b = boxes(db, seed, 2)
    a.warehouse_id, a.row, a.slot, a.level = seed["wh"].id, 1, 1, 1
    b.warehouse_id, b.row, b.slot, b.level = seed["wh"].id, 1, 1, 1
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert Box.query.count() == 2
