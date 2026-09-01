import { defineConfig } from 'vite'; import react from '@vitejs/plugin-react';
const apiTarget=process.env.WEAVEPATH_API_TARGET||'http://localhost:8000';
export default defineConfig({plugins:[react()],server:{proxy:{'/api':apiTarget}},test:{environment:'jsdom',setupFiles:'./src/test/setup.ts'}});
