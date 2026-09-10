@echo off
setlocal
set "PLUGIN_ROOT=%~dp0.."
if defined CODEX_MCP_NODE_PATH if exist "%CODEX_MCP_NODE_PATH%" goto run_mcp_node
if defined CODEX_BROWSER_USE_NODE_PATH if exist "%CODEX_BROWSER_USE_NODE_PATH%" goto run_browser_node
if defined CODEX_ELECTRON_RESOURCES_PATH if exist "%CODEX_ELECTRON_RESOURCES_PATH%\cua_node\bin\node.exe" goto run_electron_node
if defined USERPROFILE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" goto run_user_node
where node >nul 2>&1
if not errorlevel 1 goto run_system_node
echo WeavePath Codex Companion could not find a Node runtime. 1>&2
exit /b 127
:run_mcp_node
"%CODEX_MCP_NODE_PATH%" "%PLUGIN_ROOT%\server.mjs" %*
exit /b %ERRORLEVEL%
:run_browser_node
"%CODEX_BROWSER_USE_NODE_PATH%" "%PLUGIN_ROOT%\server.mjs" %*
exit /b %ERRORLEVEL%
:run_electron_node
"%CODEX_ELECTRON_RESOURCES_PATH%\cua_node\bin\node.exe" "%PLUGIN_ROOT%\server.mjs" %*
exit /b %ERRORLEVEL%
:run_user_node
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" "%PLUGIN_ROOT%\server.mjs" %*
exit /b %ERRORLEVEL%
:run_system_node
node "%PLUGIN_ROOT%\server.mjs" %*
exit /b %ERRORLEVEL%
