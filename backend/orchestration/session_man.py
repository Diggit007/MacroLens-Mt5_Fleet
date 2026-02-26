import os
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("SessionMan")

class SessionManager:
    """
    Manages the 'Virtual Session' for local MT5 execution.
    Since we are running locally (No Docker), this class primarily acts as a 
    Configuration and Path Resolver for where files should be written.
    """
    
    def __init__(self):
        # Default internal folder (Relative to backend root)
        self.internal_shared_root = Path(__file__).parent.parent / "mt5_shared"
        self.internal_shared_root.mkdir(parents=True, exist_ok=True)
        
        # Load custom path from Env (e.g., "C:/Users/.../MQL5/Files")
        self.custom_mt5_path = os.getenv("LOCAL_MT5_PATH")
        if self.custom_mt5_path:
            self.custom_mt5_path = Path(self.custom_mt5_path)
            if not self.custom_mt5_path.exists():
                logger.warning(f"LOCAL_MT5_PATH is set but does not exist: {self.custom_mt5_path}")

    def get_session_folder(self, user_id: str = "default") -> Path:
        """
        Determines where to write files for this user.
        In Local Mode, we usually map User ID to the same single local terminal,
        or we could use subfolders in the local terminal if the EA supports it.
        For MVP, we write to the root of the MQL5/Files folder.
        """
        # --- VPS FIX: Force correct path if it exists ---
        # REMOVED for Multi-User Support:
        # We want dynamic folders (user_123, user_456) so different terminals can watch them.
        # vps_root = Path(r"C:\MacroLens\mt5_shared\user_default")
        # if vps_root.exists():
        #     return vps_root
        # ------------------------------------------------

        if self.custom_mt5_path:
            # Assume custom_path is the ROOT shared folder (e.g. C:/MacroLens/mt5_shared)
            # We append user_{id} to separate users
            user_folder = self.custom_mt5_path / f"user_{user_id}"
            if not user_folder.exists():
                try:
                    user_folder.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Created new session folder: {user_folder}")
                except Exception as e:
                    logger.error(f"Failed to create session folder {user_folder}: {e}")
            return user_folder
        
        # Fallback to internal folder
        folder = self.internal_shared_root / f"user_{user_id}"
        folder.mkdir(parents=True, exist_ok=True)
        # Log the path once to help debug
        logger.info(f"Resolved Session Folder for {user_id}: {folder.absolute()}")
        return folder

    async def start_session(self, user_id: str, *args):
        """
        No-op in Local Mode, but checks paths.
        """
        folder = self.get_session_folder(user_id)
        logger.info(f"Session Active. Using path: {folder}")
        return {"status": "active", "mode": "local", "path": str(folder)}

    async def get_command_file_path(self, user_id: str) -> Path:
        return self.get_session_folder(user_id) / "command.json"

    async def get_response_file_path(self, user_id: str) -> Path:
        return self.get_session_folder(user_id) / "response.json"
