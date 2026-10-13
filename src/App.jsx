import { Fragment, useCallback, useEffect, useRef, useState } from 'react'

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

async function api(path, { token, timeoutMs, ...options } = {}) {
  const headers = new Headers(options.headers || {})
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (!(options.body instanceof FormData) && options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const timeoutSignal = timeoutMs ? AbortSignal.timeout(timeoutMs) : null
  const signal = options.signal && timeoutSignal
    ? AbortSignal.any([options.signal, timeoutSignal])
    : (options.signal || timeoutSignal)
  let response
  try {
    response = await fetch(`${API_URL}${path}`, { ...options, headers, signal })
  } catch (error) {
    if (error?.name === 'AbortError' || error?.name === 'TimeoutError') {
      throw new Error('Verification timed out. Please try again; the first check-in can take about a minute while models load.')
    }
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

function accessStatusLabel(status) {
  return { pending: 'Pending', available: 'Available', revoked: 'Revoked', on_hold: 'On hold' }[status] || status
}

function GuestInvitationClaim({ invitationToken, onClaim }) {
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [preview, setPreview] = useState(null)
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)

  useEffect(() => {
    api(`/guest/invitations/preview?token=${encodeURIComponent(invitationToken)}`)
      .then(setPreview)
      .catch((requestError) => setError(requestError.message))
  }, [invitationToken])

  async function claim(event) {
    event.preventDefault()
    setError('')
    if (preview?.requires_password) {
      if (password.length < 10) {
        setError('Create a password of at least 10 characters.')
        return
      }
      if (password !== confirmPassword) {
        setError('Your passwords do not match.')
        return
      }
    }
    setLoading(true)
    try {
      const body = { token: invitationToken }
      if (preview?.requires_password) body.password = password
      const response = await api('/guest/invitations/claim', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      onClaim(response)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  const accountInvite = preview?.requires_password
  const inviteRole = preview?.role === 'nominee' ? 'nominee' : 'recipient'
  return <main className="auth-page"><section className="auth-main"><div className="auth-card">
    <div className="auth-heading"><span className="eyebrow">CLOUDGUARD INVITATION</span><h2>{accountInvite ? `Create your ${inviteRole} account` : 'Verify your invited email'}</h2><p>{accountInvite ? `Verify ${preview.email_hint} and create a password to sign in. ${inviteRole === 'nominee' ? 'Your nominee role does not grant document access.' : 'This does not unlock documents.'}` : 'Continue from the invitation email to confirm you control its intended address. This link can only be used once.'}</p></div>
    {error && <div className="notice notice-error" role="alert">{error}</div>}
    <div className="notice notice-warm"><span className="notice-icon"><Icon name="lock" size={17} /></span><span><b>Invitation does not grant document access</b><small>Assigned files stay locked until the owner completes and approves release. Missed owner check-ins never release documents automatically.</small></span></div>
    <form className="form-stack" onSubmit={claim}>
      {accountInvite && <>
        <label>Password
          <span className="password-wrap">
            <input value={password} onChange={(event) => setPassword(event.target.value)} type={showPassword ? 'text' : 'password'} placeholder="At least 10 characters" autoComplete="new-password" required minLength="10" />
            <button type="button" className="password-toggle" onClick={() => setShowPassword(!showPassword)} aria-label={showPassword ? 'Hide password' : 'Show password'}><Icon name="eye" size={18} /></button>
          </span>
        </label>
        <label>Confirm password<input value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} type={showPassword ? 'text' : 'password'} placeholder="Enter your password again" autoComplete="new-password" required minLength="10" /></label>
      </>}
      <button className="button button-primary" type="submit" disabled={loading || !preview}>{loading ? 'Verifying invitation...' : accountInvite ? 'Verify email and create account' : 'Verify and continue'} <Icon name="arrow" size={17} /></button>
    </form>
  </div></section></main>
}

function GuestSession({ token, onSignOut }) {
  const [guest, setGuest] = useState(null)
  const [error, setError] = useState('')
  const [signingOut, setSigningOut] = useState(false)

  useEffect(() => {
    api('/guest/me', { token })
      .then((response) => setGuest(response.guest))
      .catch((requestError) => setError(requestError.message))
  }, [token])

  async function signOut() {
    setSigningOut(true)
    setError('')
    try {
      await onSignOut()
    } catch (requestError) {
      setError(`Signed out on this device, but the server session may remain active until it expires: ${requestError.message}`)
    } finally {
      setSigningOut(false)
    }
  }

  return <main className="auth-page"><section className="auth-main"><div className="auth-card">
    <div className="auth-heading"><span className="eyebrow">RESTRICTED GUEST SIGN-IN</span><h2>Email verified</h2><p>{guest ? `You are signed in as a ${guest.role === 'nominee' ? 'Trusted Nominee' : 'Authorized Recipient'}.` : 'Checking your invitation session...'}</p></div>
    {error && <div className="notice notice-error" role="alert">{error}</div>}
    {guest && <div className="notice notice-warm"><span className="notice-icon"><Icon name="lock" size={17} /></span><span><b>No guest actions are enabled yet</b><small>This role-specific session does not provide owner access or document listing, download, or decryption.</small></span></div>}
    <button className="button button-quiet" type="button" onClick={signOut} disabled={signingOut}>{signingOut ? 'Signing out...' : 'Sign out'}</button>
  </div></section></main>
}

function RecipientPortal({ token, onSignOut }) {
  const [recipient, setRecipient] = useState(null)
  const [page, setPage] = useState('dashboard')
  const [pageData, setPageData] = useState({})
  const [selectedDocument, setSelectedDocument] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setBusy(true)
    setError('')
    const endpoints = {
      dashboard: '/recipient/dashboard',
      documents: '/recipient/documents',
      history: '/recipient/history',
    }
    try {
      const me = await api('/recipient/me', { token })
      setRecipient(me.recipient)
      if (page === 'detail' && selectedDocument) {
        const response = await api(`/recipient/documents/${selectedDocument}`, { token })
        setPageData(response)
      } else {
        const response = await api(endpoints[page] || endpoints.dashboard, { token })
        setPageData(response)
      }
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }, [token, page, selectedDocument])

  useEffect(() => { load() }, [load])

  async function downloadDocument(item) {
    setError('')
    setMessage('')
    try {
      const response = await fetch(`${API_URL}/recipient/documents/${item.id}/download`, { headers: { Authorization: `Bearer ${token}` } })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(describeError(body.detail, response.status))
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = item.file_name || 'document'
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      setMessage('Download started.')
      await load()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  const nav = [
    { id: 'dashboard', label: 'Overview', icon: 'grid' },
    { id: 'documents', label: 'My documents', icon: 'file' },
    { id: 'history', label: 'Access history', icon: 'clock' },
  ]
  const titles = {
    dashboard: ['Recipient dashboard', 'A simple view of whether any assigned documents are available.'],
    documents: ['My documents', 'Only documents assigned to you, with their current access status.'],
    detail: ['Document details', 'Name, status, and whether this file can be opened.'],
    history: ['Access history', 'Invitation, release, and revocation updates for your account.'],
  }[page]

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand sidebar-brand" href="#" onClick={(event) => { event.preventDefault(); setPage('dashboard'); setSelectedDocument(null) }}>
          <span className="brand-mark"><Icon name="shield" size={20} /></span><span>cloudguard<span className="brand-period">.</span></span>
        </a>
        <div className="sidebar-label">RECIPIENT PORTAL</div>
        <nav className="side-nav" aria-label="Recipient navigation">
          {nav.map((item) => <button key={item.id} className={`nav-link ${page === item.id || (item.id === 'documents' && page === 'detail') ? 'active' : ''}`} onClick={() => { setPage(item.id); setSelectedDocument(null); setError(''); setMessage('') }}>
            <Icon name={item.icon} /><span>{item.label}</span>
          </button>)}
        </nav>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div><span className="eyebrow">AUTHORIZED RECIPIENT</span><h1>{titles[0]}</h1><p>{titles[1]}</p></div>
          <button className="button button-quiet" type="button" onClick={onSignOut}><Icon name="logout" size={16} /> Sign out</button>
        </header>
        {error && <div className="notice notice-error global-notice" role="alert">{error}</div>}
        {message && <div className="notice notice-success global-notice">{message}</div>}
        {busy && <div className="loading-line"><span /> Loading your recipient space...</div>}
        {page === 'dashboard' && <RecipientDashboard data={pageData} recipient={recipient} onOpenDocuments={() => setPage('documents')} />}
        {page === 'documents' && <RecipientDocuments data={pageData.documents || []} onOpen={(id) => { setSelectedDocument(id); setPage('detail') }} onDownload={downloadDocument} />}
        {page === 'detail' && pageData.document && <RecipientDocumentDetail document={pageData.document} onBack={() => { setPage('documents'); setSelectedDocument(null) }} onDownload={downloadDocument} />}
        {page === 'history' && <RecipientHistory data={pageData.events || []} />}
      </div>
    </div>
  )
}

function RecipientDashboard({ data, recipient, onOpenDocuments }) {
  const counts = data.counts || { available: 0, pending: 0, revoked: 0, assigned: 0 }
  return <div className="page-content">
    <section className="surface checkin-card">
      <div className="section-title-row"><div><span className="eyebrow">WELCOME</span><h3>{recipient ? `Hello, ${recipient.name}` : 'Recipient dashboard'}</h3></div><span className="soft-tag">AUTHORIZED RECIPIENT</span></div>
      <p>{recipient ? `You are signed in for documents from ${recipient.owner_name}.` : 'Checking your recipient account...'}</p>
      <div className="checkin-note"><Icon name="lock" size={16} /><span>{data.availability || 'Assigned documents stay locked until the owner approves release. Missed owner check-ins never release files automatically.'}</span></div>
      <button className="button button-primary" onClick={onOpenDocuments}>View my documents <Icon name="arrow" size={17} /></button>
    </section>
    <div className="stats-grid recipient-stats">
      <StatCard icon="file" label="Assigned" value={String(counts.assigned)} detail="Documents linked to you" tone="lavender" />
      <StatCard icon="lock" label="Pending" value={String(counts.pending)} detail="Waiting for release approval" tone="peach" />
      <StatCard icon="download" label="Available" value={String(counts.available)} detail="Approved for secure access" tone="mint" />
    </div>
  </div>
}

function RecipientDocuments({ data, onOpen, onDownload }) {
  return <div className="page-content">
    <section className="surface document-list-surface">
      <div className="section-title-row"><div><span className="eyebrow">ASSIGNED TO YOU</span><h3>My documents</h3></div><span className="soft-tag">{data.length} FILE{data.length === 1 ? '' : 'S'}</span></div>
      <p className="surface-description">You only see documents assigned to you. A release requires an approved case and owner authorization; Medical Hold pauses access.</p>
      {data.length ? <div className="document-table-wrap"><table className="data-table document-table"><thead><tr><th>DOCUMENT</th><th>COLLECTION</th><th>ACCESS</th><th></th></tr></thead><tbody>
        {data.map((item) => <tr key={item.id}>
          <td><span className="file-name-cell"><span className="file-type-icon"><Icon name="file" size={17} /></span><span><b>{item.name}</b><small>{item.file_name}</small></span></span></td>
          <td><span className="soft-tag tag-collection">{item.category}</span></td>
          <td><span className={`status-pill ${item.access_status === 'available' ? 'status-success' : 'status-unverified'}`}>{accessStatusLabel(item.access_status)}</span>{item.release_hold && <small className="access-hold-note">Paused by Medical Hold</small>}</td>
          <td><div className="table-actions">
            <button className="button button-quiet button-small" onClick={() => onOpen(item.id)}>Details</button>
            {item.access_status === 'available' && <button className="icon-button" onClick={() => onDownload(item)} aria-label={`Download ${item.name}`}><Icon name="download" size={17} /></button>}
          </div></td>
        </tr>)}
      </tbody></table></div> : <div className="empty-state"><span className="empty-icon"><Icon name="file" size={25} /></span><b>No documents are assigned yet</b><p>When the owner assigns a file to you, it will appear here as pending until they approve release.</p></div>}
    </section>
  </div>
}

function RecipientDocumentDetail({ document, onBack, onDownload }) {
  const available = document.access_status === 'available'
  return <div className="page-content">
    <section className="surface">
      <div className="section-title-row"><div><span className="eyebrow">DOCUMENT DETAILS</span><h3>{document.name}</h3></div><span className={`status-pill ${available ? 'status-success' : 'status-unverified'}`}>{accessStatusLabel(document.access_status)}</span></div>
      <p className="surface-description">{document.file_name} · {document.category} · {formatBytes(document.file_size)}</p>
      <div className="result-checks recipient-access-meta">
        <div><span>Access status</span><b className={available ? 'available-status' : 'unavailable-status'}>{accessStatusLabel(document.access_status)}</b></div>
        <div><span>Release approved</span><b>{document.released_at ? formatDate(document.released_at, { dateStyle: 'medium', timeStyle: 'short' }) : 'Not yet'}</b></div>
        <div><span>Revoked</span><b>{document.revoked_at ? formatDate(document.revoked_at, { dateStyle: 'medium', timeStyle: 'short' }) : 'No'}</b></div>
      </div>
      <div className="checkin-note" style={{ marginTop: 16 }}><Icon name="lock" size={16} /><span>{available ? 'This document has been approved for release. You can download it here.' : document.access_status === 'revoked' ? 'Access has been revoked. You cannot open or download this document.' : document.release_hold ? 'Access is paused by the owner’s Medical Hold. The server will continue to block downloads until the hold ends and a fresh successful owner check-in is completed.' : !document.case_approved ? 'The case has not yet been approved by an authorized reviewer. Your invitation and missed owner check-ins do not unlock files.' : 'This document is assigned to you, but the owner has not approved its release yet.'}</span></div>
      <div className="button-row" style={{ marginTop: 16 }}>
        <button className="button button-secondary" onClick={onBack}>Back to my documents</button>
        {available && <button className="button button-primary" onClick={() => onDownload(document)}>Download <Icon name="download" size={16} /></button>}
      </div>
    </section>
  </div>
}

function RecipientHistory({ data }) {
  const labels = {
    invitation_confirmed: 'Invitation confirmed',
    assignment_pending: 'Document assigned (pending)',
    release_approved: 'Release approved',
    access_revoked: 'Access revoked',
    document_downloaded: 'Document downloaded',
  }
  return <div className="page-content history-page">
    <section className="surface history-surface">
      <div className="section-title-row"><div><span className="eyebrow">UPDATES</span><h3>Access history</h3></div></div>
      {data.length ? <div className="history-list">{data.map((item) => <article className="history-item" key={item.id}><span className="timeline-dot">{item.event_type === 'release_approved' || item.event_type === 'invitation_confirmed' ? '✓' : '!'}</span><div className="history-main"><div><b>{labels[item.event_type] || item.event_type}</b></div><p>{formatDate(item.created_at, { dateStyle: 'full', timeStyle: 'short' })}</p><div className="history-checks"><span>{item.detail}</span></div></div></article>)}</div> : <div className="empty-state"><span className="empty-icon"><Icon name="clock" size={25} /></span><b>No updates yet</b><p>Invitation confirmation, release approval, and revocation notices will appear here.</p></div>}
    </section>
  </div>
}

function caseStatusLabel(status) {
  return {
    not_submitted: 'Not submitted',
    under_review: 'Under review',
    more_information_required: 'More information required',
    approved: 'Approved',
    rejected: 'Rejected',
  }[status] || status
}

function NomineePortal({ token, onSignOut }) {
  const [nominee, setNominee] = useState(null)
  const [page, setPage] = useState('dashboard')
  const [data, setData] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      const me = await api('/nominee/me', { token })
      setNominee(me.nominee)
      const endpoint = {
        dashboard: '/nominee/dashboard',
        case: '/nominee/case',
        history: '/nominee/history',
      }[page]
      setData(await api(endpoint, { token }))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }, [token, page])
  useEffect(() => { load() }, [load])

  const titles = {
    dashboard: ['Nominee dashboard', 'Your case responsibilities and latest status.'],
    case: ['Death notification', 'Submit supporting evidence for independent review.'],
    history: ['Case updates', 'Invitation, submission, and review decisions.'],
  }[page]

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand sidebar-brand" href="#" onClick={(event) => { event.preventDefault(); setPage('dashboard') }}>
        <span className="brand-mark"><Icon name="shield" size={20} /></span><span>cloudguard<span className="brand-period">.</span></span>
      </a>
      <div className="sidebar-label">NOMINEE PORTAL</div>
      <nav className="side-nav" aria-label="Nominee navigation">
        {[['dashboard', 'Overview', 'grid'], ['case', 'Death notification', 'file'], ['history', 'Case updates', 'clock']].map(([id, label, icon]) =>
          <button key={id} className={`nav-link ${page === id ? 'active' : ''}`} onClick={() => { setPage(id); setMessage(''); setError('') }}>
            <Icon name={icon} /><span>{label}</span>
          </button>
        )}
      </nav>
    </aside>
    <div className="app-main">
      <header className="topbar">
        <div><span className="eyebrow">TRUSTED NOMINEE</span><h1>{titles[0]}</h1><p>{titles[1]}</p></div>
        <button className="button button-quiet" type="button" onClick={onSignOut}><Icon name="logout" size={16} /> Sign out</button>
      </header>
      {error && <div className="notice notice-error global-notice" role="alert">{error}</div>}
      {message && <div className="notice notice-success global-notice" role="status">{message}</div>}
      {busy && <div className="loading-line"><span /> Loading your nominee space...</div>}
      {page === 'dashboard' && <div className="page-content">
        <section className="surface checkin-card">
          <div className="section-title-row"><div><span className="eyebrow">YOUR RESPONSIBILITY</span><h3>{nominee ? `Welcome, ${nominee.name}` : 'Nominee dashboard'}</h3></div><span className="soft-tag">NOMINEE ONLY</span></div>
          <p>{nominee ? `You are the trusted nominee for ${nominee.owner_name}.` : 'Checking your nominee account...'}</p>
          <div className="checkin-note"><Icon name="lock" size={16} /><span>{data.notice || 'You may submit a death notification and supporting certificate for an authorized reviewer. You cannot approve your own case or access documents.'}</span></div>
          <div className="nominee-case-status"><span>Case status</span><b className={`status-pill ${data.case_status === 'approved' ? 'status-success' : 'status-unverified'}`}>{caseStatusLabel(data.case_status || 'not_submitted')}</b></div>
          <button className="button button-primary" onClick={() => setPage('case')}>{data.case_status === 'not_submitted' ? 'Submit a death notification' : 'View case status'} <Icon name="arrow" size={17} /></button>
        </section>
        <section className="surface nominee-boundary"><Icon name="shield" size={17} /><span><b>Your submission does not unlock documents</b><small>A reviewer independently checks evidence. Even an approved case requires separate owner-controlled document release. Missed check-ins alone never release files.</small></span></section>
      </div>}
      {page === 'case' && <NomineeCasePage data={data.case || null} token={token} onSubmitted={async (result) => { setMessage(result.message); await load() }} />}
      {page === 'history' && <NomineeCaseHistory data={data.events || []} />}
    </div>
  </div>
}

function NomineeCasePage({ data, token, onSubmitted }) {
  const [note, setNote] = useState('')
  const [file, setFile] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const formRef = useRef(null)

  async function submit(event) {
    event.preventDefault()
    setError('')
    if (!file) {
      setError('Choose the death certificate file to submit.')
      return
    }
    const body = new FormData()
    body.set('note', note)
    body.set('evidence', file, file.name)
    setLoading(true)
    try {
      const response = await api('/nominee/case', { token, method: 'POST', body })
      setFile(null)
      setNote('')
      formRef.current?.reset()
      await onSubmitted(response)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  const canSubmit = !data || data.status === 'more_information_required'
  return <div className="page-content">
    <section className="surface nominee-case-surface">
      <div className="section-title-row"><div><span className="eyebrow">CASE STATUS</span><h3>{caseStatusLabel(data?.status || 'not_submitted')}</h3></div><span className={`status-pill ${data?.status === 'approved' ? 'status-success' : 'status-unverified'}`}>{caseStatusLabel(data?.status || 'not_submitted')}</span></div>
      {data && <div className="nominee-case-details">
        <p>Submitted {formatDate(data.submitted_at, { dateStyle: 'medium', timeStyle: 'short' })} · Evidence: {data.evidence_name} ({formatBytes(data.evidence_size)})</p>
        {data.reviewer_message && <div className={`notice ${data.status === 'rejected' ? 'notice-error' : 'notice-warm'}`}><span><b>Reviewer update</b><small>{data.reviewer_message}</small></span></div>}
      </div>}
      {data?.status === 'under_review' && <div className="notice notice-warm"><Icon name="clock" size={17} /><span><b>Evidence is under review</b><small>Wait for an authorized reviewer’s decision. Do not send duplicate submissions.</small></span></div>}
      {data?.status === 'approved' && <div className="notice notice-success"><Icon name="shield" size={17} /><span><b>Case approved</b><small>The reviewer’s decision does not grant you document access. Any document release remains a separate owner-controlled action.</small></span></div>}
      {data?.status === 'rejected' && <div className="notice notice-error" role="status"><span><b>Case rejected</b><small>Follow the reviewer’s instructions or contact the account owner. You cannot approve or reopen this case.</small></span></div>}
      {canSubmit && <form ref={formRef} className="form-stack nominee-evidence-form" onSubmit={submit}>
        {data?.status === 'more_information_required' && <div className="notice notice-warm"><span><b>More information is required</b><small>Submit the requested supporting certificate again. The case returns to review; it is not approved automatically.</small></span></div>}
        <label>Death certificate (PDF, JPG, or PNG; 10 MB maximum)
          <input type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" onChange={(event) => setFile(event.target.files?.[0] || null)} required />
        </label>
        <label>Optional note
          <textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength="2000" rows="4" placeholder="Add information for the reviewer." />
        </label>
        {error && <div className="notice notice-error" role="alert">{error}</div>}
        <button className="button button-primary" type="submit" disabled={loading}>{loading ? 'Submitting securely...' : data ? 'Resubmit evidence for review' : 'Submit case for review'} <Icon name="lock" size={16} /></button>
      </form>}
    </section>
    <section className="surface nominee-boundary"><Icon name="lock" size={17} /><span><b>Evidence is encrypted and access-restricted</b><small>Only authorized reviewers can inspect the uploaded evidence. Submission or approval never directly unlocks documents.</small></span></section>
  </div>
}

function NomineeCaseHistory({ data }) {
  const labels = {
    invitation_confirmed: 'Invitation confirmed',
    evidence_submitted: 'Case submitted',
    evidence_resubmitted: 'Updated evidence submitted',
    case_under_review: 'Case under review',
    case_more_information_required: 'More information required',
    case_approved: 'Case approved',
    case_rejected: 'Case rejected',
  }
  return <div className="page-content history-page"><section className="surface history-surface">
    <div className="section-title-row"><div><span className="eyebrow">CASE TIMELINE</span><h3>Updates and instructions</h3></div></div>
    {data.length ? <div className="history-list">{data.map((item) => <article className="history-item" key={item.id}><span className="timeline-dot">•</span><div className="history-main"><b>{labels[item.event_type] || item.event_type}</b><p>{formatDate(item.created_at, { dateStyle: 'full', timeStyle: 'short' })}</p><div className="history-checks"><span>{item.detail}</span></div></div></article>)}</div> : <div className="empty-state"><span className="empty-icon"><Icon name="clock" size={25} /></span><b>No case updates yet</b><p>Invitation confirmation, evidence submission, and reviewer decisions will appear here.</p></div>}
  </section></div>
}

function ReviewerPortal({ token, onSignOut }) {
  const [cases, setCases] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setError('')
    setBusy(true)
    try {
      const response = await api('/reviewer/cases', { token })
      setCases(response.cases)
      if (!selectedId && response.cases.length) setSelectedId(response.cases[0].id)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }, [token, selectedId])
  useEffect(() => { load() }, [load])

  const selected = cases.find((item) => item.id === selectedId)
  async function openEvidence() {
    if (!selected) return
    setError('')
    try {
      const response = await fetch(`${API_URL}/reviewer/cases/${selected.id}/evidence`, { headers: { Authorization: `Bearer ${token}` } })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(describeError(body.detail, response.status))
      }
      const url = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = selected.evidence_name
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  async function submitDecision(nextDecision) {
    setError('')
    if (!selected || !nextDecision || message.trim().length < 5) {
      setError('Select a decision and provide at least five characters of guidance.')
      return
    }
    setBusy(true)
    try {
      await api(`/reviewer/cases/${selected.id}/decision`, {
        token,
        method: 'POST',
        body: JSON.stringify({ decision: nextDecision, message: message.trim() }),
      })
      setMessage('')
      setDecision('')
      await load()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand sidebar-brand" href="#" onClick={(event) => event.preventDefault()}>
        <span className="brand-mark"><Icon name="shield" size={20} /></span><span>cloudguard<span className="brand-period">.</span></span>
      </a>
      <div className="sidebar-label">REVIEWER PORTAL</div>
      <nav className="side-nav" aria-label="Reviewer navigation"><button className="nav-link active"><Icon name="file" /><span>Death cases</span></button></nav>
    </aside>
    <div className="app-main">
      <header className="topbar"><div><span className="eyebrow">AUTHORIZED REVIEWER</span><h1>Death case review</h1><p>Review evidence and record an independent case decision.</p></div><button className="button button-quiet" type="button" onClick={onSignOut}><Icon name="logout" size={16} /> Sign out</button></header>
      {error && <div className="notice notice-error global-notice" role="alert">{error}</div>}
      {busy && <div className="loading-line"><span /> Loading case records...</div>}
      <div className="page-content reviewer-workspace">
        <section className="surface reviewer-case-list">
          <div className="section-title-row"><div><span className="eyebrow">SUBMISSIONS</span><h3>Cases for review</h3></div><span className="soft-tag">{cases.filter((item) => item.status === 'under_review').length} PENDING</span></div>
          {cases.length ? cases.map((item) => <button className={`reviewer-case-item ${selectedId === item.id ? 'active' : ''}`} key={item.id} onClick={() => { setSelectedId(item.id); setMessage('') }}>
            <b>{item.nominee_name}</b><small>Owner: {item.owner_name}</small><span className="status-pill status-unverified">{caseStatusLabel(item.status)}</span>
          </button>) : <div className="empty-state"><span className="empty-icon"><Icon name="file" size={24} /></span><b>No cases submitted</b><p>Submitted nominee evidence appears here for review.</p></div>}
        </section>
        <section className="surface reviewer-case-detail">
          {selected ? <>
            <div className="section-title-row"><div><span className="eyebrow">CASE #{selected.id}</span><h3>{caseStatusLabel(selected.status)}</h3></div><span className="soft-tag">INDEPENDENT REVIEW</span></div>
            <dl className="reviewer-case-meta"><div><dt>Nominee</dt><dd>{selected.nominee_name} · {selected.nominee_email}</dd></div><div><dt>Owner</dt><dd>{selected.owner_name} · {selected.owner_email}</dd></div><div><dt>Submitted</dt><dd>{formatDate(selected.submitted_at, { dateStyle: 'medium', timeStyle: 'short' })}</dd></div><div><dt>Evidence</dt><dd>{selected.evidence_name} · {formatBytes(selected.evidence_size)}</dd></div></dl>
            {selected.note && <div className="reviewer-note"><b>Nominee note</b><p>{selected.note}</p></div>}
            <button className="button button-secondary" onClick={openEvidence}><Icon name="download" size={16} /> Open supporting certificate</button>
            {selected.reviewer_message && <div className="notice notice-warm"><span><b>Previous decision note</b><small>{selected.reviewer_message}</small></span></div>}
            {selected.status === 'under_review' && <form className="form-stack reviewer-decision-form" onSubmit={(event) => event.preventDefault()}>
              <label>Reviewer instructions<textarea value={message} onChange={(event) => setMessage(event.target.value)} minLength="5" maxLength="2000" rows="4" required placeholder="Explain the decision or specify what is missing." /></label>
              <div className="button-row">
                <button className="button button-secondary" type="button" onClick={() => submitDecision('more_information_required')} disabled={busy}>Request more information</button>
                <button className="button button-primary" type="button" onClick={() => submitDecision('approved')} disabled={busy}>Approve case</button>
                <button className="button button-quiet danger-icon" type="button" onClick={() => submitDecision('rejected')} disabled={busy}>Reject case</button>
              </div>
              <small className="field-hint">Approval records the case decision only. It does not release or expose documents.</small>
            </form>}
          </> : <div className="empty-state"><span className="empty-icon"><Icon name="file" size={24} /></span><b>Select a case</b><p>Choose a submission to inspect its evidence and status.</p></div>}
        </section>
      </div>
    </div>
  </div>
}

function AuthScreen({ onLogin, onRecipientLogin, onNomineeLogin, onReviewerLogin, systemNotice }) {
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
      } else if (mode === 'recipient-login') {
        const response = await api('/recipient/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        })
        onRecipientLogin(response)
      } else if (mode === 'nominee-login') {
        const response = await api('/nominee/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        })
        onNomineeLogin(response)
      } else if (mode === 'reviewer-login') {
        const response = await api('/reviewer/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        })
        onReviewerLogin(response)
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
            <span className="eyebrow">{mode === 'recipient-login' ? 'AUTHORIZED RECIPIENT' : mode === 'nominee-login' ? 'TRUSTED NOMINEE' : mode === 'reviewer-login' ? 'AUTHORIZED REVIEWER' : mode === 'login' ? 'WELCOME BACK' : 'START YOUR VAULT'}</span>
            <h2>{mode === 'recipient-login' ? 'Recipient sign-in' : mode === 'nominee-login' ? 'Nominee sign-in' : mode === 'reviewer-login' ? 'Reviewer sign-in' : mode === 'login' ? 'Sign in to your vault' : 'Create your account'}</h2>
            <p>{mode.endsWith('-login') ? 'Use the password you created from your invitation or reviewer account.' : mode === 'login' ? 'Your important things are right where you left them.' : 'A few details to set up your private space.'}</p>
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
            {(mode === 'login' || mode === 'recipient-login') && <label>Email address<input name="email" type="email" placeholder="you@example.com" autoComplete="email" required /></label>}
            <label>Password
              <span className="password-wrap">
                <input name="password" type={showPassword ? 'text' : 'password'} placeholder={mode === 'register' ? 'At least 10 characters' : 'Enter your password'} autoComplete={mode === 'register' ? 'new-password' : 'current-password'} required minLength={mode === 'register' ? 10 : 1} />
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
              <p className="field-hint">Your live preview stays on this device. The five captured images are encrypted when saved and used to confirm it is you at live check-in.</p>
            </>}
            {mode === 'login' && <div className="form-meta"><span>Secure access to your personal vault</span><span className="secure-label"><Icon name="lock" size={13} /> ENCRYPTED</span></div>}
            {mode.endsWith('-login') && <div className="form-meta"><span>{mode === 'nominee-login' ? 'Case submission only; no document access' : mode === 'reviewer-login' ? 'Case review only; no document access' : 'Documents stay locked until approved release'}</span><span className="secure-label"><Icon name="lock" size={13} /> RESTRICTED</span></div>}
            <button className="button button-primary button-full" type="submit" disabled={loading}>{loading ? 'Please wait...' : mode === 'register' ? 'Create secure account' : 'Sign in'}<Icon name="arrow" size={17} /></button>
          </form>
          <p className="auth-switch">{mode === 'login' ? 'New to CloudGuard?' : mode === 'register' ? 'Already have a CloudGuard account?' : 'Use a different sign-in role?'} <button type="button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(''); setNotice('') }}>{mode === 'login' ? 'Create an account' : 'Owner sign in'}</button></p>
          {mode !== 'register' && <div className="auth-role-links">
            {mode !== 'recipient-login' && <p className="auth-switch">Authorized recipient? <button type="button" onClick={() => { setMode('recipient-login'); setError(''); setNotice('') }}>Recipient sign-in</button></p>}
            {mode !== 'nominee-login' && <p className="auth-switch">Trusted nominee? <button type="button" onClick={() => { setMode('nominee-login'); setError(''); setNotice('') }}>Nominee sign-in</button></p>}
            {mode !== 'reviewer-login' && <p className="auth-switch">Reviewer? <button type="button" onClick={() => { setMode('reviewer-login'); setError(''); setNotice('') }}>Reviewer sign-in</button></p>}
          </div>}
          <div className="auth-footnote"><Icon name="lock" size={13} /> Your files stay yours. Always.</div>
        </div>
        <div className="auth-bottom">CLOUDGUARD <span>•</span> A PRIVATE PLACE FOR WHAT MATTERS</div>
      </section>
    </main>
  )
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem('cloudguard_token') || '')
  const [guestToken, setGuestToken] = useState(() => sessionStorage.getItem('cloudguard_guest_token') || '')
  const [recipientToken, setRecipientToken] = useState(() => sessionStorage.getItem('cloudguard_recipient_token') || '')
  const [nomineeToken, setNomineeToken] = useState(() => sessionStorage.getItem('cloudguard_nominee_token') || '')
  const [reviewerToken, setReviewerToken] = useState(() => sessionStorage.getItem('cloudguard_reviewer_token') || '')
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
  const invitationToken = new URLSearchParams(window.location.hash.slice(1)).get('guest-invitation')

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
    if (!token || guestToken || recipientToken || nomineeToken || reviewerToken || invitationToken) return
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
  }, [token, page, logout, guestToken, recipientToken, nomineeToken, reviewerToken, invitationToken])

  useEffect(() => {
    if (!guestToken && !recipientToken && !nomineeToken && !reviewerToken && !invitationToken) return
    localStorage.removeItem('cloudguard_token')
    localStorage.removeItem('cloudguard_user')
    setToken('')
    setUser(null)
  }, [guestToken, recipientToken, nomineeToken, reviewerToken, invitationToken])

  useEffect(() => { load() }, [load])

  function handleLogin(response) {
    sessionStorage.removeItem('cloudguard_guest_token')
    sessionStorage.removeItem('cloudguard_recipient_token')
    sessionStorage.removeItem('cloudguard_nominee_token')
    sessionStorage.removeItem('cloudguard_reviewer_token')
    setGuestToken('')
    setRecipientToken('')
    setNomineeToken('')
    setReviewerToken('')
    localStorage.setItem('cloudguard_token', response.access_token)
    localStorage.setItem('cloudguard_user', JSON.stringify(response.user))
    setToken(response.access_token)
    setUser(response.user)
    setPage('dashboard')
    setAuthNotice('')
  }

  function handleRecipientLogin(response) {
    sessionStorage.removeItem('cloudguard_guest_token')
    sessionStorage.removeItem('cloudguard_nominee_token')
    sessionStorage.removeItem('cloudguard_reviewer_token')
    setGuestToken('')
    setNomineeToken('')
    setReviewerToken('')
    localStorage.removeItem('cloudguard_token')
    localStorage.removeItem('cloudguard_user')
    sessionStorage.setItem('cloudguard_recipient_token', response.access_token)
    setRecipientToken(response.access_token)
    setAuthNotice('')
  }

  function handleNomineeLogin(response) {
    sessionStorage.removeItem('cloudguard_guest_token')
    sessionStorage.removeItem('cloudguard_recipient_token')
    sessionStorage.removeItem('cloudguard_reviewer_token')
    setGuestToken('')
    setRecipientToken('')
    setReviewerToken('')
    localStorage.removeItem('cloudguard_token')
    localStorage.removeItem('cloudguard_user')
    sessionStorage.setItem('cloudguard_nominee_token', response.access_token)
    setNomineeToken(response.access_token)
    setAuthNotice('')
  }

  function handleReviewerLogin(response) {
    sessionStorage.removeItem('cloudguard_guest_token')
    sessionStorage.removeItem('cloudguard_recipient_token')
    sessionStorage.removeItem('cloudguard_nominee_token')
    setGuestToken('')
    setRecipientToken('')
    setNomineeToken('')
    localStorage.removeItem('cloudguard_token')
    localStorage.removeItem('cloudguard_user')
    sessionStorage.setItem('cloudguard_reviewer_token', response.access_token)
    setReviewerToken(response.access_token)
    setAuthNotice('')
  }

  async function recipientSignOut() {
    let revokeFailed = false
    try {
      await api('/recipient/logout', { token: recipientToken, method: 'POST' })
    } catch {
      revokeFailed = true
    }
    sessionStorage.removeItem('cloudguard_recipient_token')
    setRecipientToken('')
    setAuthNotice(revokeFailed
      ? 'Signed out on this device. Server session revocation could not be confirmed.'
      : 'You have been signed out.')
  }

  async function nomineeSignOut() {
    let revokeFailed = false
    try {
      await api('/nominee/logout', { token: nomineeToken, method: 'POST' })
    } catch {
      revokeFailed = true
    }
    sessionStorage.removeItem('cloudguard_nominee_token')
    setNomineeToken('')
    setAuthNotice(revokeFailed
      ? 'Signed out on this device. Server session revocation could not be confirmed.'
      : 'You have been signed out.')
  }

  async function reviewerSignOut() {
    let revokeFailed = false
    try {
      await api('/reviewer/logout', { token: reviewerToken, method: 'POST' })
    } catch {
      revokeFailed = true
    }
    sessionStorage.removeItem('cloudguard_reviewer_token')
    setReviewerToken('')
    setAuthNotice(revokeFailed
      ? 'Signed out on this device. Server session revocation could not be confirmed.'
      : 'You have been signed out.')
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

  function handleInvitationClaim(response) {
    sessionStorage.removeItem('cloudguard_guest_token')
    sessionStorage.removeItem('cloudguard_recipient_token')
    sessionStorage.removeItem('cloudguard_nominee_token')
    sessionStorage.removeItem('cloudguard_reviewer_token')
    setGuestToken('')
    setRecipientToken('')
    setNomineeToken('')
    setReviewerToken('')
    localStorage.removeItem('cloudguard_token')
    localStorage.removeItem('cloudguard_user')
    if (response.principal_type === 'recipient') {
      sessionStorage.setItem('cloudguard_recipient_token', response.access_token)
      setRecipientToken(response.access_token)
    } else if (response.principal_type === 'nominee') {
      sessionStorage.setItem('cloudguard_nominee_token', response.access_token)
      setNomineeToken(response.access_token)
    } else {
      sessionStorage.setItem('cloudguard_guest_token', response.access_token)
      setGuestToken(response.access_token)
    }
    window.history.replaceState(null, '', window.location.pathname)
  }

  async function guestSignOut() {
    try {
      await api('/guest/logout', { token: guestToken, method: 'POST' })
    } finally {
      sessionStorage.removeItem('cloudguard_guest_token')
      setGuestToken('')
    }
  }

  if (invitationToken) {
    return <GuestInvitationClaim invitationToken={invitationToken} onClaim={handleInvitationClaim} />
  }
  if (guestToken) return <GuestSession token={guestToken} onSignOut={guestSignOut} />
  if (recipientToken) return <RecipientPortal token={recipientToken} onSignOut={recipientSignOut} />
  if (nomineeToken) return <NomineePortal token={nomineeToken} onSignOut={nomineeSignOut} />
  if (reviewerToken) return <ReviewerPortal token={reviewerToken} onSignOut={reviewerSignOut} />
  if (!token || !user) return <AuthScreen onLogin={handleLogin} onRecipientLogin={handleRecipientLogin} onNomineeLogin={handleNomineeLogin} onReviewerLogin={handleReviewerLogin} systemNotice={authNotice} />

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

          {page === 'dashboard' && <Dashboard data={pageData} onNavigate={navigate} token={token} onRefresh={load} />}
          {page === 'documents' && <Documents data={pageData.documents || []} token={token} onChange={load} />}
          {page === 'recipients' && <Recipients data={pageData.recipients || []} token={token} onChange={load} />}
          {page === 'nominee' && <Nominee data={pageData.nominee || null} token={token} onChange={load} />}
          {page === 'checkin' && <Checkin token={token} onComplete={async (checkin) => { await load(); notify(checkin.status === 'success' ? 'Check-in passed.' : checkin.face_status !== 'passed' && (checkin.face_detail === 'different_person' || checkin.face_detail === 'mismatch') ? 'Check-in failed: the live face does not match enrollment.' : 'Check-in recorded. Verification did not pass.') }} />}
          {page === 'history' && <History data={pageData.checkins || []} />}
          {page === 'profile' && <Profile data={user} token={token} onSave={(updated) => { setUser(updated); localStorage.setItem('cloudguard_user', JSON.stringify(updated)); notify('Your profile has been updated.') }} />}
        </div>
        <footer className="app-footer"><span>© {new Date().getFullYear()} CloudGuard</span><span><Icon name="lock" size={13} /> PRIVATE BY DESIGN</span></footer>
      </main>
      {toast && <div className="toast"><span className="toast-check">✓</span>{toast}</div>}
    </div>
  )
}

function Dashboard({ data, onNavigate, token, onRefresh }) {
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

    <MedicalHoldPanel hold={data.medical_hold} nomineeCase={data.nominee_case} token={token} onRefresh={onRefresh} onNavigate={onNavigate} />

    <div className="dashboard-lower">
      <section className="surface checkin-card">
        <div className="section-title-row"><div><span className="eyebrow">STAY IN THE LOOP</span><h3>Your regular check-in</h3></div><span className="soft-tag tag-unverified">VERIFICATION REQUIRED</span></div>
        <p>Take a moment to confirm you’re here. The check-in experience uses your camera and microphone to record a short sample.</p>
        <div className="checkin-note"><Icon name="shield" size={16} /><span>Your check-in is marked passed only when face match, anti-spoofing, and speech checks all succeed.</span></div>
        <button className="button button-primary" onClick={() => onNavigate('checkin')}>Start a check-in <Icon name="arrow" size={17} /></button>
      </section>
      <section className="surface trusted-card">
        <div className="section-title-row"><div><span className="eyebrow">YOUR CIRCLE</span><h3>Trusted people</h3></div><button className="icon-button" onClick={() => onNavigate('recipients')} aria-label="View trusted people"><Icon name="arrow" size={18} /></button></div>
        <p>Recipients can see only files assigned to them after independent case approval and your explicit release approval.</p>
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

function MedicalHoldPanel({ hold, nomineeCase, token, onRefresh }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const status = hold?.status || 'inactive'
  const caseState = nomineeCase?.status || 'not_submitted'

  async function update(action) {
    setError('')
    setBusy(true)
    try {
      await api('/medical-hold', { token, method: 'PUT', body: JSON.stringify({ action }) })
      await onRefresh()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  return <section className={`surface medical-hold-panel ${status !== 'inactive' ? 'medical-hold-active' : ''}`}>
    <div className="section-title-row"><div><span className="eyebrow">RELEASE SAFETY</span><h3>Medical Hold</h3></div><span className={`status-pill ${status === 'inactive' ? 'status-success' : 'status-unverified'}`}>{status === 'active' ? 'Active' : status === 'awaiting_checkin' ? 'Fresh check-in required' : 'Inactive'}</span></div>
    <p>{status === 'active'
      ? 'A Medical Hold is active. Recipient document downloads and new releases are blocked.'
      : status === 'awaiting_checkin'
        ? 'The hold has ended, but monitoring and release remain paused until you complete a fresh successful live check-in.'
        : 'You control this hold. Ending it does not resume monitoring until a fresh successful live check-in.'}</p>
    <p className="medical-case-status">Nominee case: <b>{caseStatusLabel(caseState)}</b>{nomineeCase?.reviewer_message ? ` — ${nomineeCase.reviewer_message}` : ''}</p>
    {error && <div className="notice notice-error" role="alert">{error}</div>}
    <div className="button-row">
      {status === 'active'
        ? <button className="button button-secondary" type="button" onClick={() => update('end')} disabled={busy}>{busy ? 'Updating...' : 'End Medical Hold'}</button>
        : status === 'inactive'
          ? <button className="button button-secondary" type="button" onClick={() => update('start')} disabled={busy}>{busy ? 'Updating...' : 'Start Medical Hold'}</button>
          : <button className="button button-quiet" type="button" onClick={() => onNavigate('checkin')}>Complete fresh live check-in <Icon name="arrow" size={15} /></button>}
      <button className="text-link" type="button" onClick={() => onNavigate('nominee')}>View nominee settings <Icon name="arrow" size={15} /></button>
    </div>
  </section>
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
  const [messageKind, setMessageKind] = useState('success')
  const inputRef = useRef(null)
  const visible = data.filter((item) => (filter === 'All files' || item.category === filter) && `${item.name} ${item.file_name}`.toLowerCase().includes(search.toLowerCase()))

  const [recipients, setRecipients] = useState([])
  const [selectedRecipients, setSelectedRecipients] = useState([])
  const [editingSharesFor, setEditingSharesFor] = useState(null)
  const [editShares, setEditShares] = useState([])

  function showMessage(text, kind = 'success') {
    setMessage(text)
    setMessageKind(kind)
  }

  useEffect(() => {
    api('/recipients', { token })
      .then(res => setRecipients(res.recipients || []))
      .catch((error) => {
        setMessage(`Could not load recipients: ${error.message}`)
        setMessageKind('error')
      })
  }, [token])

  function selectFile(file) {
    if (!file) return
    setSelectedFile(file)
    setDocumentName((currentName) => currentName.trim() ? currentName : file.name.replace(/\.[^.]+$/, ''))
    showMessage('')
  }

  function toggleRecipient(id, isEdit = false) {
    if (isEdit) {
      setEditShares(prev => prev.includes(id) ? prev.filter(r => r !== id) : [...prev, id])
    } else {
      setSelectedRecipients(prev => prev.includes(id) ? prev.filter(r => r !== id) : [...prev, id])
    }
  }

  async function submitUpload(event) {
    event.preventDefault()
    showMessage('')
    if (!selectedFile) { showMessage('Choose a file to upload.', 'error'); return }
    if (!documentName.trim()) { showMessage('Enter a display name for this document.', 'error'); return }
    const formData = new FormData()
    formData.set('name', documentName.trim())
    formData.set('category', category)
    formData.set('file', selectedFile, selectedFile.name)
    formData.set('recipient_ids_json', JSON.stringify(selectedRecipients))
    setLoading(true)
    try {
      const response = await api('/documents', { token, method: 'POST', body: formData })
      showMessage(`${response.document.name} has been encrypted and saved to your vault.`)
      setSelectedFile(null)
      setDocumentName('')
      setCategory('Personal')
      setSelectedRecipients([])
      if (inputRef.current) inputRef.current.value = ''
      setExpanded(false)
      await onChange()
    } catch (error) {
      showMessage(error.message, 'error')
    } finally { setLoading(false) }
  }

  async function saveShares(docId) {
    try {
      setLoading(true)
      await api(`/documents/${docId}/shares`, {
        token,
        method: 'PUT',
        body: JSON.stringify({ recipient_ids: editShares })
      })
      showMessage('Document assignments updated.')
      setEditingSharesFor(null)
      await onChange()
    } catch (error) {
      showMessage(error.message, 'error')
    } finally { setLoading(false) }
  }

  async function changeRelease(item, recipientId, currentlyAvailable) {
    setLoading(true)
    showMessage('')
    try {
      if (currentlyAvailable) {
        await api(`/documents/${item.id}/releases/${recipientId}`, { token, method: 'DELETE' })
        showMessage('Recipient access has been revoked.')
      } else {
        await api(`/documents/${item.id}/releases`, {
          token,
          method: 'POST',
          body: JSON.stringify({ recipient_id: recipientId }),
        })
        showMessage('Release approved for this assigned recipient.')
      }
      await onChange()
    } catch (error) {
      showMessage(error.message, 'error')
    } finally {
      setLoading(false)
    }
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
      showMessage('Encrypted download started.')
    } catch (error) { showMessage(error.message, 'error') }
  }

  async function remove(item) {
    if (!window.confirm(`Delete “${item.name}” from your private vault? This cannot be undone.`)) return
    try {
      await api(`/documents/${item.id}`, { token, method: 'DELETE' })
      showMessage('Document deleted from your vault.')
      await onChange()
    } catch (error) { showMessage(error.message, 'error') }
  }

  return <div className="page-content">
    <div className="notice notice-warm"><span className="notice-icon"><Icon name="lock" size={17} /></span><span><b>Private, encrypted storage</b><small>Files are encrypted before being saved. Recipients cannot download assigned files until a nominee case is approved by a reviewer, you approve release, and no Medical Hold is active.</small></span></div>
    <section className="surface upload-surface">
      <div className="section-title-row"><div><span className="eyebrow">ADD TO YOUR VAULT</span><h3>Upload a document</h3></div><span className="soft-tag">{data.length} {data.length === 1 ? 'FILE' : 'FILES'}</span></div>
      {!expanded ? <div className="upload-prompt"><div className="upload-icon"><Icon name="plus" size={23} /></div><div><b>Keep an important file close</b><p>Upload a document, photo, audio clip or archive. Up to 25 MB per file.</p></div><button className="button button-primary" onClick={() => setExpanded(true)}>Choose a file <Icon name="arrow" size={16} /></button></div> :
      <form className="upload-form" onSubmit={submitUpload}>
        <div className="upload-drop" role="button" tabIndex={0} onClick={() => inputRef.current?.click()} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); inputRef.current?.click() } }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); selectFile(event.dataTransfer.files[0]) }}>
          <span className="upload-icon"><Icon name="file" size={23} /></span><b>{selectedFile ? selectedFile.name : 'Drop a file here or browse'}</b><small>{selectedFile ? formatBytes(selectedFile.size) : 'PDF, TXT, Office files, images, audio, video & archives – 25 MB max'}</small>
          <input ref={inputRef} type="file" hidden accept=".pdf,.txt,.rtf,.doc,.docx,.odt,.xls,.xlsx,.ods,.csv,.ppt,.pptx,.jpg,.jpeg,.png,.gif,.webp,.heic,.tif,.tiff,.bmp,.mp3,.wav,.m4a,.mp4,.mov,.avi,.zip,.7z,.rar,.json,.xml" onClick={(event) => event.stopPropagation()} onChange={(event) => selectFile(event.target.files[0])} />
        </div>
        <div className="input-row upload-fields"><label>Display name<input name="name" required minLength="2" maxLength="120" placeholder="e.g. Home insurance policy" value={documentName} onChange={(event) => setDocumentName(event.target.value)} /></label><label>Collection<select name="category" value={category} onChange={(event) => setCategory(event.target.value)}>{DOC_TYPES.map((type) => <option key={type}>{type}</option>)}</select></label></div>

        {recipients.length > 0 && <div className="upload-assignments">
            <span style={{display: 'block', fontSize: '11px', fontWeight: 'bold', color: '#7f8799', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '1px'}}>Assign to Authorized Recipients</span>
            <div style={{display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px', background: '#f8f8fc', borderRadius: '10px', border: '1px solid #eef0f5'}}>
                {recipients.map(r => (
                    <label key={r.id} style={{display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '13px', margin: 0}}>
                        <input type="checkbox" checked={selectedRecipients.includes(r.id)} onChange={() => toggleRecipient(r.id)} style={{margin: 0, width: '16px', height: '16px', accentColor: 'var(--purple)'}} />
                        <div><b>{r.name}</b> <span style={{color: 'var(--muted)', fontSize: '12px'}}>({r.relationship})</span></div>
                    </label>
                ))}
            </div>
            <small style={{display: 'block', marginTop: '6px', fontSize: '11px', color: 'var(--soft-muted)'}}>Assignment alone never grants access. Reviewer and owner approval are still required.</small>
        </div>}

        {message && <div className={`notice notice-${messageKind}`} role={messageKind === 'error' ? 'alert' : 'status'}>{message}</div>}
        <div className="button-row"><button className="button button-primary" type="submit" disabled={loading}>{loading ? 'Encrypting...' : 'Encrypt and save'} <Icon name="lock" size={16} /></button><button className="button button-quiet" type="button" onClick={() => { setExpanded(false); setSelectedFile(null); setDocumentName(''); setCategory('Personal'); setMessage(''); setSelectedRecipients([]); if (inputRef.current) inputRef.current.value = '' }}>Cancel</button></div>
      </form>}
    </section>
    <section className="surface document-list-surface">
      <div className="section-title-row document-list-heading"><div><span className="eyebrow">YOUR COLLECTION</span><h3>Saved documents <span className="count-badge">{data.length}</span></h3></div><div className="list-controls"><div className="search-field"><Icon name="search" size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a file" aria-label="Search documents" /></div><select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter documents"><option>All files</option>{DOC_TYPES.map((type) => <option key={type}>{type}</option>)}</select></div></div>
      {message && !expanded && <div className={`notice notice-${messageKind} list-notice`} role={messageKind === 'error' ? 'alert' : 'status'}>{message}</div>}
      {visible.length ? <div className="document-table-wrap"><table className="data-table document-table"><thead><tr><th>DOCUMENT</th><th>COLLECTION</th><th>ASSIGNMENTS</th><th>FILE SIZE</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>

      {visible.map((item) => <Fragment key={item.id}>
      <tr>
        <td><span className="file-name-cell"><span className={`file-type-icon ${item.file_name.split('.').pop().toLowerCase() === 'pdf' ? 'pdf-icon' : ''}`}><Icon name="file" size={17} /></span><span><b>{item.name}</b><small>{item.file_name}</small></span></span></td>
        <td><span className="soft-tag tag-collection">{item.category}</span></td>
        <td>
          <div style={{display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap'}}>
            {item.recipient_ids && item.recipient_ids.length > 0 ? (
               item.recipient_ids.map(rid => {
                   const r = recipients.find(x => x.id === rid)
                   const assignment = item.assignments?.find((entry) => entry.recipient_id === rid)
                   const accessStatus = assignment?.access_status || 'pending'
                   return <div className="document-assignment" key={rid}>
                     <span><b>{r ? r.name : 'Unknown recipient'}</b><small>{accessStatusLabel(accessStatus)}</small></span>
                     <button className="text-button" type="button" onClick={() => changeRelease(item, rid, accessStatus === 'available')} disabled={loading || accessStatus === 'on_hold'} title={accessStatus === 'available' ? 'Revoke recipient access' : 'Approve release (requires approved case and no Medical Hold)'}>
                       {accessStatus === 'available' ? 'Revoke' : accessStatus === 'on_hold' ? 'On hold' : 'Approve release'}
                     </button>
                   </div>
               })
            ) : <span style={{color: 'var(--soft-muted)', fontSize: '11px'}}>Unassigned</span>}
          </div>
        </td>
        <td>{formatBytes(item.file_size)}</td>
        <td><div className="table-actions">
          <button className="icon-button" onClick={() => { setEditingSharesFor(editingSharesFor === item.id ? null : item.id); setEditShares(item.recipient_ids || []); showMessage('') }} aria-label={`Assign ${item.name}`} title="Assign Recipients"><Icon name="users" size={17} /></button>
          <button className="icon-button" onClick={() => download(item)} aria-label={`Download encrypted ${item.name}`} title="Download encrypted"><Icon name="download" size={17} /></button>
          <button className="icon-button danger-icon" onClick={() => remove(item)} aria-label={`Delete ${item.name}`} title="Delete"><Icon name="trash" size={17} /></button>
        </div></td>
      </tr>
      {editingSharesFor === item.id && <tr>
        <td colSpan="5" style={{background: '#f8f8fc', padding: '16px', borderBottom: '1px solid #eef0f5'}}>
           <div style={{maxWidth: '600px'}}>
             <h4 style={{margin: '0 0 10px 0', fontSize: '13px', color: 'var(--ink)'}}>Assign recipients for {item.name}</h4>
             {recipients.length > 0 ? (
                 <div style={{display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '16px'}}>
                     {recipients.map(r => (
                         <label key={r.id} style={{display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '13px', margin: 0}}>
                            <input type="checkbox" checked={editShares.includes(r.id)} onChange={() => toggleRecipient(r.id, true)} style={{margin: 0, width: '16px', height: '16px', accentColor: 'var(--purple)'}} />
                            <div><b>{r.name}</b> <span style={{color: 'var(--muted)', fontSize: '12px'}}>({r.relationship})</span></div>
                         </label>
                     ))}
                 </div>
             ) : <p style={{fontSize: '13px', color: 'var(--muted)', margin: '0 0 16px 0'}}>You have not added any authorized recipients yet. Add them in the Recipients tab.</p>}
             <div className="button-row">
                <button className="button button-primary button-small" onClick={() => saveShares(item.id)} disabled={loading}>{loading ? 'Saving...' : 'Save assignments'}</button>
                <button className="button button-quiet button-small" onClick={() => setEditingSharesFor(null)}>Cancel</button>
             </div>
           </div>
        </td>
      </tr>}
      </Fragment>)}

      </tbody></table></div> : <div className="empty-state"><span className="empty-icon"><Icon name="file" size={25} /></span><b>{search || filter !== 'All files' ? 'No matching documents' : 'Your vault is ready for its first document'}</b><p>{search || filter !== 'All files' ? 'Try another search or collection.' : 'Add a policy, an important record, or anything you would like to keep safe.'}</p>{!data.length && <button className="text-link" onClick={() => setExpanded(true)}>Upload your first document <Icon name="arrow" size={15} /></button>}</div>}
      <div className="surface-footnote"><Icon name="lock" size={14} /> AES-256-GCM ENCRYPTED – PRIVATE TO YOUR ACCOUNT</div>
    </section>
  </div>
}
function Recipients({ data, token, onChange }) {
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState({ name: '', email: '', relationship: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [invitations, setInvitations] = useState([])

  const loadInvitations = useCallback(async () => {
    const response = await api('/invitations', { token })
    setInvitations(response.invitations || [])
  }, [token])

  useEffect(() => {
    loadInvitations().catch((requestError) => setError(requestError.message))
  }, [loadInvitations, data])

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
    <div className="notice notice-warm"><span className="notice-icon"><Icon name="users" size={17} /></span><span><b>Recipient access is approval-gated</b><small>Recipients create their own password from an invitation. They see only assigned documents, and downloads remain blocked until an authorized reviewer approves a nominee case and you approve the document release.</small></span></div>
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
        <div className="recipient-info"><Icon name="shield" size={16} /><span>Recipients have a separate portal and never gain access through invitation alone.</span></div>
      </section>
      <section className="surface recipient-list-surface">
        <div className="section-title-row"><div><span className="eyebrow">YOUR PEOPLE</span><h3>Trusted people</h3></div></div>
        {data.length ? <div className="recipient-list">{data.map((item) => <article className="recipient-item" key={item.id}>
          <span className="avatar recipient-avatar">{initials(item.name)}</span><span className="recipient-details"><b>{item.name}</b><small>{item.email}</small><span className="relationship-tag">{item.relationship}</span><InvitationControls role="recipient" targetId={item.id} token={token} invitations={invitations} onChange={loadInvitations} /></span>
          <span className="recipient-actions"><button className="icon-button" onClick={() => beginEdit(item)} aria-label={`Edit ${item.name}`}><Icon name="edit" size={16} /></button><button className="icon-button danger-icon" onClick={() => remove(item)} aria-label={`Remove ${item.name}`}><Icon name="trash" size={16} /></button></span>
        </article>)}</div> : <div className="empty-state compact-empty"><span className="empty-icon"><Icon name="users" size={23} /></span><b>Your circle starts here</b><p>Add a trusted contact to keep their details with your records.</p></div>}
      </section>
    </div>
    <section className="surface recipient-future"><span className="future-lock"><Icon name="lock" size={19} /></span><span><b>Invitation does not release documents</b><small>Each recipient sees only their assignments. A file remains blocked until the reviewer approves the case, you approve its release, and any Medical Hold has ended with a fresh successful owner check-in.</small></span></section>
  </div>
}

function InvitationControls({ role, targetId, token, invitations, onChange }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [claimUrl, setClaimUrl] = useState('')
  const [copied, setCopied] = useState(false)
  const latest = invitations.find((item) => item.role === role && item.target_id === targetId)
  const canRevoke = latest && ['sent', 'claimed'].includes(latest.status)

  async function send() {
    setBusy(true)
    setMessage('')
    setClaimUrl('')
    setCopied(false)
    try {
      const response = await api('/invitations', {
        token,
        method: 'POST',
        body: JSON.stringify({ role, target_id: targetId }),
      })
      const url = response.claim_url || response.invitation?.claim_url || ''
      setClaimUrl(url)
      setMessage(response.message || response.invitation?.email_note || 'Invitation ready. Copy the link below.')
      await onChange()
    } catch (requestError) {
      setMessage(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  async function revoke() {
    if (!latest) return
    setBusy(true)
    setMessage('')
    setClaimUrl('')
    try {
      await api(`/invitations/${latest.id}`, { token, method: 'DELETE' })
      setMessage('Invitation revoked.')
      await onChange()
    } catch (requestError) {
      setMessage(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  async function copyLink() {
    if (!claimUrl) return
    try {
      await navigator.clipboard.writeText(claimUrl)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2500)
    } catch {
      window.prompt('Copy this invitation link:', claimUrl)
    }
  }

  return (
    <span className="invitation-controls">
      {latest && <small>Invite: {latest.status}</small>}
      {canRevoke ? (
        <button type="button" className="text-button" onClick={revoke} disabled={busy}>
          {busy ? 'Please wait...' : 'Revoke access'}
        </button>
      ) : (
        <button type="button" className="text-button" onClick={send} disabled={busy}>
          {busy ? 'Creating...' : latest ? 'Send new invitation' : 'Invite to CloudGuard'}
        </button>
      )}
      {message && <small role="status">{message}</small>}
      {claimUrl && (
        <div className="invite-link-box" style={{ marginTop: '0.6rem', padding: '0.75rem', background: 'var(--surface-soft, #f6f5ff)', borderRadius: '10px', maxWidth: '100%' }}>
          <small style={{ display: 'block', marginBottom: '0.35rem', fontWeight: 600 }}>
            Invitation email sent. Share this link manually if needed.
          </small>
          <code style={{ display: 'block', wordBreak: 'break-all', fontSize: '12px', marginBottom: '0.5rem' }}>
            {claimUrl}
          </code>
          <button type="button" className="button button-primary" onClick={copyLink} style={{ fontSize: '13px', padding: '0.4rem 0.85rem' }}>
            {copied ? 'Copied!' : 'Copy invitation link'}
          </button>
        </div>
      )}
    </span>
  )
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
  const [invitations, setInvitations] = useState([])

  const loadInvitations = useCallback(async () => {
    const response = await api('/invitations', { token })
    setInvitations(response.invitations || [])
  }, [token])

  useEffect(() => {
    loadInvitations().catch((requestError) => setError(requestError.message))
  }, [loadInvitations, data?.id, data?.email])

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
    <div className="notice notice-warm"><span className="notice-icon"><Icon name="shield" size={17} /></span><span><b>Your nominee is separate from recipients</b><small>Choose one trusted contact and invite them to a nominee account. They can submit evidence and follow a case, but nominee status grants no document access.</small></span></div>
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
        {data ? <div className="nominee-preview">        <span className="avatar">{initials(data.name)}</span><span><b>{data.name}</b><small>{data.relationship} · {data.email}</small><InvitationControls role="nominee" targetId={data.id} token={token} invitations={invitations} onChange={loadInvitations} /></span></div> : <div className="nominee-preview nominee-empty"><Icon name="users" size={17} /><span>No nominee has been added yet.</span></div>}
      </section>
    </div>
    <section className="surface nominee-boundary"><Icon name="lock" size={17} /><span><b>Review and release stay separate</b><small>The nominee submits a death certificate for an independent reviewer. The nominee cannot approve their own case or access documents; a reviewer’s approval still requires your separate release approval.</small></span></section>
  </div>
}

function checkinResultCopy(result) {
  const faceFailed = result.face_status !== 'passed'
  const differentPerson = faceFailed && (result.face_detail === 'different_person' || result.face_detail === 'mismatch')
  const noFace = faceFailed && result.face_detail === 'no_face'
  if (result.status === 'success') {
    return {
      title: 'Check-in passed',
      summary: 'Face match, anti-spoof, and spoken phrase all passed.',
      faceLabel: 'Passed',
    }
  }
  return {
    title: differentPerson ? 'Check-in failed' : 'Check-in not passed',
    summary: differentPerson
      ? 'Face verification failed: this person does not match the enrolled face.'
      : noFace
        ? 'Face verification failed: no usable face was found in the check-in recording.'
        : 'At least one verification check did not pass. See the details below and try again.',
    faceLabel: differentPerson ? 'Different person' : 'Not passed',
    faceHint: differentPerson
      ? 'The live face does not match your enrollment photos. Check-in failed because this appears to be a different person.'
      : noFace
        ? 'Keep your face centred and well lit, then record again.'
        : 'Face not matched. Ensure good lighting and centre your face, matching your enrollment photos.',
  }
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
      setSeconds(12)
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
      const response = await api('/check-ins', { token, method: 'POST', body: form, timeoutMs: 120000 })
      setResult(response.checkin)
      recordingClip.current = null
      setStep('result')
      await onComplete(response.checkin)
    } catch (requestError) {
      setError(requestError.message || 'Check-in verification failed. Please try again.')
    }
    finally { setLoading(false) }
  }

  const resultCopy = result ? checkinResultCopy(result) : null

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
        {step === 'recorded' && <div className="phrase-panel"><span className="eyebrow">RECORDING COMPLETE</span><div className="checkin-privacy"><Icon name="shield" size={15} /><span>{hasClip ? 'Submit the clip to run face, liveness, and speech checks. The server deletes it after analysis.' : 'No recording data was captured. Please record again.'}</span></div><div className="button-row"><button className="button button-primary" onClick={saveCheckin} disabled={loading || !hasClip}>{loading ? 'Verifying… about a minute on first use' : 'Verify check-in'} <Icon name="arrow" size={17} /></button><button className="button button-quiet" onClick={() => { setStep('camera'); setResult(null); setHasClip(false); recordingClip.current = null }}>Try again</button></div></div>}
        {step === 'result' && result && resultCopy && <div className="result-panel"><span className="result-checkin-icon"><Icon name="shield" size={23} /></span><span className="eyebrow">CHECK-IN RESULT</span><h3>{resultCopy.title}</h3><p>{resultCopy.summary}</p><div className="overall-result"><span>Verification status</span><b>{result.status === 'success' ? 'PASSED' : 'FAILED'}</b></div><div className="result-checks"><div><span>Face verification</span><b className={result.face_status === 'passed' ? 'available-status' : 'unavailable-status'}>{resultCopy.faceLabel}</b></div><div><span>Anti-spoof liveness</span><b className={result.liveness_status === 'passed' ? 'available-status' : 'unavailable-status'}>{result.liveness_status === 'passed' ? 'Passed' : 'Not passed'}</b></div><div><span>Speech match</span><b className={result.speech_status === 'passed' ? 'available-status' : 'unavailable-status'}>{result.speech_status === 'passed' ? 'Passed' : 'Not passed'}</b></div></div><small className="result-time">Saved {formatDate(result.created_at, { dateStyle: 'medium', timeStyle: 'short' })}</small>{result.status !== 'success' && <div className="result-hints">{[['face_status', resultCopy.faceHint],['liveness_status','Liveness check failed. Use a live camera, not a photo or screen replay.'],['speech_status','Speech not matched. Speak the phrase clearly at a normal pace, close to the microphone.']].filter(([key]) => result[key] !== 'passed').map(([key, hint]) => <div key={key} className="result-hint-item"><Icon name="close" size={14} /><span>{hint}</span></div>)}</div>}<div className="button-row" style={{marginTop:'12px'}}><button className="button button-secondary" onClick={() => { stopCamera(); setResult(null) }}>Done</button>{result.status !== 'success' && <button className="button button-quiet" onClick={() => { setStep('camera'); setResult(null); setHasClip(false); recordingClip.current = null }}>Try again</button>}</div></div>}
        <video className={`checkin-video ${streamOn ? 'video-visible' : ''}`} ref={videoRef} muted playsInline />
      </section>
      <aside className="checkin-aside">
        <section className="surface aside-card"><span className="aside-icon"><Icon name="shield" size={19} /></span><span className="eyebrow">YOUR PRIVACY MATTERS</span><h3>Private verification</h3><p>The recording is analyzed by this server and deleted after processing. Only verification results are retained.</p><div className="aside-separator" /><span className="aside-detail"><Icon name="lock" size={15} /> All three checks are required to pass.</span></section>
        <section className="surface aside-card" style={{marginTop:'12px'}}><span className="aside-icon"><Icon name="scan" size={19} /></span><span className="eyebrow">FACE MATCHING</span><h3>Must match enrollment</h3><p>Your live face is compared to the 5 photos captured during registration. A different person will fail check-in.</p></section>
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
