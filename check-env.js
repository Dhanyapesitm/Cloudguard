/**
 * CloudGuard environment configuration diagnostic script.
 * Run with: npm run check-env (or node check-env.js)
 *
 * Checks that required environment variables are set and do not contain
 * placeholder values.
 *
 * Supports both:
 * - New Supabase Publishable Key format: "sb_publishable_..." (recommended)
 * - Legacy Supabase Anon JWT format: "eyJhbGci..."
 *
 * Variable names supported:
 * - VITE_SUPABASE_PUBLISHABLE_KEY (preferred)
 * - VITE_SUPABASE_ANON_KEY (backwards-compatible fallback)
 *
 * Security: NEVER prints raw secret values or keys to the console.
 */
import { readFileSync, existsSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = __dirname

function loadEnvFile(filePath) {
  if (!existsSync(filePath)) return {}
  const lines = readFileSync(filePath, 'utf8').split('\n')
  const result = {}
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue
    const eq = trimmed.indexOf('=')
    if (eq < 0) continue
    const key = trimmed.slice(0, eq).trim()
    const val = trimmed.slice(eq + 1).trim().replace(/^["']|["']$/g, '')
    result[key] = val
  }
  return result
}

const PLACEHOLDER_PATTERNS = [
  'your-project',
  'your-project-id',
  'placeholder',
  'example.supabase.co',
  'your-supabase-publishable-key',
  'your-supabase-anon-key',
  'your-supabase',
]

function isPlaceholder(value) {
  if (!value) return false
  const lower = value.toLowerCase()
  return PLACEHOLDER_PATTERNS.some((p) => lower.includes(p))
}

const envPath = resolve(root, '.env')
const envLocalPath = resolve(root, '.env.local')

console.log('CloudGuard Environment Diagnostics\n' + '='.repeat(38))

// 1. Frontend .env check
let envExists = false
let env = {}
if (existsSync(envPath)) {
  console.log('✅  .env file found at project root')
  env = loadEnvFile(envPath)
  envExists = true
} else if (existsSync(envLocalPath)) {
  console.log('✅  .env.local file found at project root')
  env = loadEnvFile(envLocalPath)
  envExists = true
} else {
  console.log('❌  No .env or .env.local file found at project root.')
  console.log('    Create c:\\Major_Project\\.env and add:')
  console.log('      VITE_SUPABASE_URL=https://your-project-id.supabase.co')
  console.log('      VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...')
}

let frontendConfigOk = false
if (envExists) {
  const url = (env['VITE_SUPABASE_URL'] || '').trim()
  const keyVarName = env['VITE_SUPABASE_PUBLISHABLE_KEY']
    ? 'VITE_SUPABASE_PUBLISHABLE_KEY'
    : (env['VITE_SUPABASE_ANON_KEY'] ? 'VITE_SUPABASE_ANON_KEY' : 'VITE_SUPABASE_PUBLISHABLE_KEY')
  const key = (env['VITE_SUPABASE_PUBLISHABLE_KEY'] || env['VITE_SUPABASE_ANON_KEY'] || '').trim()

  let urlOk = false
  if (!url) {
    console.log('❌  VITE_SUPABASE_URL is missing from .env')
  } else if (isPlaceholder(url)) {
    console.log('❌  VITE_SUPABASE_URL contains a placeholder value. Replace it with your Supabase Project URL.')
  } else if (!url.startsWith('https://') || !url.includes('.supabase.co')) {
    console.log('⚠️   VITE_SUPABASE_URL does not look like a standard Supabase URL (expected https://xxx.supabase.co)')
    urlOk = true
  } else {
    const project = url.replace('https://', '').split('.')[0]
    console.log(`✅  VITE_SUPABASE_URL set to project "${project}.supabase.co"`)
    urlOk = true
  }

  let keyOk = false
  if (!key) {
    console.log(`❌  ${keyVarName} is missing from .env`)
    console.log('    Set VITE_SUPABASE_PUBLISHABLE_KEY to your Supabase publishable key.')
  } else if (isPlaceholder(key)) {
    console.log(`❌  ${keyVarName} contains a placeholder value. Replace it with your actual key.`)
  } else if (key.startsWith('sb_secret_')) {
    console.log(`❌  ${keyVarName} appears to be a SECRET key (starts with "sb_secret_").`)
    console.log('    Never put secret keys in the frontend! Use the publishable key starting with "sb_publishable_".')
  } else if (key.startsWith('sb_publishable_')) {
    console.log(`✅  ${keyVarName} set using new Supabase publishable key format (sb_publishable_..., length=${key.length})`)
    keyOk = true
  } else if (key.startsWith('ey') && key.includes('.')) {
    console.log(`✅  ${keyVarName} set using legacy JWT format (length=${key.length})`)
    keyOk = true
  } else if (key.length >= 20) {
    console.log(`✅  ${keyVarName} set (length=${key.length})`)
    keyOk = true
  } else {
    console.log(`⚠️   ${keyVarName} appears unusually short (length=${key.length}). Check that you copied the complete key.`)
  }

  frontendConfigOk = urlOk && keyOk
}

// 2. Backend .env check
const backendEnvPath = resolve(root, 'backend', '.env')
if (existsSync(backendEnvPath)) {
  const backendEnv = loadEnvFile(backendEnvPath)
  const backendJwt = (backendEnv['SUPABASE_JWT_SECRET'] || '').trim()
  if (!backendJwt) {
    console.log('ℹ️   backend/.env found, but SUPABASE_JWT_SECRET is not yet configured.')
    console.log('    (Required when backend verifies Supabase access tokens; find in Supabase Dashboard -> Settings -> API -> JWT Settings)')
  } else if (isPlaceholder(backendJwt)) {
    console.log('⚠️   backend/.env SUPABASE_JWT_SECRET contains a placeholder value.')
  } else if (/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(backendJwt)) {
    console.log('⚠️   backend/.env SUPABASE_JWT_SECRET is formatted as a UUID (e.g. an Organization ID or User ID).')
    console.log('    In Supabase, the JWT Secret is NOT a UUID. Make sure to copy the "JWT Secret" from:')
    console.log('    Project Settings (gear icon) -> API -> scroll to "JWT Settings" -> JWT Secret (click Reveal).')
  } else {
    console.log(`✅  backend/.env SUPABASE_JWT_SECRET set (length=${backendJwt.length})`)
  }
} else {
  console.log('ℹ️   backend/.env not found. (Copy backend/.env.example to backend/.env when configuring backend Supabase JWT validation)')
}

console.log('')
if (frontendConfigOk) {
  console.log('🎉  Frontend Supabase configuration is valid!')
  console.log('    Run "npm run dev" to launch CloudGuard.')
} else {
  console.log('⚠️   Please review the items marked with ❌ above.')
}
