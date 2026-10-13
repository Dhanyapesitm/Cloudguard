"""
Apply no-Resend invitation fix to local CloudGuard project.
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "backend" / "main.py"
APP = ROOT / "src" / "App.jsx"

NEW_CREATE_INVITATION = '''@app.post("/api/invitations", status_code=201)
def create_invitation(
    payload: CreateInvitationPayload,
    user: sqlite3.Row = Depends(current_user),
) -> dict:
    """Create an invitation. Email (Resend) is optional.

    Always returns a claim_url the owner can copy and share manually
    (WhatsApp, SMS, etc.). If Resend is configured it is attempted, but
    failure no longer blocks the invitation.
    """
    invitation_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(invitation_token.encode("ascii")).hexdigest()
    now = utc_now()
    expires_at = now + timedelta(days=7)

    config, missing = invitation_email_configuration()
    app_url = (config.get("CLOUDGUARD_APP_URL") or "http://localhost:5173").rstrip("/")
    claim_url = f"{app_url}/#guest-invitation={invitation_token}"

    with db() as connection:
        target = invitation_target(connection, user["id"], payload.role, payload.target_id)
        email = target["email"].strip().lower()
        revoke_target_invitations(connection, user["id"], payload.role, payload.target_id)
        cursor = connection.execute(
            """INSERT INTO invitations(owner_id,email,role,target_id,token_hash,status,created_at,expires_at)
               VALUES(?,?,?,?,?,'pending',?,?)""",
            (
                user["id"],
                email,
                payload.role,
                payload.target_id,
                token_hash,
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
        invitation_id = cursor.lastrowid
        audit(connection, user["id"], f"invitation.created:{invitation_id}:{payload.role}")

    email_delivery = "skipped"
    email_note = "Share the claim link below with the invitee. No email provider is required."

    if not missing:
        try:
            send_invitation_email(email, payload.role, invitation_token)
            email_delivery = "sent"
            email_note = "Invitation email was also sent via Resend."
        except Exception as error:
            email_delivery = "failed"
            if isinstance(error, ResendError):
                safe_detail = sanitized_resend_error(
                    error,
                    [
                        invitation_token,
                        email,
                        config.get("RESEND_API_KEY", ""),
                    ],
                )
                email_note = (
                    f"Email could not be sent ({safe_detail}). "
                    "Use the claim link below instead."
                )
                logger.warning("Invitation %s email failed: %s", invitation_id, safe_detail)
            else:
                email_note = (
                    f"Email could not be sent ({type(error).__name__}). "
                    "Use the claim link below instead."
                )
                logger.warning(
                    "Invitation %s email failed (%s).", invitation_id, type(error).__name__
                )
    else:
        email_note = (
            "Email is not configured (optional). "
            "Copy the claim link and share it with the invitee."
        )

    with db() as connection:
        cursor = connection.execute(
            """UPDATE invitations SET status='sent',sent_at=?
               WHERE id=? AND status='pending' AND expires_at>?""",
            (utc_now().isoformat(), invitation_id, utc_now().isoformat()),
        )
        if cursor.rowcount != 1:
            raise HTTPException(
                status_code=503,
                detail="The invitation could not be activated. Please try again.",
            )
        audit(
            connection,
            user["id"],
            f"invitation.sent:{invitation_id}:{payload.role}:{email_delivery}",
        )

    return {
        "invitation": {
            "id": invitation_id,
            "email": email,
            "role": payload.role,
            "status": "sent",
            "expires_at": expires_at.isoformat(),
            "claim_url": claim_url,
            "email_delivery": email_delivery,
            "email_note": email_note,
        },
        "claim_url": claim_url,
        "message": (
            "Invitation is ready. Copy the claim link and send it to the person "
            "(email is optional and was not required)."
        ),
    }
'''

NEW_INVITATION_CONTROLS = '''function InvitationControls({ role, targetId, token, invitations, onChange }) {
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
            Share this link with the invitee (no email needed)
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
'''

def patch_main(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "Email (Resend) is optional" in text and '"claim_url": claim_url' in text:
        print(f"[ok] {path} already has the no-Resend invitation code.")
        return
    pattern = re.compile(
        r'@app\.post\("/api/invitations", status_code=201\)\s*\n'
        r'def create_invitation\([\s\S]*?\n'
        r'(?=@app\.get\("/api/invitations"\))',
        re.MULTILINE,
    )
    if not pattern.search(text):
        raise SystemExit(f"Could not match create_invitation in {path}")
    text = pattern.sub(NEW_CREATE_INVITATION.rstrip() + "\n\n", text)
    path.write_text(text, encoding="utf-8")
    print(f"[updated] {path}")

def patch_app(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "Copy invitation link" in text and "claimUrl" in text:
        print(f"[ok] {path} already has the claim-link UI.")
        return
    pattern = re.compile(
        r'function InvitationControls\(\{ role, targetId, token, invitations, onChange \}\) \{[\s\S]*?\n\}'
        r'(?=\n\nfunction )',
        re.MULTILINE,
    )
    if not pattern.search(text):
        pattern = re.compile(
            r'function InvitationControls\([\s\S]*?\n\}'
            r'(?=\n\nfunction Nominee\b|\n\nfunction Recipients\b|\n\nfunction )',
            re.MULTILINE,
        )
    if not pattern.search(text):
        raise SystemExit(f"Could not match InvitationControls in {path}")
    text = pattern.sub(NEW_INVITATION_CONTROLS.rstrip() + "\n", text)
    path.write_text(text, encoding="utf-8")
    print(f"[updated] {path}")

def main() -> None:
    print(f"Project root: {ROOT}")
    if not MAIN.exists():
        raise SystemExit(f"Missing {MAIN}")
    if not APP.exists():
        raise SystemExit(f"Missing {APP}")
    patch_main(MAIN)
    patch_app(APP)
    print("Done. Restart backend + frontend, then Ctrl+F5 in the browser.")

if __name__ == "__main__":
    main()
