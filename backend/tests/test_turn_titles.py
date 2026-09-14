import pytest
from graph_core import GraphStore, Conflict, Validation


def test_turn_title_is_independent_and_survives_reopen(tmp_path):
    path = str(tmp_path / 'titles.db')
    store = GraphStore(path)
    graph = store.create_workflow(name='Test', root_title='Route', root_instance_id='A')
    wf = graph['workflowId']
    store.append_message(wf, 'A', role='user', content='first')
    store.append_message(wf, 'A', role='user', content='second')
    turns = store.list_turns(wf, 'A')['turns']
    revision = store.get_graph(wf)['graphRevision']
    store.rename_turn(wf, 'A', turns[1]['anchorMessageId'], title='RAG学习', expected_revision=revision)
    with pytest.raises(Conflict):
        store.rename_turn(wf, 'A', turns[0]['anchorMessageId'], title='stale', expected_revision=revision)
    store.close()
    store = GraphStore(path)
    loaded = store.list_turn_tree(wf, 'A')['turns']
    assert loaded[0]['title'] == 'first'
    assert loaded[1]['title'] == 'RAG学习'
    assert store.get_graph(wf)['nodes'][0]['title'] == 'Route'
    assert [turn['userMessage']['content'] for turn in loaded] == ['first', 'second']
    store.close()


def test_generated_titles_use_each_turn_and_preserve_manual_names(store=None):
    store = GraphStore(':memory:')
    wf = store.create_workflow(name='Test', root_title='Root', root_instance_id='A')['workflowId']
    store.append_message(wf, 'A', role='user', content='请解释 **RAG** 的工作原理。并举例说明。')
    first = store.list_turns(wf, 'A')['turns'][0]
    assert first['title'] == '解释 RAG 的工作原理'
    store.rename_turn(wf, 'A', first['anchorMessageId'], title='我的学习计划', expected_revision=store.get_graph(wf)['graphRevision'])
    store.append_message(wf, 'A', role='assistant', content='这是 RAG 的回答')
    store.append_message(wf, 'A', role='user', content='向量数据库的检索方法' * 20)
    turns = store.list_turn_tree(wf, 'A')['turns']
    assert turns[0]['title'] == '我的学习计划'
    assert turns[1]['title'].startswith('向量数据库')
    assert len(turns[1]['title']) <= 28
    store.close()


def test_canvas_counts_live_ancestors_without_loading_their_bodies(monkeypatch):
    store = GraphStore(':memory:')
    wf = store.create_workflow(name='Test', root_title='Root', root_instance_id='A')['workflowId']
    store.append_message(wf, 'A', role='user', content='large parent ' * 10000)
    store.fork(wf, 'A', title='Child', instance_id='B')
    store.append_message(wf, 'A', role='user', content='latest parent')
    def forbidden(*args, **kwargs):
        raise AssertionError('Canvas must not load effective message bodies for a count')
    monkeypatch.setattr(store, '_effective_messages', forbidden)
    assert store.list_turns(wf, 'B')['inheritedMessageCount'] == 2
    store.close()
