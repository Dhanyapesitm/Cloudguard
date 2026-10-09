import { useCallback, useEffect, useRef, useState } from 'react'

const API_URL = import.meta.env.VITE_API_URL || '/api'
const NAV_ITEMS = [
  { id: 'dashboard', label: 'Overview', icon: 'grid' },
  { id: 'documents', label: 'My documents', icon: 'file' },
  { id: 'recipients', label: 'Recipients', icon: 'users' },
  { id: 'nominee', label: 'Nominee', icon: 'shield' },
  { id: 'checkin', label: 'Live check-in', icon: 'scan' },
  { id: 'history', label: 'Check-in history', icon: 'clock' },
]
const SENTENCES = [
  'Today I am confirming my CloudGuard check-in.',
  'I am completing my secure digital check-in today.',
  'My important documents are safely stored with CloudGuard.',
  'I am here and completing my personal check-in.',
  'CloudGuard helps me keep the things that matter secure.',
]
const DOC_TYPES = ['Personal', 'Financial', 'Legal', 'Insurance', 'Certificates', 'Other']

function describeError(detail, status) {
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      const location = Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body').join(' → ') : ''
      return [location, item.msg].filter(Boolean).join(': ')
    })
    return messages.length ? messages.join(' ') : `Request failed (${status}).`
  }
  if (detail && typeof detail === 'object') {
    if (typeof detail.detail === 'string') return detail.detail
    if (typeof detail.message === 'string') return detail.message
    if (typeof detail.error === 'string') return detail.error
    return `Request failed (${status}).`
  }
  return typeof detail === 'string' && detail ? detail : `Request failed (${status}).`
}

async function api(path, { token, ...options } = {}) {
  const headers = new Headers(options.headers || {})
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (!(options.body instanceof FormData) && options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  let response
  try {
    response = await fetch(`${API_URL}${path}`, { ...options, headers })
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error('CloudGuard API is not reachable. From the project folder, run `npm run dev` to start both the website and API, then retry.')
    }
    throw error
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(describeError(body.detail, response.status))
  }
  if (response.status === 204) return null
  return response.json()
}

function Icon({ name, size = 19 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }
  const paths = {
    grid: <><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.5" /></>,
    file: <><path d="M13.5 3.5h-7A2.5 2.5 0 0 0 4 6v12a2.5 2.5 0 0 0 2.5 2.5h11A2.5 2.5 0 0 0 20 18V10z" /><path d="M13 3.5V10h7M8 14h8M8 17h6" /></>,
    users: <><circle cx="9" cy="8" r="3.3" /><path d="M3 20v-1.5a6 6 0 0 1 12 0V20zM16 5a3.2 3.2 0 0 1 0 6.2M18 14a5.5 5.5 0 0 1 3 4.5V20h-3" /></>,
    scan: <><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M8 12h8M12 8v8" /></>,
    clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></>,
    search: <><circle cx="10.8" cy="10.8" r="6.5" /><path d="m16 16 4.5 4.5" /></>,
    shield: <><path d="M12 3 19 6v5c0 4.8-3 8-7 10-4-2-7-5.2-7-10V6z" /><path d="m9 12 2 2 4-4" /></>,
    chevron: <path d="m7 10 5 5 5-5" />,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
    plus: <><path d="M12 5v14M5 12h14" /></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" /></>,
    download: <><path d="M12 3v12m-5-5 5 5 5-5M5 17v3h14v-3" /></>,
    trash: <><path d="M4 7h16M10 11v6m4-6v6M6 7l1 14h10l1-14M9 7V4h6v3" /></>,
    edit: <><path d="m14 5 5 5M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10z" /></>,
    logout: <><path d="M10 17l5-5-5-5M15 12H3M21 4v16" /></>,
    eye: <><path d="M2.5 12s3.3-6 9.5-6 9.5 6 9.5 6-3.3 6-9.5 6-9.5-6-9.5-6z" /><circle cx="12" cy="12" r="2.4" /></>,
    close: <><path d="m6 6 12 12M18 6 6 18" /></>,
  }
  return <svg {...common}>{paths[name] || paths.shield}</svg>
}

function initials(name = '') {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || 'CG'
}

function formatDate(value, options = { month: 'short', day: 'numeric', year: 'numeric' }) {
  if (!value) return 'Not yet completed'
  return new Intl.DateTimeFormat(undefined, options).format(new Date(value))
}

function formatBytes(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function AuthScreen({ onLogin, systemNotice }) {
  const [mode, setMode] = useState('login')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [faceSamples, setFaceSamples] = useState([])
  const [cameraOpen, setCameraOpen] = useState(false)
  const [captureProgress, setCaptureProgress] = useState(0)
  const [capturing, setCapturing] = useState(false)
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const cancelCaptureRef = useRef(false)

  useEffect(() => () => {
    cancelCaptureRef.current = true
    streamRef.current?.getTracks().forEach((track) => track.stop())
  }, [])

  function stopEnrollmentCamera() {
    cancelCaptureRef.current = true
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    setCameraOpen(false)
    setCapturing(false)
  }

  async function openEnrollmentCamera() {
    setError('')
    setNotice('')
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Camera access needs a secure browser context (HTTPS or localhost).')
      return
    }
    try {
      stopEnrollmentCamera()
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      })
      streamRef.current = stream
      const video = videoRef.current
      if (!video) throw new Error('The camera preview could not be opened. Please try again.')
      video.srcObject = stream
      await video.play()
      const deadline = Date.now() + 5000
      while (!video.videoWidth && Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 100))
      }
      if (!video.videoWidth) throw new Error('The camera did not start in time. Check browser permissions and try again.')
      cancelCaptureRef.current = false
      setCameraOpen(true)
    } catch (cameraError) {
      stopEnrollmentCamera()
      setError(cameraError instanceof Error && cameraError.name !== 'NotAllowedError'
        ? cameraError.message
        : 'Camera access is required. Allow camera permission in your browser and try again.')
    }
  }

  async function captureEnrollmentSamples() {
    const video = videoRef.current
    if (!streamRef.current || !video?.videoWidth || capturing) {
      setError('Start the camera and wait for your live preview before capturing samples.')
      return
    }
    setError('')
    cancelCaptureRef.current = false
    setCapturing(true)
    setCaptureProgress(0)
    const samples = []
    try {
      for (let index = 0; index < 5; index += 1) {
        if (cancelCaptureRef.current) return
        await new Promise((resolve) => window.setTimeout(resolve, 700))
        if (cancelCaptureRef.current || !videoRef.current?.videoWidth) return
        const currentVideo = videoRef.current
        const scale = Math.min(1, 720 / Math.max(currentVideo.videoWidth, currentVideo.videoHeight))
        const canvas = document.createElement('canvas')
        canvas.width = Math.round(currentVideo.videoWidth * scale)
        canvas.height = Math.round(currentVideo.videoHeight * scale)
        const context = canvas.getContext('2d')
        if (!context) throw new Error('Could not capture the camera image. Please try again.')
        context.drawImage(currentVideo, 0, 0, canvas.width, canvas.height)
        samples.push(canvas.toDataURL('image/jpeg', 0.78))
        setCaptureProgress(index + 1)
        setFaceSamples([...samples])
      }
      stopEnrollmentCamera()
    } catch (captureError) {
      setError(captureError.message || 'The camera sample could not be captured. Please try again.')
    } finally {
      setCapturing(false)
    }
  }

  async function submit(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const email = form.get('email')
    const password = form.get('password')
    setLoading(true)
    try {
      if (mode === 'register') {
        if (password !== form.get('confirmPassword')) throw new Error('Your passwords do not match.')
        if (faceSamples.length !== 5) throw new Error('Capture all 5 face samples to finish creating your account.')
        const response = await api('/auth/register', {
          method: 'POST',
          body: JSON.stringify({
            name: form.get('name'),
            email,
            phone: form.get('phone'),
            password,
            face_enrollment_samples_b64: faceSamples.map((sample) => sample.split(',')[1]),
          }),
        })
        setNotice(response.message)
        setMode('login')
        setFaceSamples([])
        stopEnrollmentCamera()
        formElement.reset()
      } else {
        const response = await api('/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        })
        onLogin(response)
      }
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="auth-page">
      <aside className="auth-story">
        <a className="brand brand-light" href="#" aria-label="CloudGuard home">
          <span className="brand-mark"><Icon name="shield" size={21} /></span>
          <span>cloudguard<span className="brand-period">.</span></span>
        </a>
        <div className="story-copy">
          <div className="eyebrow eyebrow-light"><span className="live-dot" /> YOUR LEGACY, IN SAFE HANDS</div>
          <h1>Keep what matters.<br /><em>Protected.</em></h1>
          <p>A private, thoughtfully designed home for your most important documents and the people you trust.</p>
          <div className="story-points">
            <div><span className="point-icon"><Icon name="lock" size={16} /></span><span><b>Private by design</b><small>Your files are encrypted before they are stored.</small></span></div>
            <div><span className="point-icon"><Icon name="scan" size={16} /></span><span><b>Stay connected</b><small>A simple check-in helps keep your account current.</small></span></div>
          </div>
        </div>
        <div className="story-footer"><span>Thoughtful security for life's important things.</span><span>01 — PRIVATE STORAGE</span></div>
      </aside>
      <section className="auth-main">
        <div className="mobile-brand brand"><span className="brand-mark"><Icon name="shield" size={20} /></span><span>cloudguard<span className="brand-period">.</span></span></div>
        <div className="auth-card">
          <div className="auth-heading">
            <span className="eyebrow">{mode === 'login' ? 'WELCOME BACK' : 'START YOUR VAULT'}</span>
            <h2>{mode === 'login' ? 'Sign in to your vault' : 'Create your account'}</h2>
            <p>{mode === 'login' ? 'Your important things are right where you left them.' : 'A few details to set up your private space.'}</p>
          </div>
          {notice && <div className="notice notice-success">{notice}</div>}
          {systemNotice && <div className="notice notice-warm">{systemNotice}</div>}
          {error && <div className="notice notice-error" role="alert">{error}</div>}
          <form className="form-stack" onSubmit={submit}>
            {mode === 'register' && <>
              <label>Full name<input name="name" type="text" placeholder="Your name" autoComplete="name" required minLength="2" /></label>
              <div className="input-row">
                <label>Email address<input name="email" type="email" placeholder="you@example.com" autoComplete="email" required /></label>
                <label>Phone number<input name="phone" type="tel" placeholder="+1 (555) 000-0000" autoComplete="tel" required minLength="5" /></label>
              </div>
            </>}
            {mode === 'login' && <label>Email address<input name="email" type="email" placeholder="you@example.com" autoComplete="email" required /></label>}
            <label>Password
              <span className="password-wrap">
                <input name="password" type={showPassword ? 'text' : 'password'} placeholder={mode === 'login' ? 'Enter your password' : 'At least 10 characters'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required minLength={mode === 'register' ? 10 : 1} />
                <button type="button" className="password-toggle" onClick={() => setShowPassword(!showPassword)} aria-label={showPassword ? 'Hide password' : 'Show password'}><Icon name="eye" size={18} /></button>
              </span>
            </label>
            {mode === 'register' && <>
              <label>Confirm password
                <span className="password-wrap">
                  <input name="confirmPassword" type={showConfirm ? 'text' : 'password'} placeholder="Enter your password again" autoComplete="new-password" required minLength="10" />
                  <button type="button" className="password-toggle" onClick={() => setShowConfirm(!showConfirm)} aria-label={showConfirm ? 'Hide password' : 'Show password'}><Icon name="eye" size={18} /></button>
                </span>
              </label>
              <div className="enrollment-card">
                <div className="enrollment-heading"><span className="mini-icon"><Icon name="scan" size={17} /></span><span><b>Face enrollment</b><small>Capture five face samples for your secure profile.</small></span><span className={`sample-count ${faceSamples.length === 5 ? 'sample-count-done' : ''}`}>{faceSamples.length}/5</span></div>
                <div className={`enrollment-camera ${cameraOpen ? 'enrollment-camera-active' : 'enrollment-camera-idle'}`}>
                  <div className="enrollment-preview">
                    <video className="enrollment-live-video" ref={videoRef} muted playsInline />
                    {cameraOpen && <><div className="face-guide"><span>Center your face here</span></div><span className="camera-live-label"><span className="live-dot" /> LIVE PREVIEW</span></>}
                  </div>
                  {cameraOpen && <>
                    <p className="camera-instructions">Look at the camera, keep your face inside the guide, and use even lighting. Five samples will be captured automatically.</p>
                    <div className="button-row">
                      <button type="button" className="button button-primary button-small" onClick={captureEnrollmentSamples} disabled={capturing}>{capturing ? `Capturing ${captureProgress}/5...` : 'Capture 5 samples'}</button>
                      <button type="button" className="button button-quiet button-small" onClick={stopEnrollmentCamera} disabled={capturing}>Cancel</button>
                    </div>
                    {capturing && <div className="capture-progress" aria-label={`${captureProgress} of 5 samples captured`}><span style={{ width: `${captureProgress * 20}%` }} /></div>}
                  </>}
                </div>
                {!cameraOpen && faceSamples.length < 5 && <div className="enrollment-start">
                  <p>{faceSamples.length ? `${faceSamples.length} of 5 captured. You can capture the remaining samples.` : 'Open the camera to see your live preview before taking samples.'}</p>
                  <button type="button" className="button button-secondary button-small" onClick={openEnrollmentCamera}>Open camera</button>
                </div>}
                {faceSamples.length > 0 && <div className="sample-strip">
                  {faceSamples.map((sample, index) => <div className="sample-thumb" key={`${index}-${sample.length}`}><img src={sample} alt={`Face enrollment sample ${index + 1}`} /><span>{String(index + 1).padStart(2, '0')}</span></div>)}
                  {faceSamples.length === 5 && <button type="button" className="text-button recapture-button" onClick={() => { setFaceSamples([]); setCaptureProgress(0); openEnrollmentCamera() }}>Retake all</button>}
                </div>}
              </div>
              <p className="field-hint">Your live preview stays on this device. The five captured images are encrypted when saved; face matching is not enabled yet.</p>
            </>}
            {mode === 'login' && <div className="form-meta"><span>Secure access to your personal vault</span><span className="secure-label"><Icon name="lock" size={13} /> ENCRYPTED</span></div>}
            <button className="button button-primary button-full" type="submit" disabled={loading}>{loading ? 'Please wait...' : mode === 'login' ? 'Sign in' : 'Create secure account'}<Icon name="arrow" size={17} /></button>
          </form>
          <p className="auth-switch">{mode === 'login' ? 'New to CloudGuard?' : 'Already have a CloudGuard account?'} <button type="button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(''); setNotice('') }}>{mode === 'login' ? 'Create an account' : 'Sign in'}</button></p>
          <div className="auth-footnote"><Icon name="lock" size={13} /> Your files stay yours. Always.</div>
        </div>
        <div className="auth-bottom">CLOUDGUARD <span>•</span> A PRIVATE PLACE FOR WHAT MATTERS</div>
      </section>
    </main>
  )
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem('cloudguard_token') || '')
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem('cloudguard_user') || 'null') } catch { return null }
  })
  const [page, setPage] = useState('dashboard')
  const [profileOpen, setProfileOpen] = useState(false)
  const [authNotice, setAuthNotice] = useState('')
  const [toast, setToast] = useState('')
  const [pageData, setPageData] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const notify = useCallback((message) => {
    setToast(message)
    window.setTimeout(() => setToast(''), 3200)
  }, [])

  const logout = useCallback(async () => {
    let revokeFailed = false
    if (token) {
      try {
        await api('/auth/logout', { token, method: 'POST' })
      } catch {
        revokeFailed = true
      }
    }
    localStorage.removeItem('cloudguard_token')
    localStorage.removeItem('cloudguard_user')
    setToken('')
    setUser(null)
    setPage('dashboard')
    setProfileOpen(false)
    setAuthNotice(revokeFailed ? 'Signed out on this device. Server session revocation could not be confirmed.' : 'You have been signed out.')
  }, [token])

  const load = useCallback(async () => {
    if (!token) return
    setBusy(true)
    setError('')
    const endpoints = {
      dashboard: '/dashboard',
      documents: '/documents',
      recipients: '/recipients',
      nominee: '/nominee',
      history: '/check-ins',
      profile: '/auth/me',
    }
    const endpoint = endpoints[page]
    if (!endpoint) { setBusy(false); return }
    try {
      const response = await api(endpoint, { token })
      setPageData(response)
      if (response.user) setUser(response.user)
    } catch (requestError) {
      if (requestError.message.toLowerCase().includes('session') || requestError.message.toLowerCase().includes('sign in')) logout()
      else setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }, [token, page, logout])

  useEffect(() => { load() }, [load])

  function handleLogin(response) {
    localStorage.setItem('cloudguard_token', response.access_token)
    localStorage.setItem('cloudguard_user', JSON.stringify(response.user))
    setToken(response.access_token)
    setUser(response.user)
    setPage('dashboard')
    setAuthNotice('')
  }

  function navigate(nextPage) {
    setPage(nextPage)
    setError('')
    setProfileOpen(false)
  }

  async function runAction(action) {
    setError('')
    try {
      await action()
      await load()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  if (!token || !user) return <AuthScreen onLogin={handleLogin} systemNotice={authNotice} />

  const currentPage = {
    dashboard: ['Your vault', 'A calm, clear view of everything you keep safe.'],
    documents: ['My documents', 'Your private collection, organized in one place.'],
    recipients: ['Trusted people', 'The people you have chosen to keep in the loop.'],
    nominee: ['Trusted nominee', 'Manage the one person you have designated as your nominee.'],
    checkin: ['Live check-in', 'Confirm that you are here with a short, personal check-in.'],
    history: ['Check-in history', 'A private record of your completed check-ins.'],
    profile: ['Your profile', 'Manage your account details and check-in preferences.'],
  }[page]

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand sidebar-brand" href="#" onClick={(event) => { event.preventDefault(); navigate('dashboard') }}>
          <span className="brand-mark"><Icon name="shield" size={20} /></span><span>cloudguard<span className="brand-period">.</span></span>
        </a>
        <div className="sidebar-label">YOUR SPACE</div>
        <nav className="side-nav" aria-label="Main navigation">
          {NAV_ITEMS.map((item) => <button key={item.id} className={`nav-link ${page === item.id ? 'active' : ''}`} onClick={() => navigate(item.id)}>
            <Icon name={item.icon} /><span>{item.label}</span>{item.id === 'documents' && pageData.documents?.length > 0 && <small>{pageData.documents.length}</small>}
          </button>)}
        </nav>
        <div className="sidebar-bottom">
          <div className="privacy-card"><span className="privacy-icon"><Icon name="lock" size={17} /></span><b>Your vault is private</b><p>Documents are encrypted and only accessible to you.</p></div>
          <div className="sidebar-profile">
            <span className="avatar avatar-small">{initials(user.name)}</span>
            <span className="profile-brief"><b>{user.name}</b><small>Personal vault</small></span>
            <button className="sidebar-more" onClick={() => setProfileOpen(!profileOpen)} aria-label="Open profile menu"><Icon name="chevron" size={17} /></button>
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>My space</span><span className="breadcrumb-divider">/</span><b>{currentPage[0]}</b></div>
          <div className="topbar-right">
            <span className="topbar-secure"><span className="live-dot" /> PRIVATE SPACE</span>
            <div className="profile-menu-wrap">
              <button className="profile-trigger" onClick={() => setProfileOpen(!profileOpen)} aria-expanded={profileOpen}>
                <span className="avatar">{initials(user.name)}</span><span className="profile-trigger-name">{user.name.split(' ')[0]}</span><Icon name="chevron" size={15} />
              </button>
              {profileOpen && <div className="profile-menu">
                <div className="menu-person"><b>{user.name}</b><small>{user.email}</small></div>
                <button onClick={() => navigate('profile')}><Icon name="users" size={16} /> Account settings</button>
                <button className="menu-logout" onClick={logout}><Icon name="logout" size={16} /> Sign out</button>
              </div>}
            </div>
          </div>
        </header>

        <div className="content-wrap">
          <div className="page-heading">
            <div><span className="eyebrow">CLOUDGUARD / {page.replace('-', ' ').toUpperCase()}</span><h1>{currentPage[0]}</h1><p>{currentPage[1]}</p></div>
            {page === 'dashboard' && <div className="today-chip"><span className="live-dot" /> YOUR SPACE IS SECURE</div>}
          </div>
          {error && <div className="notice notice-error global-notice" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss"><Icon name="close" size={16} /></button></div>}
          {busy && <div className="loading-line"><span /> Loading your private space...</div>}

          {page === 'dashboard' && <Dashboard data={pageData} onNavigate={navigate} />}
          {page === 'documents' && <Documents data={pageData.documents || []} token={token} onChange={load} />}
          {page === 'recipients' && <Recipients data={pageData.recipients || []} token={token} onChange={load} />}
          {page === 'nominee' && <Nominee data={pageData.nominee || null} token={token} onChange={load} />}
          {page === 'checkin' && <Checkin token={token} onComplete={async (checkin) => { await load(); notify(checkin.status === 'success' ? 'Check-in passed.' : 'Check-in recorded. Verification did not pass.') }} />}
          {page === 'history' && <History data={pageData.checkins || []} />}
          {page === 'profile' && <Profile data={user} token={token} onSave={(updated) => { setUser(updated); localStorage.setItem('cloudguard_user', JSON.stringify(updated)); notify('Your profile has been updated.') }} />}
        </div>
        <footer className="app-footer"><span>© {new Date().getFullYear()} CloudGuard</span><span><Icon name="lock" size={13} /> PRIVATE BY DESIGN</span></footer>
      </main>
      {toast && <div className="toast"><span className="toast-check">✓</span>{toast}</div>}
    </div>
  )
}

function Dashboard({ data, onNavigate }) {
  const user = data.user || {}
  const last = data.last_checkin
  const dueDate = data.next_checkin ? formatDate(data.next_checkin) : last ? 'After a passed check-in' : 'After your first check-in'
  return <div className="dashboard-page">
    <section className="welcome-panel">
      <div className="welcome-copy"><span className="welcome-kicker">A GOOD DAY TO CHECK IN</span><h2>Good to see you, {user.name?.split(' ')[0] || 'there'}.</h2><p>Your important things are safe. Here’s how your space is looking.</p>
        <button className="button button-light" onClick={() => onNavigate('checkin')}>Complete a check-in <Icon name="arrow" size={17} /></button>
      </div>
      <div className="welcome-art" aria-hidden="true"><div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" /><div className="art-shield"><Icon name="shield" size={58} /></div><span className="art-star" /><span className="art-spark" /></div>
    </section>

    <div className="stat-grid">
      <StatCard icon="shield" label="Account status" value="All set" detail="Your account is active" tone="mint" />
      <StatCard icon="clock" label="Last check-in" value={last ? formatDate(last.created_at) : 'Not yet'} detail={last?.status === 'success' ? 'Verification passed' : last?.status === 'failed' ? 'Verification did not pass' : last ? 'Verification unavailable' : 'Whenever you’re ready'} tone="lavender" />
      <StatCard icon="scan" label="Next check-in" value={dueDate} detail={`Every ${user.checkin_interval || 30} days`} tone="peach" />
      <StatCard icon="file" label="In your vault" value={`${data.documents_count || 0} documents`} detail="Encrypted and private" tone="sky" />
    </div>

    <div className="dashboard-lower">
      <section className="surface checkin-card">
        <div className="section-title-row"><div><span className="eyebrow">STAY IN THE LOOP</span><h3>Your regular check-in</h3></div><span className="soft-tag tag-unverified">VERIFICATION REQUIRED</span></div>
        <p>Take a moment to confirm you’re here. The check-in experience uses your camera and microphone to record a short sample.</p>
        <div className="checkin-note"><Icon name="shield" size={16} /><span>Your check-in is marked passed only when face match, anti-spoofing, and speech checks all succeed.</span></div>
        <button className="button button-primary" onClick={() => onNavigate('checkin')}>Start a check-in <Icon name="arrow" size={17} /></button>
      </section>
      <section className="surface trusted-card">
        <div className="section-title-row"><div><span className="eyebrow">YOUR CIRCLE</span><h3>Trusted people</h3></div><button className="icon-button" onClick={() => onNavigate('recipients')} aria-label="View trusted people"><Icon name="arrow" size={18} /></button></div>
        <p>Recipients are contacts you’ve chosen. They cannot see or access your documents in this prototype.</p>
        <div className="trusted-count"><span className="trusted-count-icon"><Icon name="users" size={18} /></span><span><b>{data.recipients_count || 0} trusted {data.recipients_count === 1 ? 'person' : 'people'}</b><small>Add or update your people</small></span></div>
        <button className="text-link" onClick={() => onNavigate('recipients')}>Manage trusted people <Icon name="arrow" size={15} /></button>
        <button className="text-link dashboard-nominee-link" onClick={() => onNavigate('nominee')}>Manage nominee <Icon name="arrow" size={15} /></button>
      </section>
    </div>
    <section className="surface quick-access">
      <div className="section-title-row"><div><span className="eyebrow">YOUR PRIVATE COLLECTION</span><h3>Make space for what matters</h3></div><button className="text-link" onClick={() => onNavigate('documents')}>Go to documents <Icon name="arrow" size={15} /></button></div>
      <p>Store important papers, policies and files in one encrypted place. Only your signed-in account can download them.</p>
      <div className="quick-categories">{['Personal', 'Financial', 'Legal', 'Insurance'].map((item, index) => <button key={item} className={`category-pill category-${index}`} onClick={() => onNavigate('documents')}><span className="category-dot" />{item}<Icon name="arrow" size={14} /></button>)}</div>
    </section>
  </div>
}

function StatCard({ icon, label, value, detail, tone }) {
  return <article className="stat-card"><span className={`stat-icon stat-${tone}`}><Icon name={icon} size={19} /></span><span className="stat-label">{label}</span><b className="stat-value">{value}</b><small>{detail}</small></article>
}

function Documents({ data, token, onChange }) {
  const [expanded, setExpanded] = useState(false)
  const [selectedFile, setSelectedFile] = useState(null)
  const [documentName, setDocumentName] = useState('')
  const [category, setCategory] = useState('Personal')
  const [filter, setFilter] = useState('All files')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const inputRef = useRef(null)
  const visible = data.filter((item) => (filter === 'All files' || item.category === filter) && `${item.name} ${item.file_name}`.toLowerCase().includes(search.toLowerCase()))

  function selectFile(file) {
    if (!file) return
    setSelectedFile(file)
    setDocumentName((currentName) => currentName.trim() ? currentName : file.name.replace(/\.[^.]+$/, ''))
    setMessage('')
  }

  async function submitUpload(event) {
    event.preventDefault()
    setMessage('')
    if (!selectedFile) { setMessage('Choose a file to upload.'); return }
    if (!documentName.trim()) { setMessage('Enter a display name for this document.'); return }
    const formData = new FormData()
    formData.set('name', documentName.trim())
    formData.set('category', category)
    formData.set('file', selectedFile, selectedFile.name)
    setLoading(true)
    try {
      const response = await api('/documents', { token, method: 'POST', body: formData })
      setMessage(`${response.document.name} has been encrypted and saved to your vault.`)
      setSelectedFile(null)
      setDocumentName('')
      setCategory('Personal')
      if (inputRef.current) inputRef.current.value = ''
      setExpanded(false)
      await onChange()
    } catch (error) {
      setMessage(error.message)
    } finally { setLoading(false) }
  }

  async function download(item) {
    try {
      const response = await fetch(`${API_URL}/documents/${item.id}/download`, { headers: { Authorization: `Bearer ${token}` } })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(describeError(body.detail, response.status))
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${item.file_name}.cgenc`
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { setMessage(error.message) }
  }

  async function remove(item) {
    if (!window.confirm(`Delete “${item.name}” from your private vault? This cannot be undone.`)) return
    try {
      await api(`/documents/${item.id}`, { token, method: 'DELETE' })
      setMessage('Document deleted from your vault.')
      await onChange()
    } catch (error) { setMessage(error.message) }
  }

  return <div className="page-content">
    <div className="notice notice-warm"><span className="notice-icon"><Icon name="lock" size={17} /></span><span><b>Private, encrypted storage</b><small>Files are encrypted with AES-256-GCM before being saved. Only you can access them. No automatic document sharing is enabled.</small></span></div>
    <section className="surface upload-surface">
      <div className="section-title-row"><div><span className="eyebrow">ADD TO YOUR VAULT</span><h3>Upload a document</h3></div><span className="soft-tag">{data.length} {data.length === 1 ? 'FILE' : 'FILES'}</span></div>
      {!expanded ? <div className="upload-prompt"><div className="upload-icon"><Icon name="plus" size={23} /></div><div><b>Keep an important file close</b><p>Upload a document, photo, audio clip or archive. Up to 25 MB per file.</p></div><button className="button button-primary" onClick={() => setExpanded(true)}>Choose a file <Icon name="arrow" size={16} /></button></div> :
      <form className="upload-form" onSubmit={submitUpload}>
        <div className="upload-drop" onClick={() => inputRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); selectFile(event.dataTransfer.files[0]) }}>
          <span className="upload-icon"><Icon name="file" size={23} /></span><b>{selectedFile ? selectedFile.name : 'Drop a file here or browse'}</b><small>{selectedFile ? formatBytes(selectedFile.size) : 'PDF, TXT, Office files, images, audio, video & archives · 25 MB max'}</small>
          <input ref={inputRef} type="file" hidden accept=".pdf,.txt,.rtf,.doc,.docx,.odt,.xls,.xlsx,.ods,.csv,.ppt,.pptx,.jpg,.jpeg,.png,.gif,.webp,.heic,.tif,.tiff,.bmp,.mp3,.wav,.m4a,.mp4,.mov,.avi,.zip,.7z,.rar,.json,.xml" onChange={(event) => selectFile(event.target.files[0])} />
        </div>
        <div className="input-row upload-fields"><label>Display name<input name="name" required minLength="2" maxLength="120" placeholder="e.g. Home insurance policy" value={documentName} onChange={(event) => setDocumentName(event.target.value)} /></label><label>Collection<select name="category" value={category} onChange={(event) => setCategory(event.target.value)}>{DOC_TYPES.map((type) => <option key={type}>{type}</option>)}</select></label></div>
        {message && <div className={`notice ${message.includes('encrypted') || message.includes('deleted') ? 'notice-success' : 'notice-error'}`}>{message}</div>}
        <div className="button-row"><button className="button button-primary" type="submit" disabled={loading}>{loading ? 'Encrypting...' : 'Encrypt and save'} <Icon name="lock" size={16} /></button><button className="button button-quiet" type="button" onClick={() => { setExpanded(false); setSelectedFile(null); setDocumentName(''); setCategory('Personal'); setMessage(''); if (inputRef.current) inputRef.current.value = '' }}>Cancel</button></div>
      </form>}
    </section>
    <section className="surface document-list-surface">
      <div className="section-title-row document-list-heading"><div><span className="eyebrow">YOUR COLLECTION</span><h3>Saved documents <span className="count-badge">{data.length}</span></h3></div><div className="list-controls"><div className="search-field"><Icon name="search" size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a file" aria-label="Search documents" /></div><select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter documents"><option>All files</option>{DOC_TYPES.map((type) => <option key={type}>{type}</option>)}</select></div></div>
      {message && !expanded && <div className="notice notice-success list-notice">{message}</div>}
      {visible.length ? <div className="document-table-wrap"><table className="data-table document-table"><thead><tr><th>DOCUMENT</th><th>COLLECTION</th><th>FILE SIZE</th><th>ADDED</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{visible.map((item) => <tr key={item.id}>
        <td><span className="file-name-cell"><span className={`file-type-icon ${item.file_name.split('.').pop().toLowerCase() === 'pdf' ? 'pdf-icon' : ''}`}><Icon name="file" size={17} /></span><span><b>{item.name}</b><small>{item.file_name}</small></span></span></td>
        <td><span className="soft-tag tag-collection">{item.category}</span></td><td>{formatBytes(item.file_size)}</td><td>{formatDate(item.created_at, { month: 'short', day: 'numeric', year: 'numeric' })}</td>
        <td><div className="table-actions"><button className="icon-button" onClick={() => download(item)} aria-label={`Download encrypted ${item.name}`} title="Download encrypted"><Icon name="download" size={17} /></button><button className="icon-button danger-icon" onClick={() => remove(item)} aria-label={`Delete ${item.name}`} title="Delete"><Icon name="trash" size={17} /></button></div></td>
      </tr>)}</tbody></table></div> : <div className="empty-state"><span className="empty-icon"><Icon name="file" size={25} /></span><b>{search || filter !== 'All files' ? 'No matching documents' : 'Your vault is ready for its first document'}</b><p>{search || filter !== 'All files' ? 'Try another search or collection.' : 'Add a policy, an important record, or anything you would like to keep safe.'}</p>{!data.length && <button className="text-link" onClick={() => setExpanded(true)}>Upload your first document <Icon name="arrow" size={15} /></button>}</div>}
      <div className="surface-footnote"><Icon name="lock" size={14} /> AES-256-GCM ENCRYPTED <span>·</span> PRIVATE TO YOUR ACCOUNT</div>
    </section>
  </div>
}

function Recipients({ data, token, onChange }) {
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState({ name: '', email: '', relationship: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  function beginEdit(item) {
    setEditing(item.id)
    setForm({ name: item.name, email: item.email, relationship: item.relationship })
  }

  async function submit(event) {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      await api(editing ? `/recipients/${editing}` : '/recipients', {
        token,
        method: editing ? 'PATCH' : 'POST',
        body: JSON.stringify(form),
      })
      setForm({ name: '', email: '', relationship: '' })
      setEditing(null)
      await onChange()
    } catch (requestError) { setError(requestError.message) }
    finally { setLoading(false) }
  }

  async function remove(item) {
    if (!window.confirm(`Remove ${item.name} from your trusted people?`)) return
    try { await api(`/recipients/${item.id}`, { token, method: 'DELETE' }); await onChange() }
    catch (requestError) { setError(requestError.message) }
  }

  return <div className="page-content recipient-page">
    <div className="notice notice-warm"><span className="notice-icon"><Icon name="users" size={17} /></span><span><b>Trusted contacts are not document recipients yet</b><small>These contacts are saved for your records only. They do not have accounts and cannot see or download your files.</small></span></div>
    <div className="two-column-layout">
      <section className="surface recipient-form-surface">
        <div className="section-title-row"><div><span className="eyebrow">{editing ? 'UPDATE CONTACT' : 'YOUR TRUSTED CIRCLE'}</span><h3>{editing ? 'Edit trusted person' : 'Add a trusted person'}</h3></div><span className="soft-tag">{data.length} SAVED</span></div>
        <p className="surface-description">Keep a trusted person's details close. You can change or remove them at any time.</p>
        <form className="form-stack recipient-form" onSubmit={submit}>
          <label>Full name<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Their full name" required minLength="2" maxLength="100" /></label>
          <label>Email address<input value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} type="email" placeholder="name@example.com" required /></label>
          <label>Relationship<input value={form.relationship} onChange={(event) => setForm({ ...form, relationship: event.target.value })} placeholder="e.g. Sister, close friend, attorney" required minLength="2" maxLength="60" /></label>
          {error && <div className="notice notice-error">{error}</div>}
          <div className="button-row"><button type="submit" className="button button-primary" disabled={loading}>{loading ? 'Saving...' : editing ? 'Save changes' : 'Add trusted person'} <Icon name={editing ? 'edit' : 'plus'} size={16} /></button>{editing && <button type="button" className="button button-quiet" onClick={() => { setEditing(null); setForm({ name: '', email: '', relationship: '' }) }}>Cancel</button>}</div>
        </form>
        <div className="recipient-info"><Icon name="shield" size={16} /><span>For this first version, trusted contacts are saved as contact information only. No portal, account invitation or document sharing is active.</span></div>
      </section>
      <section className="surface recipient-list-surface">
        <div className="section-title-row"><div><span className="eyebrow">YOUR PEOPLE</span><h3>Trusted people</h3></div></div>
        {data.length ? <div className="recipient-list">{data.map((item) => <article className="recipient-item" key={item.id}>
          <span className="avatar recipient-avatar">{initials(item.name)}</span><span className="recipient-details"><b>{item.name}</b><small>{item.email}</small><span className="relationship-tag">{item.relationship}</span></span>
          <span className="recipient-actions"><button className="icon-button" onClick={() => beginEdit(item)} aria-label={`Edit ${item.name}`}><Icon name="edit" size={16} /></button><button className="icon-button danger-icon" onClick={() => remove(item)} aria-label={`Remove ${item.name}`}><Icon name="trash" size={16} /></button></span>
        </article>)}</div> : <div className="empty-state compact-empty"><span className="empty-icon"><Icon name="users" size={23} /></span><b>Your circle starts here</b><p>Add a trusted contact to keep their details with your records.</p></div>}
      </section>
    </div>
    <section className="surface recipient-future"><span className="future-lock"><Icon name="lock" size={19} /></span><span><b>Recipient access is intentionally not enabled</b><small>In a later phase, recipients can be invited to a separate portal. They should verify their email, and access to any document must remain locked until the owner-defined release and verification process is implemented. No automatic release happens in this prototype.</small></span></section>
  </div>
}

function Nominee({ data, token, onChange }) {
  const [form, setForm] = useState({
    name: data?.name || '',
    email: data?.email || '',
    relationship: data?.relationship || '',
    phone: data?.phone || '',
  })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setForm({
      name: data?.name || '',
      email: data?.email || '',
      relationship: data?.relationship || '',
      phone: data?.phone || '',
    })
  }, [data?.name, data?.email, data?.relationship, data?.phone])

  async function submit(event) {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      await api('/nominee', {
        token,
        method: 'PUT',
        body: JSON.stringify(form),
      })
      await onChange()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  async function remove() {
    if (!window.confirm('Remove your trusted nominee from your account?')) return
    setError('')
    try {
      await api('/nominee', { token, method: 'DELETE' })
      await onChange()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  return <div className="page-content nominee-page">
    <div className="notice notice-warm"><span className="notice-icon"><Icon name="shield" size={17} /></span><span><b>Your nominee is different from your recipients</b><small>Choose one trusted contact for your records. This prototype does not verify nominees, create nominee logins, or grant access to your documents.</small></span></div>
    <div className="two-column-layout">
      <section className="surface nominee-form-surface">
        <div className="section-title-row"><div><span className="eyebrow">{data ? 'DESIGNATED CONTACT' : 'YOUR TRUSTED NOMINEE'}</span><h3>{data ? 'Your nominee' : 'Designate a nominee'}</h3></div><span className={`soft-tag ${data ? 'nominee-saved-tag' : ''}`}>{data ? 'SAVED' : 'ONE PERSON'}</span></div>
        <p className="surface-description">Keep one trusted person’s contact information here. You can update or remove it whenever you need.</p>
        <form className="form-stack recipient-form" onSubmit={submit}>
          <label>Nominee name<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Their full name" required minLength="2" maxLength="100" /></label>
          <label>Nominee email<input value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} type="email" placeholder="name@example.com" required /></label>
          <label>Relationship<input value={form.relationship} onChange={(event) => setForm({ ...form, relationship: event.target.value })} placeholder="e.g. Spouse, sibling, close friend" required minLength="2" maxLength="60" /></label>
          <label>Phone number <span className="optional-label">OPTIONAL</span><input value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} type="tel" placeholder="+1 (555) 000-0000" maxLength="32" /></label>
          {error && <div className="notice notice-error">{error}</div>}
          <div className="button-row"><button type="submit" className="button button-primary" disabled={loading}>{loading ? 'Saving...' : data ? 'Save nominee' : 'Save nominee'} <Icon name="arrow" size={16} /></button>{data && <button type="button" className="button button-quiet nominee-remove" onClick={remove}>Remove nominee</button>}</div>
        </form>
      </section>
      <section className="surface nominee-explainer">
        <span className="nominee-illustration"><Icon name="shield" size={28} /></span>
        <span className="eyebrow">A DESIGNATED CONTACT</span>
        <h3>One person, kept separate</h3>
        <p>Your nominee is stored separately from your authorized recipients. Their details are visible only in your account.</p>
        {data ? <div className="nominee-preview"><span className="avatar">{initials(data.name)}</span><span><b>{data.name}</b><small>{data.relationship} · {data.email}</small></span></div> : <div className="nominee-preview nominee-empty"><Icon name="users" size={17} /><span>No nominee has been added yet.</span></div>}
      </section>
    </div>
    <section className="surface nominee-boundary"><Icon name="lock" size={17} /><span><b>Nominee access is not enabled</b><small>Saving a nominee does not invite them to sign in or allow them to see, download, or receive documents. A separate approval and verification workflow would be needed before adding those capabilities.</small></span></section>
  </div>
}

function Checkin({ token, onComplete }) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const recorderRef = useRef(null)
  const timerRef = useRef(null)
  const [step, setStep] = useState('ready')
  const [streamOn, setStreamOn] = useState(false)
  const [seconds, setSeconds] = useState(12)
  const [sentence, setSentence] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [hasClip, setHasClip] = useState(false)
  const [loading, setLoading] = useState(false)
  const recordingChunks = useRef([])
  const recordingClip = useRef(null)
  const [verificationReady, setVerificationReady] = useState(null)

  useEffect(() => () => {
    window.clearInterval(timerRef.current)
    streamRef.current?.getTracks().forEach((track) => track.stop())
  }, [])

  useEffect(() => {
    let active = true
    api('/health')
      .then((health) => { if (active) setVerificationReady(health.ai_verification_configured === true) })
      .catch(() => { if (active) setVerificationReady(false) })
    return () => { active = false }
  }, [])

  async function openCamera() {
    setError('')
    setResult(null)
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Camera access needs a secure browser context (HTTPS or localhost).')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } }, audio: { echoCancellation: true, noiseSuppression: true } })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
      }
      setStreamOn(true)
      setSentence(SENTENCES[Math.floor(Math.random() * SENTENCES.length)])
      setStep('camera')
    } catch {
      setError('Please allow camera and microphone access in your browser to continue.')
    }
  }

  function startRecording() {
    setError('')
    setResult(null)
    const stream = streamRef.current
    if (!stream || !window.MediaRecorder) {
      setError('This browser cannot record a check-in. Try the latest version of Chrome, Edge or Firefox.')
      return
    }
    try {
      const mimeType = typeof MediaRecorder.isTypeSupported === 'function'
        ? ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4']
          .find((type) => MediaRecorder.isTypeSupported(type))
        : undefined
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      recorderRef.current = recorder
      recordingChunks.current = []
      recordingClip.current = null
      setHasClip(false)
      recorder.ondataavailable = (event) => {
        if (event.data.size) recordingChunks.current.push(event.data)
      }
      recorder.onstop = () => {
        const clip = new Blob(recordingChunks.current, { type: recorder.mimeType || 'video/webm' })
        recordingClip.current = clip
        setHasClip(clip.size > 0)
        setStep('recorded')
      }
      recorder.start()
      setSeconds(10)
      setStep('recording')
      timerRef.current = window.setInterval(() => setSeconds((remaining) => {
        if (remaining <= 1) {
          window.clearInterval(timerRef.current)
          recorder.stop()
          return 0
        }
        return remaining - 1
      }), 1000)
    } catch {
      setError('This browser could not start a recording. Check camera permissions and try again.')
    }
  }

  function stopCamera() {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    setStreamOn(false)
    setStep('ready')
    setHasClip(false)
    recordingClip.current = null
  }

  async function saveCheckin() {
    setLoading(true)
    setError('')
    try {
      const clip = recordingClip.current
      if (!clip?.size) throw new Error('No check-in recording is available. Please record again.')
      const form = new FormData()
      form.append('sentence', sentence)
      form.append('recording', clip, `check-in.${clip.type.includes('mp4') ? 'mp4' : 'webm'}`)
      const response = await api('/check-ins', { token, method: 'POST', body: form })
      setResult(response.checkin)
      recordingClip.current = null
      setStep('result')
      await onComplete(response.checkin)
    } catch (requestError) { setError(requestError.message) }
    finally { setLoading(false) }
  }

  return <div className="page-content checkin-page">
    <div className="verification-banner"><span className="verification-banner-icon"><Icon name="scan" size={18} /></span><span><b>{verificationReady ? 'Local verification is configured' : 'Local verification is unavailable'}</b><small>{verificationReady ? 'This server checks face match, anti-spoofing, and the spoken phrase, then deletes the clip. All three checks must pass; first use may download model weights.' : 'Check-ins require the local face, liveness, and speech models. No result will be recorded unless every check completes.'}</small></span><span className={`verification-indicator ${verificationReady ? 'verification-ready' : ''}`}><span className="live-dot" /> {verificationReady ? 'AI CONFIGURED' : 'UNAVAILABLE'}</span></div>
    <div className="checkin-layout">
      <section className="surface checkin-flow">
        <div className="section-title-row"><div><span className="eyebrow">A MOMENT FOR YOU</span><h3>Complete a check-in</h3></div><span className="checkin-duration"><Icon name="clock" size={14} /> ABOUT 12 SEC</span></div>
        <div className="checkin-steps">
          <div className={`checkin-step ${step !== 'ready' ? 'step-done' : 'step-current'}`}><span>01</span><div><b>Allow camera & microphone</b><small>They’re used only for this check-in.</small></div>{step !== 'ready' && <span className="step-check">✓</span>}</div>
          <div className={`checkin-step ${['camera', 'recording', 'recorded', 'result'].includes(step) ? 'step-current' : ''}`}><span>02</span><div><b>Read your personal phrase</b><small>A new phrase is chosen each time.</small></div></div>
          <div className={`checkin-step ${['recording', 'recorded', 'result'].includes(step) ? 'step-current' : ''}`}><span>03</span><div><b>Record a short live clip</b><small>Keep your face in frame as you speak.</small></div></div>
          <div className={`checkin-step ${step === 'result' ? 'step-current' : ''}`}><span>04</span><div><b>Review your result</b><small>See whether verification passed.</small></div></div>
        </div>
        {['camera', 'recording', 'recorded'].includes(step) && <div className="checkin-phrase"><span className="eyebrow">READ THIS PHRASE ALOUD</span><p>"{sentence}"</p><small>Speak clearly and naturally while looking at the camera.</small></div>}
        {error && <div className="notice notice-error">{error}</div>}
        {step === 'ready' && <div className="checkin-tips"><Icon name="shield" size={15} /><span><b>Tips for a successful check-in:</b><ul style={{margin:'6px 0 0 0',paddingLeft:'18px',fontSize:'0.85em'}}><li>Face the light source - good lighting is key.</li><li>Centre your face in the camera frame.</li><li>Speak the phrase clearly at a natural pace.</li><li>Use the same camera as during enrollment.</li></ul></span></div>}
        {step === 'ready' && <div className="checkin-privacy"><Icon name="lock" size={15} /><span>Your recording is sent to this server for verification, then deleted. Only the phrase and check results are saved.</span></div>}
        {step === 'ready' && <button className="button button-primary" onClick={openCamera}>Begin check-in <Icon name="arrow" size={17} /></button>}
        {step === 'camera' && <div className="phrase-panel"><div className="button-row"><button className="button button-primary" onClick={startRecording}><span className="record-dot" /> Start 12-second recording</button><button className="button button-quiet" onClick={stopCamera}>Cancel</button></div></div>}
        {step === 'recording' && <div className="recording-panel"><span className="recording-pulse" /><b>Recording — read the phrase above aloud</b><span className="timer-count">{seconds}<small>sec</small></span><div className="recording-progress-bar"><span style={{ width: `${((12 - seconds) / 12) * 100}%` }} /></div><small>Speak naturally and keep your face centred in the camera.</small></div>}
        {step === 'recorded' && <div className="phrase-panel"><span className="eyebrow">RECORDING COMPLETE</span><div className="checkin-privacy"><Icon name="shield" size={15} /><span>{hasClip ? 'Submit the clip to run face, liveness, and speech checks. The server deletes it after analysis.' : 'No recording data was captured. Please record again.'}</span></div><div className="button-row"><button className="button button-primary" onClick={saveCheckin} disabled={loading || !hasClip}>{loading ? 'Verifying check-in...' : 'Verify check-in'} <Icon name="arrow" size={17} /></button><button className="button button-quiet" onClick={() => { setStep('camera'); setResult(null); setHasClip(false); recordingClip.current = null }}>Try again</button></div></div>}
        {step === 'result' && result && <div className="result-panel"><span className="result-checkin-icon"><Icon name="shield" size={23} /></span><span className="eyebrow">CHECK-IN RESULT</span><h3>{result.status === 'success' ? 'Check-in passed' : 'Check-in not passed'}</h3><p>{result.status === 'success' ? 'Face match, anti-spoof, and spoken phrase all passed.' : 'At least one verification check did not pass. See the details below and try again.'}</p><div className="overall-result"><span>Verification status</span><b>{result.status === 'success' ? 'PASSED' : 'NOT PASSED'}</b></div><div className="result-checks"><div><span>Face verification</span><b className={result.face_status === 'passed' ? 'available-status' : 'unavailable-status'}>{result.face_status === 'passed' ? 'Passed' : 'Not passed'}</b></div><div><span>Anti-spoof liveness</span><b className={result.liveness_status === 'passed' ? 'available-status' : 'unavailable-status'}>{result.liveness_status === 'passed' ? 'Passed' : 'Not passed'}</b></div><div><span>Speech match</span><b className={result.speech_status === 'passed' ? 'available-status' : 'unavailable-status'}>{result.speech_status === 'passed' ? 'Passed' : 'Not passed'}</b></div></div><small className="result-time">Saved {formatDate(result.created_at, { dateStyle: 'medium', timeStyle: 'short' })}</small>{result.status !== 'success' && <div className="result-hints">{[['face_status','Face not matched � ensure good lighting and centre your face, matching your enrollment photos.'],['liveness_status','Liveness check failed � use a live camera, not a photo or screen replay.'],['speech_status','Speech not matched � speak the phrase clearly at a normal pace, close to the microphone.']].filter(([key]) => result[key] !== 'passed').map(([key, hint]) => <div key={key} className="result-hint-item"><Icon name="close" size={14} /><span>{hint}</span></div>)}</div>}<div className="button-row" style={{marginTop:'12px'}}><button className="button button-secondary" onClick={() => { stopCamera(); setResult(null) }}>Done</button>{result.status !== 'success' && <button className="button button-quiet" onClick={() => { setStep('camera'); setResult(null); setHasClip(false); recordingClip.current = null }}>Try again</button>}</div></div>}
        <video className={`checkin-video ${streamOn ? 'video-visible' : ''}`} ref={videoRef} muted playsInline />
      </section>
      <aside className="checkin-aside">
        <section className="surface aside-card"><span className="aside-icon"><Icon name="shield" size={19} /></span><span className="eyebrow">YOUR PRIVACY MATTERS</span><h3>Private verification</h3><p>The recording is analyzed by this server and deleted after processing. Only verification results are retained.</p><div className="aside-separator" /><span className="aside-detail"><Icon name="lock" size={15} /> All three checks are required to pass.</span></section>
        <section className="surface aside-card" style={{marginTop:'12px'}}><span className="aside-icon"><Icon name="scan" size={19} /></span><span className="eyebrow">FACE MATCHING</span><h3>Matched to enrollment</h3><p>Your live face is compared to the 5 photos captured during registration. Use the same camera and good lighting.</p></section>
      </aside>
    </div>
  </div>
}

function History({ data }) {
  return <div className="page-content history-page">
    <div className="history-summary"><div><span className="eyebrow">YOUR PRIVATE RECORD</span><b>{data.length}</b><small>{data.length === 1 ? 'check-in saved' : 'check-ins saved'}</small></div><span className="history-summary-icon"><Icon name="clock" size={23} /></span></div>
    <section className="surface history-surface"><div className="section-title-row"><div><span className="eyebrow">ACTIVITY</span><h3>Recent check-ins</h3></div><span className="soft-tag">LATEST 100</span></div>
      {data.length ? <div className="history-list">{data.map((item, index) => {
        const passed = item.status === 'success'
        const verifiedFailure = item.status === 'failed'
        const verificationLabel = passed ? 'PASSED' : verifiedFailure ? 'NOT PASSED' : 'NOT VERIFIED'
        const checkLabel = (value) => ({ passed: 'Passed', failed: 'Failed', not_configured: 'Not checked', demo: 'Not checked' }[value] || value)
        return <article className="history-item" key={item.id}><span className="timeline-dot">{passed ? '✓' : '!'}</span><div className="history-main"><div><b>Live check-in</b><span className={`status-pill ${passed ? 'status-success' : 'status-unverified'}`}>{verificationLabel}</span></div><p>{formatDate(item.created_at, { dateStyle: 'full', timeStyle: 'short' })}</p><div className="history-checks"><span>Face <b>{checkLabel(item.face_status)}</b></span><span>Liveness <b>{checkLabel(item.liveness_status)}</b></span><span>Speech <b>{checkLabel(item.speech_status)}</b></span></div></div><span className="history-index">{String(data.length - index).padStart(2, '0')}</span></article>
      })}</div> : <div className="empty-state"><span className="empty-icon"><Icon name="clock" size={25} /></span><b>No check-ins yet</b><p>Your personal activity will appear here after your first check-in.</p></div>}
      <div className="surface-footnote"><Icon name="shield" size={14} /> ONLY VISIBLE TO YOUR ACCOUNT</div>
    </section>
  </div>
}

function Profile({ data, token, onSave }) {
  const [form, setForm] = useState({ name: data.name || '', phone: data.phone || '', checkin_interval: data.checkin_interval || 30 })
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(false)

  useEffect(() => setForm({ name: data.name || '', phone: data.phone || '', checkin_interval: data.checkin_interval || 30 }), [data.name, data.phone, data.checkin_interval])

  async function submit(event) {
    event.preventDefault()
    setError('')
    setSaved(false)
    setLoading(true)
    try {
      const response = await api('/profile', { token, method: 'PATCH', body: JSON.stringify({ ...form, checkin_interval: Number(form.checkin_interval) }) })
      onSave(response.user)
      setSaved(true)
    } catch (requestError) { setError(requestError.message) }
    finally { setLoading(false) }
  }

  return <div className="page-content profile-page">
    <section className="surface profile-card">
      <div className="profile-cover"><div className="cover-orb cover-one" /><div className="cover-orb cover-two" /><span className="avatar profile-avatar">{initials(data.name)}</span></div>
      <div className="profile-card-heading"><div><span className="eyebrow">PERSONAL ACCOUNT</span><h3>{data.name}</h3><p>{data.email}</p></div><span className="account-active"><span className="live-dot" /> ACTIVE</span></div>
      <form className="profile-form" onSubmit={submit}>
        <div className="section-title-row"><div><span className="eyebrow">ACCOUNT DETAILS</span><h3>Your information</h3></div></div>
        <div className="input-row"><label>Full name<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} required minLength="2" maxLength="100" /></label><label>Email address<input value={data.email || ''} type="email" disabled /><small className="field-hint">Email address cannot be changed here.</small></label></div>
        <label>Phone number<input value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} required minLength="5" maxLength="32" /></label>
        <div className="profile-divider" />
        <div className="section-title-row schedule-heading"><div><span className="eyebrow">YOUR ROUTINE</span><h3>Check-in preferences</h3></div><span className="soft-tag">OPTIONAL REMINDER</span></div>
        <p className="surface-description">Choose a preferred check-in interval for your dashboard. Automated reminders are not enabled in this prototype.</p>
        <label>Preferred interval<select value={form.checkin_interval} onChange={(event) => setForm({ ...form, checkin_interval: event.target.value })}><option value="7">Every week</option><option value="14">Every 2 weeks</option><option value="30">Every month</option><option value="60">Every 2 months</option><option value="90">Every 3 months</option><option value="180">Every 6 months</option><option value="365">Every year</option></select></label>
        {error && <div className="notice notice-error">{error}</div>}{saved && <div className="notice notice-success">Your account details have been saved.</div>}
        <button type="submit" className="button button-primary" disabled={loading}>{loading ? 'Saving...' : 'Save changes'} <Icon name="arrow" size={16} /></button>
      </form>
    </section>
    <section className="surface security-info"><span className="security-info-icon"><Icon name="lock" size={19} /></span><div><b>Your security at a glance</b><p>Your password is stored using bcrypt. Documents and face enrollment images are encrypted at rest with AES-256-GCM. Your account token expires after 12 hours.</p></div><span className="soft-tag tag-secure">SECURE</span></section>
  </div>
}

export default App
