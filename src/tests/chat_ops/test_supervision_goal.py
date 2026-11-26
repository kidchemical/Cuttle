"""The schedule view's goal: the supervisor's own open Tasks list names the panel."""
from api.chat_ops import service
from api.gizmos import tasks
from tests.chat_ops.test_chat_ops import chats, isolated_home, _schedule, _ch  # noqa: F401


def test_goal_is_the_supervisors_open_tasks_list(chats, isolated_home):
    db, owner, sup, target, _ = chats
    tasks.create(gizmo_id='release', title='Release v0.8', session_id=sup, user_id=owner, db=db,
                 items=[{'id': 'a', 'text': 'Cut'}, {'id': 'b', 'text': 'Tag'}])
    _schedule(chats)
    view = service.overview(_ch(sup), owner, db=db)
    assert view['supervising'][0]['goal'] == 'Release v0.8'
    # The supervised chat's own view names no goal: its panel is the supervisor's.
    assert 'goal' not in service.overview(_ch(target), owner, db=db)['supervised_by'][0]


def test_goal_is_omitted_when_every_list_is_done(chats, isolated_home):
    db, owner, sup, _target, _ = chats
    tasks.create(gizmo_id='release', title='Release v0.8', session_id=sup, user_id=owner, db=db,
                 items=[{'id': 'a', 'text': 'Cut'}])
    tasks.patch('release', {'set_done': ['a']}, session_id=sup, user_id=owner, db=db)
    _schedule(chats)
    assert 'goal' not in service.overview(_ch(sup), owner, db=db)['supervising'][0]


def test_goal_is_omitted_without_a_tasks_list(chats, isolated_home):
    db, owner, sup, _target, _ = chats
    _schedule(chats)
    assert 'goal' not in service.overview(_ch(sup), owner, db=db)['supervising'][0]
