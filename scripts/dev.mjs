import { execFileSync } from 'node:child_process'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { setTimeout as delay } from 'node:timers/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const projectPath = dirname(dirname(fileURLToPath(import.meta.url)))
const backendPath = join(projectPath, 'backend')
const pythonPath = join(backendPath, '.venv', 'Scripts', 'python.exe')
const pythonEnvironment = { ...process.env, PYTHONUTF8: '1' }

execFileSync('powershell.exe', [
  '-NoProfile',
  '-ExecutionPolicy',
  'Bypass',
  '-File',
  join(projectPath, 'scripts', 'prepare-api.ps1'),
], { cwd: projectPath, stdio: 'inherit', env: pythonEnvironment })

if (!existsSync(pythonPath)) {
  throw new Error('Backend Python was not set up. Run npm run dev:api to inspect setup errors.')
}

const api = spawn(pythonPath, [
  '-m', 'uvicorn', 'main:app',
  '--reload',
  '--reload-dir', backendPath,
  '--host', '127.0.0.1',
  '--port', '8000',
  '--app-dir', backendPath,
], { cwd: backendPath, stdio: 'inherit', env: pythonEnvironment })

let server
let stopping = false

async function stop(exitCode) {
  if (stopping) return
  stopping = true
  if (server) await server.close()
  if (api.exitCode === null && !api.killed) api.kill()
  process.exitCode = exitCode
}

api.on('error', (error) => {
  console.error(`Could not start the CloudGuard API: ${error.message}`)
  void stop(1)
})

api.on('exit', (code, signal) => {
  if (!stopping) {
    console.error(`CloudGuard API exited unexpectedly (${signal || code}).`)
    void stop(code && code !== 0 ? code : 1)
  }
})

process.on('SIGINT', () => void stop(0))
process.on('SIGTERM', () => void stop(0))

try {
  let apiReady = false
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (api.exitCode !== null) throw new Error('The CloudGuard API stopped during startup.')
    try {
      const response = await fetch('http://127.0.0.1:8000/api/health', { signal: AbortSignal.timeout(2000) })
      if (response.ok && (await response.json()).status === 'ok') {
        apiReady = true
        break
      }
    } catch {
      await delay(1000)
    }
  }
  if (!apiReady) throw new Error('The API did not become ready. Run npm run dev:api to inspect startup errors.')

  server = await createServer({ configFile: join(projectPath, 'vite.config.js') })
  await server.listen()
  server.printUrls()
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error))
  await stop(1)
}
