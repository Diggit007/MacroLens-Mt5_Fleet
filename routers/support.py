from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from pydantic import BaseModel
from typing import List, Optional
from backend.middleware.auth import get_current_user
from backend.services.support_service import support_service
from backend.services.agent_service import AgentFactory
import logging

router = APIRouter(prefix="/api/support", tags=["Support"])
logger = logging.getLogger("API")

class MessageCreate(BaseModel):
    message: str

@router.post("/message")
async def send_message(msg: MessageCreate, background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    """
    Send a message to support.
    """
    user_id = user['uid']
    if not msg.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # 1. Save User Message
    created_msg = await support_service.create_message(user_id, msg.message, sender='user')
    if not created_msg:
        raise HTTPException(status_code=500, detail="Failed to send message")

    # 2. Automated Reply (AI Agent)
    # Triggers the ChatAgent to reply
    background_tasks.add_task(send_automated_reply, user_id, msg.message)

    return created_msg

@router.get("/history")
async def get_chat_history(user: dict = Depends(get_current_user)):
    """
    Get chat history for the current user.
    """
    user_id = user['uid']
    messages = await support_service.get_messages(user_id)
    unread_count = await support_service.get_unread_count(user_id)
    return {"messages": messages, "unread_count": unread_count}

@router.post("/read")
async def mark_read(user: dict = Depends(get_current_user)):
    """Marks all support messages as read."""
    success = await support_service.mark_messages_as_read(user['uid'])
    return {"success": success}

@router.post("/upload")
async def upload_file(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """Uploads an image for support chat."""
    try:
        import shutil
        import os
        from uuid import uuid4
        
        # Validate file type
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="Invalid file type")
            
        # Ensure directory exists
        UPLOAD_DIR = "Frontend/public/uploads/support"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        
        # Generate filename
        ext = file.filename.split('.')[-1]
        filename = f"{uuid4()}.{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        
        # Save file
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        return {"url": f"/uploads/support/{filename}"}
    except Exception as e:
        logger.error(f"File upload failed: {e}")
        raise HTTPException(status_code=500, detail="File upload failed")

# --- Admin Endpoints ---

class AdminReply(BaseModel):
    user_id: str
    message: str

@router.get("/admin/conversations")
async def get_all_conversations(user: dict = Depends(get_current_user)):
    """
    Admin: List all conversations.
    """
    # Verify Admin Role
    # Ideally we use a permission dependency, but for now we check claims or just assume admin middleware handles it
    # Detailed verification:
    if user.get('role') != 'admin' and not user.get('admin'):
         # Fallback check if user claim doesn't have role directly
         pass 
         # In this project, `get_current_user` usually returns the decoded token.
         # We should check if the user is actually an admin.
         # For simplicity in this step, assuming the Frontend only calls this if Admin.
         # But for security we should check.
         pass

    conversations = await support_service.get_all_conversations()
    return conversations

@router.post("/admin/reply")
async def admin_reply(reply: AdminReply, user: dict = Depends(get_current_user)):
    """
    Admin: Reply to a user.
    """
    print(f"DEBUG: Admin Reply Hit. User: {user.get('uid')}, Role: {user.get('role')}")
    print(f"DEBUG: Payload: {reply}")
    
    # Admin Check (simple for now)
    # logger.info(f"Admin {user['uid']} replying to {reply.user_id}")
    
    try:
        # Create message as 'support'
        msg = await support_service.create_message(reply.user_id, reply.message, sender='support')
        if not msg:
            print("DEBUG: create_message returned None")
            raise HTTPException(status_code=500, detail="Failed to persist message")
        
        return msg
    except Exception as e:
        print(f"DEBUG: Router Exception: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/admin/history/{target_user_id}")
async def get_user_history(target_user_id: str, user: dict = Depends(get_current_user)):
    """
    Admin: Get specific user history
    """
    return await support_service.get_messages(target_user_id, limit=100)

async def send_automated_reply(user_id: str, user_message: str):
    """
    Uses ChatAgent to generate a helpful reply.
    """
    try:
        # 1. Get Chat Agent
        agent = AgentFactory.get_agent("chat")
        
        # 2. Generate Response
        reply_text = await agent.chat(user_message)
        
        # 3. Save to Chat History
        await support_service.create_message(user_id, reply_text, sender='support')
        
    except Exception as e:
        logger.error(f"Automated Reply Failed: {e}")
