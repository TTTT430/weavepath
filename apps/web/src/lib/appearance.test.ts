import{afterEach,describe,expect,it}from'vitest';
import{applyAppearance,readAppearance,saveAppearance}from'./appearance';

afterEach(()=>{localStorage.clear();delete document.documentElement.dataset.appearance});

describe('appearance preference',()=>{
 it('uses the Synapse light workspace by default',()=>{localStorage.clear();expect(readAppearance()).toBe('light')});
 it('keeps an explicit dark preference and applies changes immediately',()=>{saveAppearance('dark');expect(readAppearance()).toBe('dark');expect(document.documentElement.dataset.appearance).toBe('dark');applyAppearance('light');expect(document.documentElement.dataset.appearance).toBe('light')});
});
