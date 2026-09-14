const { app, BrowserWindow, dialog, shell } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');

const APP_ID = 'org.weavepath.desktop';
const HOST = '127.0.0.1';
let backend = null;
let mainWindow = null;
let shuttingDown = false;
let recentBackendOutput = [];

app.setAppUserModelId(APP_ID);

function rememberBackendOutput(chunk) {
  const text = String(chunk || '').trim();
  if (!text) return;
  recentBackendOutput.push(text);
  recentBackendOutput = recentBackendOutput.slice(-20);
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, HOST, () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

function healthCheck(port) {
  return new Promise((resolve) => {
    const request = http.get(
      { hostname: HOST, port, path: '/api/v1/health', timeout: 1000 },
      (response) => {
        response.resume();
        resolve(response.statusCode >= 200 && response.statusCode < 500);
      },
    );
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

async function waitForBackend(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (backend && backend.exitCode !== null) {
      throw new Error(`后端提前退出（代码 ${backend.exitCode}）。`);
    }
    if (await healthCheck(port)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('后端在 30 秒内没有完成启动。');
}

function backendPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', 'WeavePathBackend.exe');
  }
  return path.resolve(__dirname, '..', '..', 'dist', 'WeavePathBackend', 'WeavePathBackend.exe');
}

function startBackend(port) {
  const executable = backendPath();
  if (!fs.existsSync(executable)) {
    throw new Error(`找不到桌面后端：${executable}`);
  }
  const localAppData = process.env.LOCALAPPDATA || app.getPath('userData');
  const dataDirectory = path.join(localAppData, 'WeavePath', 'data');
  fs.mkdirSync(dataDirectory, { recursive: true });
  backend = spawn(executable, ['--host', HOST, '--port', String(port)], {
    cwd: path.dirname(executable),
    windowsHide: true,
    env: { ...process.env, WEAVEPATH_DATA_DIR: dataDirectory },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backend.stdout.on('data', rememberBackendOutput);
  backend.stderr.on('data', rememberBackendOutput);
  backend.on('error', (error) => rememberBackendOutput(error.message));
}

function createWindow(port) {
  mainWindow = new BrowserWindow({
    icon: path.join(process.resourcesPath, 'weavepath-mark-v2.ico'),
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    backgroundColor: '#f5f7fb',
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });
  return mainWindow.loadURL(`http://${HOST}:${port}/`);
}

function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  backend.kill();
  backend = null;
}

const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    try {
      const port = await freePort();
      startBackend(port);
      await waitForBackend(port);
      await createWindow(port);
    } catch (error) {
      const details = recentBackendOutput.join('\n').slice(-4000);
      dialog.showErrorBox(
        'WeavePath 启动失败',
        `${error.message || error}${details ? `\n\n后端信息：\n${details}` : ''}`,
      );
      shuttingDown = true;
      stopBackend();
      app.quit();
    }
  });

  app.on('window-all-closed', () => {
    shuttingDown = true;
    stopBackend();
    app.quit();
  });
  app.on('before-quit', () => {
    if (!shuttingDown) shuttingDown = true;
    stopBackend();
  });
}
