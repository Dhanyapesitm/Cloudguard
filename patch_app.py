import re

with open('c:/Major_Project/src/App.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

start = content.find('function Documents({ data, token, onChange }) {')
end = content.find('function Recipients({ data, token, onChange }) {')
old_docs = content[start:end]

new_docs = """function Documents({ data, token, onChange }) {
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

  const [recipients, setRecipients] = useState([])
  const [selectedRecipients, setSelectedRecipients] = useState([])
  const [editingSharesFor, setEditingSharesFor] = useState(null)
  const [editShares, setEditShares] = useState([])

  useEffect(() => {
    api('/recipients', { token })
      .then(res => setRecipients(res.recipients || []))
      .catch(console.error)
  }, [token])

  function selectFile(file) {
    if (!file) return
    setSelectedFile(file)
    setDocumentName((currentName) => currentName.trim() ? currentName : file.name.replace(/\.[^.]+$/, ''))
    setMessage('')
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
    setMessage('')
    if (!selectedFile) { setMessage('Choose a file to upload.'); return }
    if (!documentName.trim()) { setMessage('Enter a display name for this document.'); return }
    const formData = new FormData()
    formData.set('name', documentName.trim())
    formData.set('category', category)
    formData.set('file', selectedFile, selectedFile.name)
    formData.set('recipient_ids_json', JSON.stringify(selectedRecipients))
    setLoading(true)
    try {
      const response = await api('/documents', { token, method: 'POST', body: formData })
      setMessage(`${response.document.name} has been encrypted and saved to your vault.`)
      setSelectedFile(null)
      setDocumentName('')
      setCategory('Personal')
      setSelectedRecipients([])
      if (inputRef.current) inputRef.current.value = ''
      setExpanded(false)
      await onChange()
    } catch (error) {
      setMessage(error.message)
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
      setMessage('Document assignments updated.')
      setEditingSharesFor(null)
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
    <div className="notice notice-warm"><span className="notice-icon"><Icon name="lock" size={17} /></span><span><b>Private, encrypted storage</b><small>Files are encrypted with AES-256-GCM before being saved. You can assign specific authorized recipients to access them if a verified death event occurs.</small></span></div>
    <section className="surface upload-surface">
      <div className="section-title-row"><div><span className="eyebrow">ADD TO YOUR VAULT</span><h3>Upload a document</h3></div><span className="soft-tag">{data.length} {data.length === 1 ? 'FILE' : 'FILES'}</span></div>
      {!expanded ? <div className="upload-prompt"><div className="upload-icon"><Icon name="plus" size={23} /></div><div><b>Keep an important file close</b><p>Upload a document, photo, audio clip or archive. Up to 25 MB per file.</p></div><button className="button button-primary" onClick={() => setExpanded(true)}>Choose a file <Icon name="arrow" size={16} /></button></div> :
      <form className="upload-form" onSubmit={submitUpload}>
        <div className="upload-drop" onClick={() => inputRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); selectFile(event.dataTransfer.files[0]) }}>
          <span className="upload-icon"><Icon name="file" size={23} /></span><b>{selectedFile ? selectedFile.name : 'Drop a file here or browse'}</b><small>{selectedFile ? formatBytes(selectedFile.size) : 'PDF, TXT, Office files, images, audio, video & archives – 25 MB max'}</small>
          <input ref={inputRef} type="file" hidden accept=".pdf,.txt,.rtf,.doc,.docx,.odt,.xls,.xlsx,.ods,.csv,.ppt,.pptx,.jpg,.jpeg,.png,.gif,.webp,.heic,.tif,.tiff,.bmp,.mp3,.wav,.m4a,.mp4,.mov,.avi,.zip,.7z,.rar,.json,.xml" onChange={(event) => selectFile(event.target.files[0])} />
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
            <small style={{display: 'block', marginTop: '6px', fontSize: '11px', color: 'var(--soft-muted)'}}>Recipients will only gain access after death verification.</small>
        </div>}

        {message && <div className={`notice ${message.includes('encrypted') || message.includes('deleted') || message.includes('updated') ? 'notice-success' : 'notice-error'}`}>{message}</div>}
        <div className="button-row"><button className="button button-primary" type="submit" disabled={loading}>{loading ? 'Encrypting...' : 'Encrypt and save'} <Icon name="lock" size={16} /></button><button className="button button-quiet" type="button" onClick={() => { setExpanded(false); setSelectedFile(null); setDocumentName(''); setCategory('Personal'); setMessage(''); setSelectedRecipients([]); if (inputRef.current) inputRef.current.value = '' }}>Cancel</button></div>
      </form>}
    </section>
    <section className="surface document-list-surface">
      <div className="section-title-row document-list-heading"><div><span className="eyebrow">YOUR COLLECTION</span><h3>Saved documents <span className="count-badge">{data.length}</span></h3></div><div className="list-controls"><div className="search-field"><Icon name="search" size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a file" aria-label="Search documents" /></div><select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter documents"><option>All files</option>{DOC_TYPES.map((type) => <option key={type}>{type}</option>)}</select></div></div>
      {message && !expanded && <div className="notice notice-success list-notice">{message}</div>}
      {visible.length ? <div className="document-table-wrap"><table className="data-table document-table"><thead><tr><th>DOCUMENT</th><th>COLLECTION</th><th>ASSIGNMENTS</th><th>FILE SIZE</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>
      
      {visible.map((item) => <React.Fragment key={item.id}>
      <tr>
        <td><span className="file-name-cell"><span className={`file-type-icon ${item.file_name.split('.').pop().toLowerCase() === 'pdf' ? 'pdf-icon' : ''}`}><Icon name="file" size={17} /></span><span><b>{item.name}</b><small>{item.file_name}</small></span></span></td>
        <td><span className="soft-tag tag-collection">{item.category}</span></td>
        <td>
          <div style={{display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap'}}>
            {item.recipient_ids && item.recipient_ids.length > 0 ? (
               item.recipient_ids.map(rid => {
                   const r = recipients.find(x => x.id === rid)
                   return <span key={rid} style={{background: '#e0def5', color: '#4843bd', fontSize: '10px', padding: '2px 6px', borderRadius: '4px', fontWeight: 'bold'}}>{r ? r.name : 'Unknown'}</span>
               })
            ) : <span style={{color: 'var(--soft-muted)', fontSize: '11px'}}>Unassigned</span>}
          </div>
        </td>
        <td>{formatBytes(item.file_size)}</td>
        <td><div className="table-actions">
          <button className="icon-button" onClick={() => { setEditingSharesFor(editingSharesFor === item.id ? null : item.id); setEditShares(item.recipient_ids || []); setMessage('') }} aria-label={`Assign ${item.name}`} title="Assign Recipients"><Icon name="user" size={17} /></button>
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
      </React.Fragment>)}
      
      </tbody></table></div> : <div className="empty-state"><span className="empty-icon"><Icon name="file" size={25} /></span><b>{search || filter !== 'All files' ? 'No matching documents' : 'Your vault is ready for its first document'}</b><p>{search || filter !== 'All files' ? 'Try another search or collection.' : 'Add a policy, an important record, or anything you would like to keep safe.'}</p>{!data.length && <button className="text-link" onClick={() => setExpanded(true)}>Upload your first document <Icon name="arrow" size={15} /></button>}</div>}
      <div className="surface-footnote"><Icon name="lock" size={14} /> AES-256-GCM ENCRYPTED – PRIVATE TO YOUR ACCOUNT</div>
    </section>
  </div>
}
"""

content = content.replace(old_docs, new_docs)
with open('c:/Major_Project/src/App.jsx', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done App.jsx")
