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
    assert loaded[0]['title'] is None
    assert loaded[1]['title'] == 'RAG学习'
    assert store.get_graph(wf)['nodes'][0]['title'] == 'Route'
    assert [turn['userMessage']['content'] for turn in loaded] == ['first', 'second']
    store.close()
