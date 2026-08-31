import{cleanup,fireEvent,render,screen}from'@testing-library/react';
import{afterEach,describe,expect,it,vi}from'vitest';
import{ConversationCard,nodeSubtitle,reactFlowNodePointerProps,shouldShowMiniMap}from'./WorkflowGraph';import type{Instance}from'../domain/types';
const node:Instance={id:'n1',parentId:null,topicId:'internal-topic-id',title:'数据集设计',status:'active'};
afterEach(()=>{cleanup();vi.clearAllTimers();vi.useRealTimers()});
describe('workflow graph presentation',()=>{
 it('hides the minimap for small graphs',()=>{expect(shouldShowMiniMap(3)).toBe(false);expect(shouldShowMiniMap(7)).toBe(false);expect(shouldShowMiniMap(8)).toBe(true)});
 it('never exposes topicId as node subtitle',()=>{expect(nodeSubtitle(node)).toBe('');expect(nodeSubtitle({...node,summary:'字段与标注规范'})).toBe('字段与标注规范')});
 it('keeps the original conversation title',()=>{expect(node.title).toBe('数据集设计')});
 it('activates exactly once through the React Flow wrapper path',()=>{const activate=vi.fn(),events=reactFlowNodePointerProps(activate);expect(events.onNodeClick).toBeTypeOf('function');events.onNodeDoubleClick({} as never,{id:'n1'} as never);expect(activate).toHaveBeenCalledOnce();expect(activate).toHaveBeenCalledWith('n1')});
 it('cancels selection and activates exactly once for a browser double-click sequence',()=>{vi.useFakeTimers();const select=vi.fn(),activate=vi.fn();render(<ConversationCard data={{instance:node,active:false,onSelect:select,onActivate:activate}}/>);const title=screen.getByText('数据集设计');fireEvent.click(title,{detail:1});fireEvent.click(title,{detail:2});fireEvent.doubleClick(title,{detail:2});vi.runAllTimers();expect(select).not.toHaveBeenCalled();expect(activate).toHaveBeenCalledOnce();expect(activate).toHaveBeenCalledWith('n1')});
 it('activates from click detail 2 even when the browser emits no dblclick event',()=>{vi.useFakeTimers();const select=vi.fn(),activate=vi.fn();render(<ConversationCard data={{instance:node,active:false,onSelect:select,onActivate:activate}}/>);const title=screen.getByText('数据集设计');fireEvent.click(title,{detail:1});fireEvent.click(title,{detail:2});vi.runAllTimers();expect(select).not.toHaveBeenCalled();expect(activate).toHaveBeenCalledOnce();expect(activate).toHaveBeenCalledWith('n1')});
 it('selects after the single-click arbitration delay',()=>{vi.useFakeTimers();const select=vi.fn(),activate=vi.fn();render(<ConversationCard data={{instance:node,active:false,onSelect:select,onActivate:activate}}/>);fireEvent.click(screen.getByText('数据集设计'));expect(select).not.toHaveBeenCalled();vi.runAllTimers();expect(select).toHaveBeenCalledOnce();expect(select).toHaveBeenCalledWith('n1');expect(activate).not.toHaveBeenCalled()});
});
