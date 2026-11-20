"""Pure Tasks model: item owner / in_progress fields, their ops and invariants."""
import copy

import pytest

from api.gizmos import tasks_model as model

CHAT_OWNER = {'kind': 'chat', 'id': 'CH-000182', 'label': 'Codex worker', 'agent': 'codex'}


def _base():
    return {'items': [
        {'id': 'a', 'text': 'API', 'done': False, 'children': []},
        {'id': 'b', 'text': 'UI', 'done': False, 'children': [
            {'id': 'b.1', 'text': 'Button', 'done': False, 'children': []},
        ]},
    ]}


def test_legacy_payload_is_unchanged_by_normalize_and_patch():
    legacy = _base()
    before = copy.deepcopy(legacy)
    assert model.normalize_tasks_payload(legacy) == before
    assert model.apply_tasks_patch(legacy, {'set_done': ['a']}) == {
        'items': [
            {'id': 'a', 'text': 'API', 'done': True, 'children': []},
            before['items'][1],
        ]}
    # String items keep the old four-key shape too.
    assert model.normalize_tasks_payload({'items': ['one']})['items'] == [
        {'id': 't1', 'text': 'one', 'done': False, 'children': []}]


def test_set_owner_round_trips_and_null_removes_it():
    out = model.apply_tasks_patch(_base(), {'set_owner': {'a': CHAT_OWNER, 'b': {'kind': 'user', 'label': 'You'}}})
    assert out['items'][0]['owner'] == CHAT_OWNER
    assert out['items'][1]['owner'] == {'kind': 'user', 'label': 'You'}
    assert 'owner' not in out['items'][1]['children'][0]
    again = model.apply_tasks_patch(out, {'set_owner': {'a': None}})
    assert 'owner' not in again['items'][0]
    assert again['items'][1]['owner'] == {'kind': 'user', 'label': 'You'}


def test_set_owner_on_missing_item_is_ignored_like_set_done():
    out = model.apply_tasks_patch(_base(), {'set_owner': {'zz': CHAT_OWNER}})
    assert out == model.normalize_tasks_payload(_base())


def test_optional_owner_keys_are_omitted_when_empty():
    out = model.apply_tasks_patch(_base(), {'set_owner': {'a': {'kind': 'agent', 'agent': 'claude', 'label': 'Claude'}}})
    assert out['items'][0]['owner'] == {'kind': 'agent', 'agent': 'claude', 'label': 'Claude'}
    out = model.apply_tasks_patch(_base(), {'set_owner': {'a': {'kind': 'group', 'label': 'Crew'}}})
    assert out['items'][0]['owner'] == {'kind': 'group', 'label': 'Crew'}


def test_in_progress_set_and_clear_round_trip():
    out = model.apply_tasks_patch(_base(), {'set_in_progress': ['a', 'b.1']})
    assert out['items'][0]['in_progress'] is True
    assert out['items'][1]['children'][0]['in_progress'] is True
    assert 'in_progress' not in out['items'][1]
    out = model.apply_tasks_patch(out, {'clear_in_progress': ['a']})
    assert 'in_progress' not in out['items'][0]
    assert out['items'][1]['children'][0]['in_progress'] is True


def test_set_done_clears_in_progress_and_keeps_owner():
    out = model.apply_tasks_patch(_base(), {'set_owner': {'a': CHAT_OWNER}, 'set_in_progress': ['a']})
    done = model.apply_tasks_patch(out, {'set_done': ['a']})
    assert done['items'][0]['done'] is True
    assert 'in_progress' not in done['items'][0]
    assert done['items'][0]['owner'] == CHAT_OWNER


def test_done_item_cannot_be_put_in_progress_on_write():
    with pytest.raises(ValueError, match='done and cannot be in progress'):
        model.apply_tasks_patch(_base(), {'set_done': ['a'], 'set_in_progress': ['a']})
    done = model.apply_tasks_patch(_base(), {'set_done': ['a']})
    with pytest.raises(ValueError, match='done and cannot be in progress'):
        model.apply_tasks_patch(done, {'set_in_progress': ['a']})


def test_add_keeps_owner_and_in_progress():
    out = model.apply_tasks_patch(_base(), {'add': [
        {'parent': 'b', 'item': {'id': 'b.2', 'text': 'Form', 'owner': CHAT_OWNER, 'in_progress': True}},
        {'item': {'id': 'c', 'text': 'Docs', 'owner': {'kind': 'user', 'label': 'You'}}},
    ]})
    kid = out['items'][1]['children'][1]
    assert kid['owner'] == CHAT_OWNER and kid['in_progress'] is True
    assert out['items'][2]['owner'] == {'kind': 'user', 'label': 'You'}


def test_items_replace_keeps_owner_and_in_progress_when_sent():
    out = model.apply_tasks_patch(_base(), {'items': [
        {'id': 'x', 'text': 'New', 'owner': CHAT_OWNER, 'in_progress': True},
    ]})
    assert out['items'] == [{'id': 'x', 'text': 'New', 'done': False, 'children': [],
                             'owner': CHAT_OWNER, 'in_progress': True}]


def test_add_and_items_replace_reject_done_and_in_progress():
    with pytest.raises(ValueError, match='done and cannot be in progress'):
        model.apply_tasks_patch(_base(), {'add': [{'id': 'c', 'text': 'x', 'done': True, 'in_progress': True}]})
    with pytest.raises(ValueError, match='done and cannot be in progress'):
        model.apply_tasks_patch(_base(), {'items': [{'id': 'c', 'text': 'x', 'done': True, 'in_progress': True}]})


def test_stored_row_with_done_and_in_progress_is_repaired_on_read():
    stored = {'items': [{'id': 'a', 'text': 'API', 'done': True, 'in_progress': True, 'children': []}]}
    data = model.normalize_tasks_payload(stored)
    assert data['items'][0]['done'] is True
    assert 'in_progress' not in data['items'][0]
    assert model.tasks_fully_complete(stored) is True


def test_stored_malformed_owner_is_dropped_on_read_not_fatal():
    stored = {'items': [{'id': 'a', 'text': 'API', 'done': False, 'children': [],
                         'owner': {'kind': 'martian', 'label': 'x'}}]}
    assert 'owner' not in model.normalize_tasks_payload(stored)['items'][0]


@pytest.mark.parametrize('owner, message', [
    ('chat', 'owner must be an object'),
    ({'kind': 'robot', 'label': 'x'}, 'owner.kind'),
    ({'kind': 'chat', 'label': 'Worker'}, 'chat owner needs'),
    ({'kind': 'chat', 'id': 'CH-000001', 'label': ''}, 'owner.label is required'),
    ({'kind': 'chat', 'id': 'x' * 129, 'label': 'Worker'}, 'owner.id must be at most 128'),
    ({'kind': 'agent', 'agent': 'a' * 41, 'label': 'Claude'}, 'owner.agent must be at most 40'),
    ({'kind': 'user', 'label': 'y' * 61}, 'owner.label must be at most 60'),
])
def test_owner_validation_errors_on_write(owner, message):
    with pytest.raises(ValueError, match=message):
        model.apply_tasks_patch(_base(), {'set_owner': {'a': owner}})


def test_set_owner_and_in_progress_shape_errors():
    with pytest.raises(ValueError, match='set_owner must be an object'):
        model.apply_tasks_patch(_base(), {'set_owner': ['a']})
    with pytest.raises(ValueError, match='in_progress must be a boolean'):
        model.apply_tasks_patch(_base(), {'add': [{'id': 'c', 'text': 'x', 'in_progress': 'yes'}]})


def test_origin_turn_id_is_kept_through_normalize_and_every_patch():
    stored = {'items': _base()['items'], 'origin_turn_id': 'turn-abc'}
    assert model.normalize_tasks_payload(stored)['origin_turn_id'] == 'turn-abc'
    out = model.apply_tasks_patch(stored, {'set_done': ['a'], 'add': [{'id': 'c', 'text': 'z'}]})
    assert out['origin_turn_id'] == 'turn-abc'
    out = model.apply_tasks_patch(stored, {'items': []})
    assert out == {'items': [], 'origin_turn_id': 'turn-abc'}
    assert 'origin_turn_id' not in model.normalize_tasks_payload(_base())


def test_every_op_preserves_owner_of_untouched_items():
    seeded = model.apply_tasks_patch(_base(), {'set_owner': {'b': CHAT_OWNER}})
    ops = [
        {'set_done': ['a']}, {'set_undone': ['a']}, {'set_text': {'a': 'API v2'}},
        {'remove': ['a']}, {'add': [{'id': 'c', 'text': 'New'}]},
        {'set_in_progress': ['a']}, {'clear_in_progress': ['a']},
    ]
    for patch in ops:
        out = model.apply_tasks_patch(seeded, patch)
        b = [n for n in out['items'] if n['id'] == 'b'][0]
        assert b['owner'] == CHAT_OWNER, patch


def test_label_round_trips_and_empty_clears_it():
    out = model.apply_tasks_patch(_base(), {'set_label': {'a': '  Fix   API '}})
    assert out['items'][0]['label'] == 'Fix API'
    assert out['items'][0]['text'] == 'API'
    assert 'label' not in out['items'][1]
    assert 'label' not in model.apply_tasks_patch(out, {'set_label': {'a': ''}})['items'][0]


def test_label_on_add_and_strict_limit():
    out = model.apply_tasks_patch(_base(), {'add': [{'item': {'id': 'c', 'text': 'Long step', 'label': 'Docs'}}]})
    assert out['items'][-1]['label'] == 'Docs'
    with pytest.raises(ValueError):
        model.apply_tasks_patch(_base(), {'set_label': {'a': 'one two three four'}})
    with pytest.raises(ValueError):
        model.apply_tasks_patch(_base(), {'add': [{'item': {'id': 'c', 'text': 'x', 'label': 'x' * 33}}]})
    # Stored rows are cut to fit, never rejected on read.
    stored = model.normalize_tasks_payload({'items': [{'id': 'a', 'text': 't', 'label': 'one two three four'}]})
    assert stored['items'][0]['label'] == 'one two three'
