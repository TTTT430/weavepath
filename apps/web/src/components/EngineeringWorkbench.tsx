import {useCallback, useEffect, useMemo, useState} from 'react';
import type {Artifact, BranchComparison, Graph} from '../domain/types';
import {api} from '../lib/api';
import {useI18n} from '../lib/i18n';
import {MarkdownMessage} from './MarkdownMessage';
import {ErrorBanner} from './ErrorBanner';

type Section = 'compare' | 'artifacts';

const copy = {
  en: {
    title: 'Workflow review', compare: 'Branch compare', artifacts: 'Artifacts',
    compareHint: 'Compare branch summaries, route differences, model results and artifacts without mixing sibling transcripts.',
    selectBranches: 'Select 2–4 conversation branches', runCompare: 'Compare', sharedRoute: 'Shared memory prefix',
    routeDifference: 'Branch-specific route', summary: 'Conversation summary', noSummary: 'No local conversation summary yet.',
    messages: 'Local message counts', latestRun: 'Latest Agent conclusion', model: 'Model', noRun: 'No Agent run',
    branchArtifacts: 'Branch artifacts', noArtifacts: 'No artifacts', mergeTitle: 'Carry selected knowledge forward',
    mergeHint: 'Only checked conclusions and artifact references enter the target route. Conversation transcripts remain isolated.',
    mergeTarget: 'Target route', acceptConclusion: 'Use this conclusion', acceptArtifact: 'Use artifact',
    merge: 'Add selected knowledge', merged: 'Selected knowledge is now available to the target route and its descendants.',
    artifactLibrary: 'Artifact library', newArtifact: 'New artifact', name: 'Name', kind: 'Kind', mime: 'MIME type',
    content: 'Content', owner: 'Conversation', saveArtifact: 'Save version', preview: 'Preview', emptyArtifacts: 'No artifacts yet.',
    selectWorkflow: 'Select a workflow first.', required: 'Select or complete the required fields.',
    transcriptSafe: 'Sibling conversations remain isolated', refresh: 'Refresh'
  },
  'zh-CN': {
    title: '工作流研判', compare: '分支对比', artifacts: '成果库',
    compareHint: '对比各分支的对话摘要、路线差异、模型结论和成果，不混合兄弟分支的对话记录。',
    selectBranches: '选择 2–4 个对话分支', runCompare: '开始对比', sharedRoute: '共同记忆前缀',
    routeDifference: '分支独有路线', summary: '对话内容摘要', noSummary: '该分支还没有可总结的本地对话。',
    messages: '本地消息规模', latestRun: '最近的 Agent 结论', model: '使用模型', noRun: '暂无 Agent 运行',
    branchArtifacts: '分支成果', noArtifacts: '暂无成果', mergeTitle: '将选中知识带入后续路线',
    mergeHint: '只有勾选的结论和 Artifact 引用会进入目标路线，兄弟分支的完整对话仍保持隔离。',
    mergeTarget: '目标路线', acceptConclusion: '采用这条结论', acceptArtifact: '采用成果',
    merge: '加入所选知识', merged: '所选知识已加入目标路线，并可供其后代路线使用。',
    artifactLibrary: '成果资料库', newArtifact: '新建成果', name: '名称', kind: '类型', mime: 'MIME 类型',
    content: '内容', owner: '所属对话', saveArtifact: '保存新版本', preview: '预览', emptyArtifacts: '暂无成果。',
    selectWorkflow: '请先选择工作流。', required: '请选择或填写必填内容。',
    transcriptSafe: '兄弟对话保持隔离', refresh: '刷新'
  }
} as const;

export function EngineeringWorkbench({workflowId, graph, visible = true, onKnowledgeChanged}: {
  workflowId: string;
  graph: Graph | null;
  visible?: boolean;
  onKnowledgeChanged?: (instanceId: string) => void;
}) {
  const {locale} = useI18n();
  const l = copy[locale];
  const [section, setSection] = useState<Section>('compare');
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<BranchComparison | null>(null);
  const [targetId, setTargetId] = useState('');
  const [selectedConclusions, setSelectedConclusions] = useState<string[]>([]);
  const [selectedArtifacts, setSelectedArtifacts] = useState<string[]>([]);
  const [notice, setNotice] = useState('');
  const [artifactPreview, setArtifactPreview] = useState<Artifact | null>(null);
  const [artifactForm, setArtifactForm] = useState({name: '', kind: 'report', mimeType: 'text/markdown', content: '', instanceId: ''});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const activeNodes = useMemo(() => graph?.nodes.filter(node => node.status === 'active') || [], [graph]);

  const refresh = useCallback(async () => {
    if (!workflowId) return;
    try {
      setArtifacts(await api.artifacts(workflowId));
      setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [workflowId]);

  useEffect(() => { if (visible) void refresh(); }, [visible, refresh]);
  useEffect(() => {
    setSelectedIds([]);
    setComparison(null);
    setTargetId('');
    setSelectedConclusions([]);
    setSelectedArtifacts([]);
    setArtifactPreview(null);
    setNotice('');
  }, [workflowId]);

  function toggle(list: string[], value: string, setter: (next: string[]) => void, max = Infinity) {
    setter(list.includes(value) ? list.filter(item => item !== value) : list.length < max ? [...list, value] : list);
  }

  async function compare() {
    if (selectedIds.length < 2) return setError(l.required);
    setBusy(true);
    try {
      const value = await api.compareBranches(workflowId, selectedIds);
      setComparison(value);
      setTargetId(value.instanceIds[0] || '');
      setSelectedConclusions([]);
      setSelectedArtifacts([]);
      setNotice('');
      setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function merge() {
    if (!comparison || !targetId) return;
    const items = comparison.branches.flatMap(branch => {
      const run = branch.latestRun;
      const key = run ? `${branch.instanceId}:${run.runId}` : '';
      return run?.finalAnswer && selectedConclusions.includes(key) ? [{
        sourceInstanceId: branch.instanceId,
        sourceRunId: run.runId,
        kind: 'conclusion' as const,
        title: run.objective,
        content: run.finalAnswer
      }] : [];
    });
    const chosenArtifacts = comparison.branches.flatMap(branch => branch.artifacts.filter(item => selectedArtifacts.includes(item.artifactId)));
    const sources = [...new Set([
      ...items.map(item => item.sourceInstanceId),
      ...chosenArtifacts.flatMap(item => item.instanceId ? [item.instanceId] : [])
    ])];
    if (!items.length && !chosenArtifacts.length) return setError(l.required);
    setBusy(true);
    try {
      await api.mergeKnowledge(workflowId, {
        targetInstanceId: targetId,
        sourceInstanceIds: sources,
        items,
        artifactIds: chosenArtifacts.map(item => item.artifactId)
      });
      if (items.length) onKnowledgeChanged?.(targetId);
      setNotice(l.merged);
      setSelectedConclusions([]);
      setSelectedArtifacts([]);
      setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function saveArtifact() {
    if (!artifactForm.name.trim() || !artifactForm.content) return setError(l.required);
    setBusy(true);
    try {
      await api.createArtifact(workflowId, {...artifactForm, ...(artifactForm.instanceId ? {} : {instanceId: undefined})});
      setArtifactForm({name: '', kind: 'report', mimeType: 'text/markdown', content: '', instanceId: ''});
      await refresh();
      setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function preview(item: Artifact) {
    try {
      setArtifactPreview(await api.artifact(workflowId, item.artifactId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  if (!workflowId) return <div className="engineering-empty">{l.selectWorkflow}</div>;
  return <main className="engineering-workbench">
    <header className="engineering-header">
      <div><h1>{l.title}</h1><span>✓ {l.transcriptSafe}</span></div>
      <nav>{(['compare', 'artifacts'] as Section[]).map(item => <button type="button" key={item} className={section === item ? 'current' : ''} onClick={() => setSection(item)}>{l[item]}</button>)}</nav>
      <button type="button" onClick={() => void refresh()}>{l.refresh}</button>
    </header>
    <ErrorBanner message={error} onRetry={() => { setError(''); void refresh(); }}/>
    {notice && <p className="engineering-notice" role="status">{notice}</p>}
    {section === 'compare' && <section className="engineering-section compare-workspace">
      <div className="engineering-control">
        <h2>{l.selectBranches}</h2><p>{l.compareHint}</p>
        <div className="node-checks" role="group" aria-label={l.selectBranches}>{activeNodes.map(node => <label className={`branch-chip${selectedIds.includes(node.id) ? ' is-selected' : ''}`} key={node.id}><input type="checkbox" checked={selectedIds.includes(node.id)} onChange={() => toggle(selectedIds, node.id, setSelectedIds, 4)}/><span>{node.title}</span></label>)}</div>
        <button className="primary" disabled={busy || selectedIds.length < 2} onClick={() => void compare()}>{l.runCompare}</button>
      </div>
      {comparison && <>
        <div className="comparison-summary"><strong>{l.sharedRoute}</strong><div className="route-chips">{comparison.sharedRoute.map(item => <span key={item.instanceId}>{item.title}</span>)}</div></div>
        <div className="comparison-grid">{comparison.branches.map(branch => {
          const node = activeNodes.find(item => item.id === branch.instanceId);
          const branchOnly = branch.memoryRoute.slice(comparison.sharedRoute.length);
          return <article className="comparison-card" key={branch.instanceId}>
            <header><h3>{branch.title}</h3><small>{branch.memoryRoute.map(item => item.title).join(' → ')}</small></header>
            <section className="branch-summary"><strong>{l.summary}</strong><p>{node?.summary || l.noSummary}</p></section>
            <dl>
              <div><dt>{l.routeDifference}</dt><dd>{branchOnly.map(item => item.title).join(' → ') || '—'}</dd></div>
              <div><dt>{l.messages}</dt><dd>{Object.entries(branch.localMessageCounts).map(([role, count]) => `${role} ${count}`).join(' · ') || '—'}</dd></div>
              <div><dt>{l.model}</dt><dd>{branch.latestRun ? modelName(branch.latestRun.modelSnapshot) : '—'}</dd></div>
              <div><dt>{l.branchArtifacts}</dt><dd>{branch.artifacts.length || l.noArtifacts}</dd></div>
            </dl>
            {branch.latestRun ? <section className="comparison-result"><strong>{l.latestRun}: {branch.latestRun.objective}</strong>{branch.latestRun.finalAnswer ? <><MarkdownMessage content={branch.latestRun.finalAnswer}/><label className={`conclusion-choice${selectedConclusions.includes(`${branch.instanceId}:${branch.latestRun.runId}`) ? ' is-selected' : ''}`}><input type="checkbox" checked={selectedConclusions.includes(`${branch.instanceId}:${branch.latestRun.runId}`)} onChange={() => toggle(selectedConclusions, `${branch.instanceId}:${branch.latestRun!.runId}`, setSelectedConclusions)}/><span>{l.acceptConclusion}</span></label></> : <p>{l.noRun}</p>}</section> : <p>{l.noRun}</p>}
            {branch.artifacts.map(item => <label className={`artifact-choice${selectedArtifacts.includes(item.artifactId) ? ' is-selected' : ''}`} key={item.artifactId}><input type="checkbox" checked={selectedArtifacts.includes(item.artifactId)} onChange={() => toggle(selectedArtifacts, item.artifactId, setSelectedArtifacts)}/><span>{l.acceptArtifact}: {item.name} v{item.version}</span></label>)}
          </article>;
        })}</div>
        <section className="merge-bar"><div><h2>{l.mergeTitle}</h2><p>{l.mergeHint}</p></div><label>{l.mergeTarget}<select value={targetId} onChange={event => setTargetId(event.target.value)}>{activeNodes.map(node => <option key={node.id} value={node.id}>{node.title}</option>)}</select></label><button className="primary" disabled={busy || (!selectedConclusions.length && !selectedArtifacts.length)} onClick={() => void merge()}>{l.merge}</button></section>
      </>}
    </section>}
    {section === 'artifacts' && <section className="engineering-section artifact-workspace">
      <form onSubmit={event => { event.preventDefault(); void saveArtifact(); }}><h2>{l.newArtifact}</h2><label>{l.name}<input value={artifactForm.name} onChange={event => setArtifactForm({...artifactForm, name: event.target.value})}/></label><div className="form-row"><label>{l.kind}<input value={artifactForm.kind} onChange={event => setArtifactForm({...artifactForm, kind: event.target.value})}/></label><label>{l.mime}<input value={artifactForm.mimeType} onChange={event => setArtifactForm({...artifactForm, mimeType: event.target.value})}/></label></div><label>{l.owner}<select value={artifactForm.instanceId} onChange={event => setArtifactForm({...artifactForm, instanceId: event.target.value})}><option value="">—</option>{activeNodes.map(node => <option value={node.id} key={node.id}>{node.title}</option>)}</select></label><label>{l.content}<textarea value={artifactForm.content} onChange={event => setArtifactForm({...artifactForm, content: event.target.value})}/></label><button className="primary" disabled={busy}>{l.saveArtifact}</button></form>
      <div className="artifact-library"><h2>{l.artifactLibrary}</h2>{!artifacts.length && <p>{l.emptyArtifacts}</p>}<div className="artifact-list">{artifacts.map(item => <button type="button" className={artifactPreview?.artifactId === item.artifactId ? 'current' : ''} key={item.artifactId} onClick={() => void preview(item)}><strong>{item.name} · v{item.version}</strong><span>{item.kind} · {item.mimeType} · {item.size} B</span><small>{item.sha256.slice(0, 12)}</small></button>)}</div></div>
      <article className="artifact-preview"><h2>{l.preview}</h2>{artifactPreview ? <><h3>{artifactPreview.name} · v{artifactPreview.version}</h3><pre>{artifactPreview.content}</pre></> : <p>—</p>}</article>
    </section>}
  </main>;
}

function modelName(value: unknown) {
  if (!value || typeof value !== 'object') return '—';
  const item = value as Record<string, unknown>;
  return [item.provider, item.model].filter(Boolean).join(' / ') || '—';
}
