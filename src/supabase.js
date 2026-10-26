import { createClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL?.trim() || ''
const supabasePublishableKey = (
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim() ||
  import.meta.env.VITE_SUPABASE_ANON_KEY?.trim() ||
  ''
)

// Detect common placeholder / unconfigured values so we can show clear errors
const PLACEHOLDER_URL_PATTERNS = [
  'your-project',
  'your-project-id',
  'placeholder',
  'example.supabase.co',
]

const PLACEHOLDER_KEY_PATTERNS = [
  'your-supabase',
  'your-supabase-publishable-key',
  'your-supabase-anon-key',
  'placeholder',
]

const _urlIsPlaceholder = PLACEHOLDER_URL_PATTERNS.some((p) => supabaseUrl.includes(p))
const _keyIsPlaceholder = (
  !supabasePublishableKey ||
  PLACEHOLDER_KEY_PATTERNS.some((p) => supabasePublishableKey.includes(p)) ||
  supabasePublishableKey.length < 20
)

export const isSupabaseConfigured = Boolean(
  supabaseUrl &&
  supabasePublishableKey &&
  !_urlIsPlaceholder &&
  !_keyIsPlaceholder
)

/**
 * Returns a human-readable configuration error string, or null when Supabase is correctly set up.
 * Safe to display in the UI — never contains credentials.
 */
export function getSupabaseConfigError() {
  if (!supabaseUrl) {
    return 'VITE_SUPABASE_URL is not set. Create a .env file in your project root (not .env.example) and add your Supabase Project URL.'
  }
  if (_urlIsPlaceholder) {
    return 'VITE_SUPABASE_URL still contains a placeholder value. Open your .env file and replace it with your actual Supabase Project URL (e.g. https://abcdefgh.supabase.co).'
  }
  if (!supabasePublishableKey || _keyIsPlaceholder) {
    return 'VITE_SUPABASE_PUBLISHABLE_KEY is not set or still contains a placeholder. Open your .env file and add your Supabase publishable API key (starts with "sb_publishable_" or legacy anon JWT).'
  }
  if (supabasePublishableKey.startsWith('sb_secret_')) {
    return 'A secret key (sb_secret_...) was provided in frontend config. For security, only use your public publishable key (starts with sb_publishable_) in .env.'
  }
  return null
}

// Log configuration status at startup (no credentials printed)
if (import.meta.env.DEV) {
  const err = getSupabaseConfigError()
  if (err) {
    console.warn('[CloudGuard] Supabase not configured:', err)
  } else {
    const domainOnly = supabaseUrl.replace(/https?:\/\//, '').split('.')[0]
    const keyType = supabasePublishableKey.startsWith('sb_publishable_')
      ? 'new publishable format'
      : (supabasePublishableKey.startsWith('ey') ? 'legacy JWT format' : 'custom format')
    console.info(`[CloudGuard] Supabase configured: project="${domainOnly}.supabase.co", key="${keyType}"`)
  }
}

// Initialize official Supabase client with automatic token refresh and session persistence
export const supabase = isSupabaseConfigured
  ? createClient(supabaseUrl, supabasePublishableKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
        storageKey: 'cloudguard_supabase_auth',
      },
    })
  : null

/**
 * Register a new user using official Supabase email and password auth.
 * If Supabase email confirmation is enabled, session will be null until confirmed.
 */
export async function signUpWithEmailPassword(email, password, metadata = {}) {
  if (!supabase) {
    throw new Error('Supabase is not configured. Please set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY in .env.')
  }
  const redirectUrl = typeof window !== 'undefined'
    ? `${window.location.origin}/#email-confirmed=true`
    : undefined

  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      data: metadata,
      emailRedirectTo: redirectUrl,
    },
  })
  if (error) {
    throw error
  }
  return data
}

/**
 * Sign in using email and password via Supabase Auth.
 */
export async function signInWithEmailPassword(email, password) {
  if (!supabase) {
    throw new Error('Supabase is not configured. Please set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY in .env.')
  }
  const { data, error } = await supabase.auth.signInWithPassword({
    email,
    password,
  })
  if (error) {
    throw error
  }
  return data
}

/**
 * Sign out of Supabase Auth.
 */
export async function signOutSupabase() {
  if (!supabase) return
  const { error } = await supabase.auth.signOut()
  if (error) {
    console.warn('Supabase sign-out warning:', error.message)
  }
}

/**
 * Request password recovery email.
 * Redirects back to the CloudGuard app with recovery token in URL hash.
 */
export async function requestPasswordReset(email) {
  if (!supabase) {
    throw new Error('Supabase is not configured. Please set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY in .env.')
  }
  const redirectUrl = typeof window !== 'undefined'
    ? `${window.location.origin}/#type=recovery`
    : undefined

  const { data, error } = await supabase.auth.resetPasswordForEmail(email, {
    redirectTo: redirectUrl,
  })
  if (error) {
    throw error
  }
  return data
}

/**
 * Update user password after recovery.
 */
export async function updateUserPassword(newPassword) {
  if (!supabase) {
    throw new Error('Supabase is not configured. Please set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY in .env.')
  }
  const { data, error } = await supabase.auth.updateUser({
    password: newPassword,
  })
  if (error) {
    throw error
  }
  return data
}

/**
 * Get current Supabase session.
 */
export async function getSupabaseSession() {
  if (!supabase) return null
  const { data, error } = await supabase.auth.getSession()
  if (error) {
    console.warn('Error fetching Supabase session:', error.message)
    return null
  }
  return data.session
}

/**
 * Listen for Supabase auth state changes (SIGNED_IN, SIGNED_OUT, PASSWORD_RECOVERY, TOKEN_REFRESHED).
 */
export function onSupabaseAuthStateChange(callback) {
  if (!supabase) {
    return { data: { subscription: { unsubscribe: () => {} } } }
  }
  return supabase.auth.onAuthStateChange(callback)
}
