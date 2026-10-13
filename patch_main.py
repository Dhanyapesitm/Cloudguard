import json

def patch():
    with open('c:/Major_Project/backend/main.py', 'r', encoding='utf-8') as f:
        content = f.read()
        
    # 1. Update init_database
    old_init = '''CREATE TABLE IF NOT EXISTS recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                relationship TEXT NOT NULL,
                created_at TEXT NOT NULL
            );'''
    new_init = old_init + '''
            CREATE TABLE IF NOT EXISTS document_shares (
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (document_id, recipient_id)
            );'''
    content = content.replace(old_init, new_init)
    
    # 2. Update list_documents
    old_list_docs = '''@app.get("/api/documents")
def list_documents(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        rows = connection.execute(
            """SELECT id,name,category,file_name,content_type,file_size,created_at
               FROM documents WHERE owner_id=? ORDER BY id DESC""",
            (user["id"],),
        ).fetchall()
    return {"documents": [dict(row) for row in rows]}'''
    
    new_list_docs = '''@app.get("/api/documents")
def list_documents(user: sqlite3.Row = Depends(current_user)) -> dict:
    with db() as connection:
        rows = connection.execute(
            """SELECT id,name,category,file_name,content_type,file_size,created_at
               FROM documents WHERE owner_id=? ORDER BY id DESC""",
            (user["id"],),
        ).fetchall()
        
        shares = connection.execute(
            """SELECT document_shares.document_id, document_shares.recipient_id 
               FROM document_shares 
               JOIN documents ON document_shares.document_id = documents.id
               WHERE documents.owner_id=?""",
            (user["id"],)
        ).fetchall()
        
    shares_by_doc = {}
    for row in shares:
        shares_by_doc.setdefault(row["document_id"], []).append(row["recipient_id"])
        
    docs = []
    for row in rows:
        d = dict(row)
        d["recipient_ids"] = shares_by_doc.get(row["id"], [])
        docs.append(d)
        
    return {"documents": docs}'''
    content = content.replace(old_list_docs, new_list_docs)
    
    # 3. Update upload_document
    old_upload_def = '''async def upload_document(
    name: str = Form(..., min_length=2, max_length=120),
    category: str = Form(..., min_length=2, max_length=40),
    file: UploadFile = File(...),
    user: sqlite3.Row = Depends(current_user),
) -> dict:'''
    new_upload_def = '''async def upload_document(
    name: str = Form(..., min_length=2, max_length=120),
    category: str = Form(..., min_length=2, max_length=40),
    recipient_ids_json: str = Form(default="[]"),
    file: UploadFile = File(...),
    user: sqlite3.Row = Depends(current_user),
) -> dict:'''
    content = content.replace(old_upload_def, new_upload_def)
        
    old_upload_insert = '''cursor = connection.execute(
            """INSERT INTO documents(owner_id,name,category,file_name,content_type,file_size,encrypted_data,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (user["id"], name.strip(), category, Path(file.filename or "document").name,
             content_type, len(data), encrypt_bytes(data), utc_now().isoformat())
        )
        audit(connection, user["id"], f"document.uploaded:{cursor.lastrowid}")
    return {"message": "Document uploaded successfully."}'''
    
    new_upload_insert = '''cursor = connection.execute(
            """INSERT INTO documents(owner_id,name,category,file_name,content_type,file_size,encrypted_data,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (user["id"], name.strip(), category, Path(file.filename or "document").name,
             content_type, len(data), encrypt_bytes(data), utc_now().isoformat())
        )
        doc_id = cursor.lastrowid
        
        import json
        try:
            r_ids = json.loads(recipient_ids_json)
        except:
            r_ids = []
            
        if r_ids:
            placeholders = ",".join("?" for _ in r_ids)
            valid_recs = connection.execute(
                f"SELECT id FROM recipients WHERE owner_id=? AND id IN ({placeholders})",
                (user["id"], *r_ids)
            ).fetchall()
            valid_rec_ids = {r["id"] for r in valid_recs}
            
            now = utc_now().isoformat()
            for rid in set(r_ids):
                if rid in valid_rec_ids:
                    connection.execute(
                        "INSERT INTO document_shares(document_id, recipient_id, created_at) VALUES(?,?,?)",
                        (doc_id, rid, now)
                    )
                    
        audit(connection, user["id"], f"document.uploaded:{doc_id}")
    return {"message": "Document uploaded successfully."}'''
    content = content.replace(old_upload_insert, new_upload_insert)
    
    # 4. Add update_document_shares endpoint just before download_document
    new_update_shares = '''
class UpdateSharesPayload(BaseModel):
    recipient_ids: list[int]

@app.put("/api/documents/{document_id}/shares")
def update_document_shares(
    document_id: int, payload: UpdateSharesPayload, user: sqlite3.Row = Depends(current_user)
) -> dict:
    with db() as connection:
        doc = connection.execute("SELECT id FROM documents WHERE id=? AND owner_id=?", (document_id, user["id"])).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found.")
            
        if payload.recipient_ids:
            placeholders = ",".join("?" for _ in payload.recipient_ids)
            valid_recs = connection.execute(
                f"SELECT id FROM recipients WHERE owner_id=? AND id IN ({placeholders})",
                (user["id"], *payload.recipient_ids)
            ).fetchall()
            valid_rec_ids = {r["id"] for r in valid_recs}
            if len(valid_rec_ids) != len(payload.recipient_ids):
                raise HTTPException(status_code=400, detail="Invalid recipient selected.")
                
        connection.execute("DELETE FROM document_shares WHERE document_id=?", (document_id,))
        now = utc_now().isoformat()
        for rid in set(payload.recipient_ids):
            connection.execute(
                "INSERT INTO document_shares(document_id, recipient_id, created_at) VALUES(?,?,?)",
                (document_id, rid, now)
            )
        audit(connection, user["id"], f"document.shares_updated:{document_id}")
        
    return {"message": "Document assignments updated."}

@app.get("/api/documents/{document_id}/download")'''
    
    content = content.replace('@app.get("/api/documents/{document_id}/download")', new_update_shares)
    
    with open('c:/Major_Project/backend/main.py', 'w', encoding='utf-8') as f:
        f.write(content)

patch()
